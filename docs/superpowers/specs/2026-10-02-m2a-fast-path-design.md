# M2a — Deterministic Fast Path: Design Spec

- **Status:** design approved section by section on 2026-10-02; this document consolidates it for implementation.
- **Decision record:** Confluence *Data Import Agent — Design Decisions* §8.1–8.13 (page 3774676996). `§` references below point there.
- **Scope:** milestone M2a. M2b (confirm-and-bind onboarding + agent) gets its own spec.

## 1. Goal

Build the deterministic core of the importer. A file whose signature is registered goes `discovered → detected → landed → promoted → completed` and lands every landable record in the raw zone, without any model involved. Rows that cannot land go to the reject repository with a reason code. A file whose signature is unknown gets profiled and matched against known schemas, and is then quarantined with a "looks like…" proposal. That proposal is what M2b's confirm-and-bind step acts on.

The guiding principle is unchanged (§0). The importer measures **landability** only. It reports `percent_parsed` / `percent_loaded` (a number, not a verdict), and it never cleans, coerces or validates semantically.

## 2. Scope

**In M2a**
- Readers for all six formats: delimited (CSV/TSV/pipe/semicolon), Excel (`.xlsx`, `.xls`), JSON (flat array of objects), NDJSON and XML.
- Signature computation and an exact-match registry lookup.
- The Mapper interface and its three tiers: declarative spec, spec + named shapers, and plugin class. M2a ships two shapers (`excel.anchor_header`, `excel.crosstab_unpivot`) and the plugin loading mechanism; it ships no plugins.
- Raw and stage tables generated from versioned schema definitions, plus a one-off command that imports a schema definition from an existing destination table.
- The loader: Python-side row triage, batched staging, bisection fallback, and transactional promote.
- Ledger, registry, artifact and reject repositories, each with an in-memory implementation and an MSSQL implementation.
- A deterministic miss profiler and matcher that writes the proposal, a quarantine row and a decision-log row.
- Re-entry transitions and a retry command.
- A CLI: `process`, `preview`, `retry`, `schema import`, and `db rebuild` (DEV only).
- Updates to the control-plane DDL and to the CLAUDE.md / README layout notes.

**Not in M2a** (later milestones)
- M2b: confirm-and-bind (the attended prompt and the review command) and the LLM agent for fuzzy or new mappings.
- Reclaim DB binding (M6), the trigger source, catalog validation of declared parameters, and the downstream handoff.
- Real-file plugins for participation and network.

## 3. Architecture

```
caller ──process(path, project, cls, file_type, source, context)──► pipeline
  pipeline ─► ledger (discovered; SHA-256 idempotency key)
           ─► readers.open_document (declared-vs-actual check) ──✗──► quarantined(corrupt|unparseable)
           ─► fingerprint ─► registry.lookup ──miss──► profiler+matcher ─► proposal ─► quarantined(unroutable)
           ─► mapper (spec | spec+shapers | plugin) ─► LandRow / RejectRow stream
           ─► precheck ─► loader.stage (batches, bisection) ─► ledger landed
           ─► loader.promote (one txn: raw INSERT…WHERE NOT EXISTS, rejects, clear stage) ─► ledger promoted
           ─► metrics (DB counts, conservation check, monotonic floor) ─► ledger completed
```

Control flow is driven by the ledger. A status changes only through `LedgerRepository.transition`, which is guarded by `can_transition` plus `WHERE status = src`. No model is involved anywhere in this path (§1, §8.3).

### Module layout

| Module | Responsibility |
|---|---|
| `config.py` | Loads `.env` settings (see §11) |
| `db.py` | The **only** module that imports DataEngine, and only lazily. It loads the repo-root `database.env` connection document, builds the `importer` connection, and returns its SQLAlchemy `.engine` (`mssql+pyodbc`, `fast_executemany=True`) |
| `models.py` | `FileStatus` and transitions (re-entry added), `LandRow`, `RejectRow`, `SignatureHit`, `Signature`, `FileResult`, `Reject` |
| `readers/` | `sniff.py` (declared-vs-actual check), `base.py` (`Document`, `TabularDocument`, `XmlDocument`, `Table`), `delimited.py`, `excel.py`, `json_reader.py`, `ndjson.py`, `xml_reader.py`, `__init__.open_document` |
| `fingerprint.py` | `fingerprint(doc, project, cls, file_type) -> Signature` (stub implemented) |
| `detector.py` | `detect(doc, declared, registry) -> SignatureHit | None` (stub implemented) |
| `registry.py`, `artifacts.py`, `ledger.py`, `reject_store.py` | Each is a Protocol plus an in-memory implementation plus an MSSQL implementation |
| `mappers/base.py` | `Mapper` ABC (new contract) |
| `mappers/spec.py` | `DeclarativeMapper` and spec validation |
| `mappers/loading.py` | Resolves an artifact version to a Mapper; checks plugin hashes |
| `mappers/plugins/` | Folder for human-saved plugin modules (empty in M2a) |
| `shapers/` | Shaper registry; `excel.py` holds `anchor_header` and `crosstab_unpivot` |
| `profiler.py` | Observed-schema profile and candidate matching |
| `precheck.py` | Per-row landability pre-check |
| `schema_tables.py` | Raw/stage DDL generation from a schema definition; `INFORMATION_SCHEMA` import |
| `loader.py` | Stage (batching, bisection) and promote |
| `pipeline.py` | `process()`, the state machine |
| `cli.py` | `importer` console entry point |

## 4. Request parameters (§8.2)

- `project`, `cls` (PC | PD | Other…) and `file_type` are required. Each is uppercased and must match `^[A-Za-z0-9_-]+$` (so it can never contain `|`).
- Recognised `file_type` values: `CSV`, `TSV`, `PIPE`, `DELIMITED`, `EXCEL`, `JSON`, `NDJSON`, `XML`. All the delimited variants use the delimited reader.
- `source` is required (stored on the ledger). `context` is an optional dict stored as JSON in `file_ledger.request_context` and readable by specs as `$context.<key>`.
- There is no catalog validation in M2a.

## 5. Readers and signatures (§8.6)

### Declared-vs-actual check (`readers/sniff.py`)
| Declared | Must hold |
|---|---|
| XML | first non-whitespace byte is `<` and lxml parses the file |
| EXCEL | `.xlsx` starts with `PK` (ZIP); `.xls` starts with `D0 CF 11 E0` (OLE2) |
| JSON | first non-whitespace char is `[` or `{`, and the file parses |
| NDJSON | each non-empty line starts with `{` (per-line parse errors become row rejects) |
| delimited | decodes as text; delimiter sniffed from `, \t | ;` (declared `TSV`/`PIPE` fixes it) |

A mismatch means `quarantined(unparseable)`. A file that can't be opened or parsed as a whole means `quarantined(corrupt)`.

**Encoding** (text formats): UTF-8, with a BOM if present, falling back to cp1252. The encoding used is recorded on the ledger.

**Column-name normalization:** `re.sub(r'[^a-z0-9]', '', name.lower())`, matching the legacy parsers.

### Physical part of the signature
`signature = f"{PROJECT}|{CLASS}|{FILE_TYPE}|{physical}"`, matched exactly.

| Format | Physical part |
|---|---|
| XML | the root element's local name, e.g. `prescriptionDrugTemplateVO`. The namespace URI is stored in `signature_registry.xml_namespace` and must also match for a hit |
| Delimited | `h:` + the first 16 hex characters of SHA-256 over the ordered normalized header joined by `\x1f` |
| Excel | `h:` + 16 hex of SHA-256 over the sorted list of `(normalized sheet name, ordered normalized row-1 header)` for every non-empty sheet |
| JSON / NDJSON | `h:` + 16 hex of SHA-256 over the sorted union of keys across all records (nested keys dot-flattened) |

The full observed header or key set is stored on the registry row, so people can read it.

### Row identity and reader-level rejects
| Format | `row_identity` | Reader reject |
|---|---|---|
| Delimited | `L<physical line no>` | `field_count_mismatch` |
| Excel | `<sheet>!R<row no>` | — |
| JSON | `[<index>]` | — |
| NDJSON | `L<line no>` | `invalid_json_line` |
| XML | namespace-free element path, e.g. `/formularyList/formularyVO[3]/…/costSharingTypeVO[2]`; if longer than 256 characters, an ordinal path (`1.3.2.1`) | — |

> **Excel preparation note (§8.6):** when a template's layout defeats exact signatures, a small amount of content preparation by the requestor is often worth it, e.g. moving the header to row 1, keeping sheet names stable, or adding a marker cell. The importer never edits submitted files. Shapers absorb layout quirks that can't be fixed by preparation.

## 6. Mapping (§8.5, §8.9)

### Interface
```python
class Mapper(ABC):
    def extract(self, doc: Document, ctx: RunContext) -> Iterator[LandRow | RejectRow]: ...

@dataclass(frozen=True)
class LandRow:  target: str; row_identity: str; fields: dict[str, str | None]; extras: dict | None
@dataclass(frozen=True)
class RejectRow: target: str | None; row_identity: str; raw_payload: str; reason_code: str; detail: str | None
```
`RunContext` carries `file_id`, `signature_id`, `artifact_version_id`, `request_context`, and the target schema definitions (column sets). `MappedRow` and the old `map_row(line)` contract are removed. `RepairRule`, `reject_store` and `reclaim` keep their contracts.

### Declarative spec (stored as JSON in `artifact_versions.definition`)
Top-level keys:
- `namespaces`: map of prefix to URI (XML only).
- `value_child`: optional XPath step, e.g. `x:cellValue`. When a field resolves to an element that has this child, the child's text is used.
- `shapers`: optional ordered list of `{name, options}`.
- `extractions`: a list. Each entry has:
  - `target`: the schema id.
  - `records`: an XPath (XML), a sheet name (Excel), or `*` (single-table formats).
  - `fields`: map of target column to source.
  - `auto`: tabular only; maps identical normalized names.
  - `unpivot`: optional `{over, name_column, value_column}`.
  - `unmapped`: `extras` (the default) or `ignore`.

Field sources:
- An XPath relative to the record (`../` reaches ancestors, an absolute path reads header context), or a normalized column name for tabular formats.
- `"*": "x:*"` maps every child element to a column of the same local name.
- Special sources: `$file_id`, `$row_identity`, `$parent(<target>)` (the row identity of the nearest ancestor record that belongs to extraction `<target>`), and `$context.<key>`.

Rules:
- Values land exactly as written. There is no trimming.
- A source that matches nothing gives `NULL`.
- A source that matches several nodes lands the first value and puts the full list in `_extras[<column>]`.
- A field the target schema doesn't define goes to `_extras` (or is dropped if `unmapped: ignore`).
- Unpivot emits one row per matched child, with `row_identity` suffixed `#<name>`.
- A mapper-level exception on a single record becomes `RejectRow(reason_code='mapping_error')`.

Spec validation runs when the spec is loaded: it checks the required keys, that every XPath compiles, that every `target` has an active schema definition, and that every shaper name is registered. An invalid spec means the file is `failed` (configuration error).

### Shapers
`@shaper("excel.anchor_header")` registers a function `(doc, **options) -> TabularDocument`. M2a ships:
- `excel.anchor_header(sheet, anchor, search_rows=4, search_cols=14)`: finds the anchor cell (comparison ignores case and spaces) and treats the rows above it as header rows, flattened with `_`.
- `excel.crosstab_unpivot(sheet, id_columns, header_rows)`: melts benefit columns into `(benefit_name, value)` rows.

Both are ported from the **structural** steps of `parseBenefitExcel` only. The legacy dictionary join, value cleaning and `|` split are downstream concerns.

### Plugins
A plugin is a `Mapper` subclass in `src/importer/mappers/plugins/<module>.py` with a `version` class attribute. The artifact definition is `{"kind":"plugin","ref":"<module>:<Class>","version":N,"sha256":"<file hash>"}`. `mappers/loading.py` imports the module only when an active artifact references it, and refuses to run (file `failed`, reason `plugin_hash_mismatch`) if the file's current SHA-256 differs from the recorded one. The agent may draft plugins as proposals; only a human commit makes one live (§5 hard boundary).

### Preview
`preview(mapper, path, declared, n=20) -> PreviewResult` gives per-target row counts, the first `n` rows, the `_extras` keys seen, and reject counts by reason. It needs no database writes.

## 7. Miss handling: profiler and matcher (§8.4)

- **Profile:** tabular files give `{sheet: [normalized columns]}`; XML gives root, namespace, and the distinct element paths with counts; JSON gives the sorted key set.
- **Candidates**, ranked:
  1. Active signatures with the same `physical_part` (and the same `xml_namespace` for XML) under any project or class. Result: "reuse mapper `<version_id>`", score 1.0.
  2. Tabular only: Jaccard similarity between each observed table's columns and every active schema definition's canonical columns. Each candidate lists matched / missing / extra columns.

  Keep the top 5 with a score of at least 0.5.
- **Recording:** upsert a `proposed` registry row keyed by the signature, holding the observed schema and the candidates. Insert a `file_quarantine` row (`unroutable`, `signature_id`) and a `decision_log` row (`action_type='profile_match'`, `applied=0`). The ledger moves to `quarantined`.
- `FileResult.proposal_summary` is the best candidate as one line, e.g. `looks like url_template (0.92; extra: newurl; missing: url)`.

## 8. Raw zone, loader and row triage (§8.7, §8.10)

### Schema definitions and tables
- The `artifact_versions` type `schema` has `artifact_key = schema_id` and `definition = {"columns": ["col", …]}`. It is versioned and never edited in place.
- `importer schema import <db>.<schema>.<table> [--as <schema_id>]` reads `INFORMATION_SCHEMA.COLUMNS` and writes v1. Columns are kept in ordinal order, with names exactly as found.
- `schema_tables.ensure(schema_def)` creates or extends:
  - `raw.<schema_id>`: the canonical columns, each `NVARCHAR(MAX) NULL`, plus:
    - `_file_id BIGINT NOT NULL`
    - `_row_identity NVARCHAR(256) NOT NULL`
    - `_signature_id BIGINT NOT NULL`
    - `_artifact_version_id BIGINT NOT NULL`
    - `_extras NVARCHAR(MAX) NULL`
    - `_landed_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()`
    - a clustered PK on `(_file_id, _row_identity)`.
  - `stage.<schema_id>`: the same columns as a heap with no constraints.
- A new schema version runs `ALTER TABLE … ADD <col> NVARCHAR(MAX) NULL` on both tables. Columns are never dropped.
- Identifiers must match `^[A-Za-z0-9_]+$` and are bracket-quoted. Values are always bound parameters.

### Pre-check (`precheck.py`, per row, before staging)
Each failed check turns the row into a reject:

| Reason code | Fails when |
|---|---|
| `unencodable_value` | a value fails `str.encode('utf-16-le')` (e.g. lone surrogates) |
| `row_identity_too_long` | `row_identity` is longer than 256 characters |
| `duplicate_row_identity` | the `row_identity` was already seen for this file and target |
| `extras_not_serializable` | `_extras` fails `json.dumps` |

### Loader
1. **Stage:** `DELETE stage.<t> WHERE _file_id = ?` for each target, then insert batches (default 5,000; adjustable after the spike) with `fast_executemany`. When a batch fails with SQLSTATE class `22` or `23`, the loader bisects it recursively until the offending rows are isolated. Each such row becomes a reject (`db_insert_error`, server message as detail), and the remaining rows stage normally. Classes `08`, `28` and `42`, and anything else, are raised as infrastructure errors, which marks the file `failed`. Commit, then ledger → `landed`.
2. **Promote** (one transaction): for each target, `INSERT INTO raw.<t> (…) SELECT … FROM stage.<t> s WHERE s._file_id = ? AND NOT EXISTS (SELECT 1 FROM raw.<t> r WHERE r._file_id = s._file_id AND r._row_identity = s._row_identity)`. Then insert the file's rejects through the reject store (idempotent on `(file_id, row_identity)`), delete the stage rows, and set the ledger to `promoted`. Any exception rolls back the whole transaction and marks the file `failed`.

`INSERT … WHERE NOT EXISTS` meets the §3 upsert/MERGE contract (each row lands exactly once and landed rows are immutable) without T-SQL `MERGE`'s concurrency defects.

## 9. Control-plane DDL (§8.11)

**Policy (prototype):** `sql/001_schema.sql` is the single create script, edited in place, with git as its history. The Schema DDL Confluence page is updated in the same change. At beta this becomes a create script plus a modify script. `importer db rebuild` (DEV only) drops importer-owned control-plane tables and the `raw` / `stage` schemas, then re-runs 001. It requires `IMPORTER_ENV=DEV` and the database name typed back as confirmation.

| Table | Change |
|---|---|
| `file_ledger` | **add** `project NVARCHAR(32) NOT NULL`, `class NVARCHAR(32) NOT NULL`, `file_type NVARCHAR(16) NOT NULL`, `signature_id BIGINT NULL` (FK), `request_context NVARCHAR(MAX) NULL`, `source_path NVARCHAR(1024) NULL`, `encoding VARCHAR(16) NULL`, `attempts INT NOT NULL DEFAULT 1`; **drop** `schema_id` |
| `signature_registry` | **add** `physical_part NVARCHAR(256) NOT NULL`, `xml_namespace NVARCHAR(512) NULL`, `observed_schema NVARCHAR(MAX) NULL`, `proposal NVARCHAR(MAX) NULL`, `approved_by NVARCHAR(128) NULL`, `approved_at DATETIME2 NULL`; **replace** `schema_id`, `active_mapper_ver` with `mapper_version_id BIGINT NULL` (FK → `artifact_versions.version_id`); index on `physical_part` |
| `artifact_versions` | type check adds `'schema'`; **rename** `schema_id` → `artifact_key`; **add** `content_sha256 CHAR(64) NULL`, `approved_by NVARCHAR(128) NULL` |
| `file_quarantine` | **add** `signature_id BIGINT NULL` (FK) |
| `decision_log`, `reject_repository` | comments only: new action types (`profile_match`, `bind_signature`, `activate_schema`) and reason codes |

The FK from `file_ledger.signature_id` to `signature_registry` means `signature_registry` must be created before `file_ledger` in the script.

## 10. Repositories, status and metrics (§8.11, §8.12)

- **Protocols** with in-memory and MSSQL implementations:
  - `LedgerRepository`: `create_or_get`, `transition`, `set_signature`, `record_counts`, `record_metrics`, `get`.
  - `SignatureRegistry`: `lookup`, `find_by_physical_part`, `propose`, `activate`.
  - `ArtifactRepository`: `get`, `active_schema`, `put_version`.
  - `RejectStore`: the existing Protocol plus an MSSQL implementation.
- **`create_or_get`** returns the existing row when the idempotency key (the SHA-256) already exists, which handles re-delivery.
- **Transitions:** the existing set, plus re-entry `quarantined → discovered` and `failed → discovered`, used only by `importer retry <file_id>` and later by M2b. Re-entry increments `attempts`.
- **Metrics:**
  - `rows_total` = every extracted row across targets, including reader rejects.
  - `rows_landed` = the sum over targets of `COUNT(*)` from `raw.<t>` where `_file_id = ?`.
  - `rows_rejected` = this file's rejects still in `rejected` status.
  - `percent_parsed = compute_percent(total - reader_rejects, total)`.
  - `percent_loaded = compute_percent(rows_landed, total)`.
  - **Conservation:** `rows_landed + rows_rejected == rows_total` must hold before `completed`. A violation marks the file `failed` (`conservation_violation`), because it means a bug.
  - `record_metrics` writes `percent_loaded` through a SQL `CASE` floor that matches `monotonic_update`. `compute_percent` is still the only place that rounds (invariant #1).
  - Quarantined files get `0.00` / `0.00`. Failed files keep their previous metrics.
- **Logging:** the `importer` logger writes one line per transition (`file_id`, from, to, duration in ms). Row values are never logged.

## 11. Configuration and CLI

**Database connection: the user's DataEngine library** (`Python-DataEngine`). The connection lives in DataEngine's standard connection document, `database.env` at the repo root (gitignored), under the name `importer`:

```
databases = '{"importer": {"type": "mssql", "server": "<DEV server>", "database": "<db>", "trusted": "yes"}}'
```

DataEngine chooses the auth mode from these fields:

| Fields set | Auth mode |
|---|---|
| `"trusted": "yes"` | Windows |
| `"UN": "<user@domain>"` with no `PW` | Entra MFA, interactive |
| neither | Entra integrated (cached token) |

DataEngine picks the ODBC driver (18 → 17 → …) and always sets `TrustServerCertificate=yes`. Under the no-stored-secrets rule, SQL-auth entries (`UN` + `PW`) are not used for the importer.

`db.py` rules:
- It resolves `database.env` from the repo root explicitly, so the CLI works from any directory.
- It imports DataEngine lazily, because `import DataEngine` runs `initialize()`: that reads `database.env` from the current directory, prints messages, and builds engines for every entry.
- Unit tests never import it.
- Values always go through `.engine` with SQLAlchemy Core bound parameters. DataEngine's string-only helpers (`query`, `execute`, `interop`) are used only for fixed SQL with no values in it, such as calling catalog procedures.

`.env` (gitignored, `python-dotenv`) keeps importer-only settings:

| Key | Meaning |
|---|---|
| `IMPORTER_ENV` | `DEV` enables `db rebuild` |

`.gitignore` gains `database.env`. CLI (`[project.scripts] importer = "importer.cli:main"`):

| Command | Does |
|---|---|
| `importer process <path> --project --class --type --source [--context k=v …]` | Runs one file; prints the `FileResult` |
| `importer preview <path> --mapper-version <version_id> --project --class --type [--context k=v …]` | Dry-runs that mapper artifact against the file; no DB writes |
| `importer retry <file_id>` | Re-entry for a quarantined or failed file |
| `importer schema import <db.schema.table> [--as id]` | Imports a schema definition v1 |
| `importer db rebuild` | DEV only; guarded |

## 12. Dependencies

Runtime deps added to `pyproject.toml`: `Python-DataEngine>=2.4` (brings `SQLAlchemy`, `pyodbc`, `python-dotenv` and also pandas/numpy/pymongo/psycopg2/pymssql), plus `lxml`, `openpyxl`, `xlrd`. `sqlalchemy>=2.0` is also declared directly, because the importer uses Core itself. Dev deps are unchanged (`pytest`, `hypothesis`). `claude-agent-sdk` stays deferred to M2b/M5. **Plan step 0** checks that wheels for these exist for the venv's Python 3.14, and falls back to Python 3.12 if any don't.

## 13. Testing and acceptance (§8.13)

- **Spike (plan step 0):** `fast_executemany` with `NVARCHAR(MAX)` against DEV, to fix the batch size and whether `setinputsizes` is needed. The result is recorded in the plan and on Confluence.
- **Unit (no DB):**
  - Synthetic fixtures in `tests/fixtures/`, modelled on the legacy structures with invented data:
    - a cut-down CMS formulary XML (`cellValue` wrappers, three targets);
    - URL-template CSVs with `url` and with `newurl`;
    - UHIP-style multi-sheet and SOPA-style cross-tab workbooks, generated at test time with openpyxl;
    - flat JSON;
    - NDJSON with one bad line.
  - Coverage: readers, signature, spec engine, shapers, profiler/matcher, pre-check, bisection (fake stager), and the pipeline on in-memory repositories (hit, miss, quarantine, failure, re-entry).
- **Property (Hypothesis):**
  - The existing two stay green and **unchanged**.
  - New: conservation; signature determinism and normalization; `row_identity` uniqueness per file and target; promote idempotency.
- **Integration:** `pytest -m integration` against DEV. It is skipped automatically when `database.env` has no `importer` entry, runs after `importer db rebuild`, and gives each test unique file content.
- **M2a acceptance:**
  1. A formulary fixture under the hand-registered `DC|PC|XML|prescriptionDrugTemplateVO` (spec ported from `parseFormulary`) reaches `completed`, fills `raw.formulary_benefit_definition`, `raw.formulary_tier_definition` and `raw.formulary_drug_list`, and reports `percent_loaded = 100.00`.
  2. Both URL CSV variants land in `raw.url_template` under two signatures.
  3. The NDJSON with a bad line completes with `percent_loaded < 100` and one `invalid_json_line` reject; the file is not failed.
  4. A formulary declared as another project is quarantined `unroutable` with the proposal "reuse mapper" from candidate 1 in §7.
  5. A byte-identical re-delivery returns the existing `file_id` without landing anything again.

## 14. Documentation updates (part of the work)

- `CLAUDE.md` and `README.md`: the repo layout, the M2a/M2b split, the run instructions (`.env`, `importer` CLI, `pytest -m integration`), and the prototype DDL policy.
- Confluence: the Schema DDL page is updated with every 001 change. The Blueprint gets the SQLAlchemy stack note and the M2a/M2b split. The project page's task notes are updated as milestones land.

## 15. Risks and open items

- **`fast_executemany` + `NVARCHAR(MAX)`:** possible memory or binding problems. The step-0 spike settles this before the loader is built.
- **Python 3.14 wheel availability:** checked in step 0; the fallback is 3.12. DataEngine and its dependencies already install on the global 3.14.
- **DataEngine import side effects:** contained by importing it lazily, only in `db.py`. If this ever becomes a problem, a small upstream change to DataEngine (an opt-out for auto-initialize) would be cleaner than workarounds in the importer.
- **Excel row-1 banners:** if a template has data in row 1, every file of it misses the registry. The matcher still proposes a match, the preparation note applies, and a shaper is the fallback.
- **Large XML (provider directories):** M2a parses the whole file with lxml. If memory becomes a problem, switching to `iterparse` is a later optimization.
- **Still open (§6):** trigger source (likely the existing catalog procedures), re-delivery handling (SHA-256 is interim), downstream handoff.
