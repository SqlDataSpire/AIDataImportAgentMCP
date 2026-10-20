# M2b — Onboarding: Design Spec

- **Status:** design approved section by section on 2026-10-03. This document consolidates it for implementation.
- **Decision record:** Confluence *Data Import Agent — Design Decisions* §8.15–8.22 (page 3774676996). `§` references below point there.
- **Builds on:** M2a (`docs/superpowers/specs/2026-10-02-m2a-fast-path-design.md`), which is implemented and accepted on DEV.

## 1. Goal

When a file's signature is unknown, a person working in their own Claude client sees what the file looks like and which known schemas it resembles. Claude drafts the column mapping and checks it against the file. The person approves, the signature is bound, and every file waiting under that signature lands. The model only ever sees metadata (headers, schemas, earlier mappings and a few capped sample values). Rows still go straight from the file to SQL Server.

The guiding principle is unchanged (§0). Onboarding never cleans or coerces anything. When in doubt a column goes to `_extras`, so nothing is lost.

## 2. Scope

**In M2b**
- **Raw-zone namespace (§8.15):** schemas are identified by destination address. Raw and stage table names are derived from that address.
- **Shared settings and environment safeguards (§8.17):**
  - a versioned `config` artifact;
  - the `dbo.database_identity` table and `importer db init`;
  - the DEV double guard;
  - `.env` is retired.
- **Schema import (§8.16):** live over per-destination connections, or from a file. Wildcards supported, with drift-aware re-import.
- **Onboarding core (§8.18):** proposal review states, the reuse / exact / fallback drafts, the bind transaction, and retry of every waiting file.
- **Shared file locations:** the ledger records the file's shared location exactly as the caller (later, the catalog) gives it: a CIFS/SMB path, an Azure Files share or an Azure Blob URL. Any reviewing machine reads the file from there to preview and retry (§7.6).
- **The MCP server (§8.19):** `importer mcp`, its tools, the `onboard` prompt, and the sign-off gate.
- **CLI additions:** `config show|set`, `db init`, `schema import` (new syntax), `pending`, `mcp`.
- **The hand-mapping manual** (`docs/mapping-manual.md`), as the final task.
- **DDL, documentation and Confluence updates.**

**Not in M2b**
- Schemas built from a file's own columns, i.e. brand-new destination tables. This is the long-term goal.
- The reject-repair agent (M5), reclaim triggers (M6), the trigger source, catalog validation, and the downstream handoff.
- The Claude Agent SDK harness and unattended model drafting. `agent/harness.py` stays a stub, with its docstring updated to point at §8.19.
- Auto-approval of any binding.
- **New plugin tooling and richer declarative specs for deeply nested files.** These are deferred to a dedicated later milestone. Executable code loaded from the database would open a code-injection path, so **rule-as-data (the declarative spec) is the requirement**, and the later milestone's job is to make specs expressive enough that plugins are rarely needed. Until then, the few complex nested files keep going through the existing legacy parsers. M2a's file-based, hash-pinned plugin loading is unchanged, and M2b exposes no plugin path through MCP (`submit_mapping` refuses `"kind": "plugin"`).

## 3. Architecture

```
unattended run (VM, no model)                 attended review (any machine with a Claude client)
process() ─miss─► profile+match ─►            Claude ──MCP stdio──► importer mcp ──► onboarding core
  signature 'proposed' (needs_mapping)          list_pending / get_file_profile / search_schemas /
  file quarantined 'unroutable'                 get_prior_mappings / get_samples / preview_mapping /
  ledger keeps the file's shared locator        submit_mapping ─► draft = inactive mapper version
  (CIFS/SMB, Azure Files, Blob URL)             accept_mapping (user approves) ─► bind tx ─► retry all
                                                waiting files (read from their locators) ─► land
```

### Module layout (new or changed)

```
src/importer/
  addresses.py          NEW  destination address parsing + physical names (§8.15)
  settings.py           NEW  config artifact read/write, defaults, validation (§8.17)
  environment.py        NEW  database_identity read, DEV guard, target banner (§8.17)
  schema_sources.py     NEW  live / file schema sources, wildcard expansion (§8.16)
  locators.py           NEW  read a file by locator: UNC/SMB path or Azure Blob URL (§7.6)
  onboarding.py         NEW  proposal states, deterministic drafts, submit/accept/reject, bind (§8.18)
  mcp_server.py         NEW  MCP tools + 'onboard' prompt over onboarding.py (§8.19)
  mcp_reference.md      NEW  spec-format reference served by get_spec_reference (seed of the manual)
  schema_tables.py      CHG  address-derived names, importer-owned schema marking, rebuild SQL
  admin.py              CHG  register_schema takes an address; register_mapper/signature unchanged
  pipeline.py           CHG  proposal object shape; locator recorded verbatim; retry/preview via locators
  repositories.py       CHG  Settings/Identity protocols; registry proposal + review updates
  memory_store.py       CHG  in-memory implementations of the above
  mssql_store.py        CHG  MSSQL implementations; per-destination engines for schema import
  db.py                 CHG  engine_for(server, database) reusing the importer entry's auth mode
  config.py             CHG  .env loading removed
  cli.py                CHG  config, db init, schema import, pending, mcp; DEV guard for db rebuild
  agent/harness.py      CHG  docstring only — superseded by §8.19
docs/mapping-manual.md  NEW  hand-mapping manual (final task)
.mcp.json               NEW  registers `importer mcp` for Claude Code in this repo
```

## 4. Raw-zone namespace (§8.15)

### Addresses (`addresses.py`)
- **Address:** `database.schema.table`, compared case-insensitively and stored lowercased, e.g. `npvcs.relational.url_template`.
- **Validation:** each part matches `[A-Za-z0-9_]+`. Anything else raises `AddressError`. Destination names with spaces or hyphens are a known risk (§13).
- **Physical names:**

| Item | Name | Example |
|---|---|---|
| Raw schema | `<db>_<schema>` | `npvcs_relational` |
| Raw table | `<db>_<schema>.<table>` | `npvcs_relational.url_template` |
| Stage table | `stage.<db>_<schema>__<table>` | `stage.npvcs_relational__url_template` |
| Raw primary key | `pk_<db>_<schema>__<table>` | `pk_npvcs_relational__url_template` |

- **Length limits:** every generated identifier must be ≤ 128 characters (the SQL Server limit), otherwise `AddressError`.
- **Reserved raw-schema names, refused:** `dbo`, `stage`, `sys`, `guest`, `information_schema`, `meta`, `raw`, and anything starting with `db_` (SQL Server role schemas).
- **Collisions after mapping:** e.g. `a_b.c.t` and `a.b_c.t` both give `a_b_c.t`. Registration refuses the second address and names the first.
- **Allowed destination:** the database part must be a destination configured in the shared settings (§5).

### Code changes
- **Schema identity:** `artifact_versions.artifact_key` for schemas, the mapper spec `target`, `LandRow.target`, `reject_repository.schema_id` and the matcher's `schema_id` all carry the **address**.
- **`parse_spec` target check:** `"target must be a registered address"` (exists in `known_schemas`) replaces the identifier-regex check.
- **`schema_tables`:**
  - `create_raw_sql`, `create_stage_sql` and `add_column_sql` take an address.
  - `ENSURE_SCHEMAS_SQL` becomes `ensure_schema_sql(name)`. It creates the schema if missing and marks it **importer-owned** with the extended property `importer_owned = 1`.
- **`REBUILD_DROP_SQL`** drops the tables in, and then, every schema carrying `importer_owned = 1`, plus `stage`. It also drops the control-plane tables **except** `dbo.database_identity`. It never touches unmarked schemas.
- **M2a tests and fixtures** move to addresses: `url_template` → `npvcs.relational.url_template`, and the formulary targets → `npvcs.xml.formulary_*`. Both invariant suites are untouched.

## 5. Shared settings and environment safeguards (§8.17)

### Settings (`settings.py`)
Stored as artifact type **`config`** under key `importer`, versioned like every other artifact. Each change creates a new active version and a `decision_log` row (`action_type = 'config_change'`). The document:

```json
{
  "destinations": {
    "NPVCS":          {"server": "cssnpi", "database": "NPVCS"},
    "JSON_IDEX":      {"server": "cssnpi", "database": "JSON_IDEX"},
    "RX":             {"server": "cssnpi", "database": "RX"},
    "NPIDATA_IMPORT": {"server": "cssnpi", "database": "NPIDATA_IMPORT"},
    "ZIPCODE":        {"server": "cssnpi", "database": "ZIPCODE"}
  },
  "samples": {"max_values": 5, "max_chars": 200}
}
```

- **Destination keys** are matched case-insensitively against an address's database part. The **allowed destinations** are exactly these keys.
- **No auth in settings:** a destination entry holds only server and database. A connection uses the same auth mode as the local `importer` entry (Windows trusted, or Entra `UN`) as the person running the command. Nothing secret is stored.
- **Defaults:** with no config version, `destinations` is empty, so schema registration refuses with a hint to run `importer config set destination …`. `samples` falls back to the defaults shown.
- **CLI:**
  - `importer config show`
  - `importer config set destination <NAME> --server S --database D`
  - `importer config unset destination <NAME>`
  - `importer config set samples.max_values N` (also `samples.max_chars N`)
- **Local files:** `.env` and `IMPORTER_ENV` are removed (`python-dotenv` stays: `db.py` parses `database.env` with it). `database.env` (the DataEngine `importer` entry) is the only local file: the pointer to the control database.

### Environment marker (`environment.py`)
- **`dbo.database_identity`** is one row: `id TINYINT PRIMARY KEY CHECK (id = 1)`, `environment VARCHAR(8) CHECK IN ('DEV','PROD')`, `created_at`, `created_by`.
- **`importer db init --environment DEV|PROD --confirm <database>`** creates the table if it is missing and inserts the row. It **refuses if a row already exists**, so it can't overwrite an environment. It also refuses a database holding any table the importer doesn't own (a destination), shows the target banner and needs the typed database name; nothing is written until every check passes (§15, final review). It needs table-creation rights (db_owner).
- **DEV guard:** `db rebuild` and any later dev-only command require **both** of these, and it refuses otherwise:
  - `database_identity.environment = 'DEV'`;
  - `'dev'` appears in `DB_NAME()` read from the server (case-insensitive), not taken from `database.env`.

  The existing typed confirmation of the database name stays.
- **Target banner:** every CLI command prints `importer @ <server>/<database> [<ENV>]` to stderr first. `[UNSET]` appears when the identity row is missing. MCP `list_pending` returns the same target string.
- **Beta (not M2b):** user sessions refuse a DEV database unless a developer flag is set. M2b only records the environment and enforces the rebuild guard.

## 6. Schema import (§8.16)

### Sources (`schema_sources.py`)
- **`LiveSource(destination)`** opens a per-destination engine, `db.engine_for(server, database)`. It runs, inside that database:
  - columns: `SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION`;
  - wildcard listing: `SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'`, filtered by `fnmatch` patterns on schema and table.

  There are no three-part names, so this is Azure SQL Database-compatible. The caller needs `VIEW DEFINITION` on the destination.
- **`FileSource(path)`** reads either a JSON list of column names (`["planid", "metal"]`) or a CSV with one column name per row. It applies to one explicit address only (no wildcards).
- **The agent never connects to a destination.** No MCP tool exposes a live source except `register_schema`, which is sign-off gated (§8).

### Command
```
importer schema import NPVCS.relational.url_template
importer schema import "NPVCS.*.*"            # every base table in NPVCS
importer schema import "RX.dbo.*"
importer schema import ZIPCODE.dbo.zip_lookup --from-file zip_cols.json
```

**Per address, in one transaction:**
1. Validate the address (§4).
2. Read the columns.
3. Compare them with the active schema version:

| Comparison | Result |
|---|---|
| No version yet | `added` |
| Identical column list (case-insensitive, order-insensitive) | `unchanged`, nothing written |
| Different | `changed`, a new version |

A new version keeps every earlier column (raw tables only gain columns) and appends the new ones. Columns that disappeared from the destination are reported as `dropped_at_source`, but stay in the schema.

**Output:** a JSON report listing `added`, `unchanged`, `changed` (with `new_columns`, `dropped_at_source`) and `refused` (address, reason, e.g. invalid identifier or collision). One `decision_log` row per run (`action_type = 'schema_import'`, `approval = <user>`).

**Not automatic:** the importer never registers schemas during file processing.

## 7. Onboarding core (§8.18)

### 7.1 Proposal shape
`signature_registry.proposal` changes from a JSON list to an object:

```json
{
  "candidates": [ /* Candidate.to_dict(), unchanged */ ],
  "review": {
    "state": "needs_mapping",
    "draft_version_id": null,
    "path": null,
    "rationale": [],
    "preview": null,
    "notes": [],
    "history": []
  }
}
```

**Field values:**
- **`state`:** `needs_mapping`, `ready_for_review`, `revising`, `accepted` or `rejected`.
- **`path`:** `reuse`, `exact`, `fallback` or `model`.
- **`rationale` entries:** `{column, target, how, evidence, confidence}`, where `how` is `exact`, `reuse`, `model` or `extras`.
- **`preview`:** the summary from §8.3.
- **`notes`:** `{at, by, text}`.
- **`history`:** `{at, by, event, draft_version_id}`.

`revising` is not used: a revision is another `submit_mapping` carrying a `note`.

**Behavior:**
- **Repeated miss on the same signature:** `registry.propose()` updates `candidates` only, and never resets `review`.
- **`proposal_summary`** on `FileResult` is unchanged.

### 7.2 Deterministic drafts
`onboarding.suggest(signature_id)` returns the cheapest applicable draft. It writes nothing.

1. **reuse:** a `reuse_mapper` candidate exists. The draft is that mapper version's definition, with path `reuse`.
2. **exact:** a single tabular candidate schema contains every observed column of one table (score 1.0 after name normalization). The draft is `{"kind": "tabular", "extractions": [{"target": <address>, "records": <sheet name for Excel, "*" otherwise>, "auto": true, "unmapped": "extras"}]}` per matched table, with path `exact`.
3. **fallback:** always available for tabular files that have at least one candidate. It uses the best candidate per table with `auto: true`, `unmapped: "extras"`. Name matches land, everything else goes to `_extras`, and nothing is lost. Path `fallback`.
4. **No draft:** XML/JSON files with no `reuse_mapper` candidate get no deterministic draft. They need a model-drafted spec (path `model`).

### 7.3 Submit, revise, reject
- **`submit(signature_id, spec, rationale, *, by, note=None)`:**
  1. Validate with `parse_spec(spec, known_schemas=<active schema addresses>, known_shapers=registered_shapers())`. Plugin specs are refused here, because plugins are human-committed code (§8.5).
  2. Preview against the triggering file, read from its locator (§7.6).
  3. Store the spec as an **inactive** version of the mapper key `sig.<signature_id>` (`put_version(..., activate=False)`).
  4. Set `draft_version_id`, `path`, `rationale` and `preview`; state → `ready_for_review`; append `note` and history; write one `decision_log` row (`draft_mapping`).

  Re-submitting while `ready_for_review` or `revising` creates the next inactive version, which is how revise-with-note works.
- **`reject(signature_id, reason, *, by)`:** state → `rejected`, with the note and history recorded and a `decision_log` row (`reject_mapping`). Waiting files stay quarantined. A later `submit` reopens the proposal.
- **Validation errors** come back as structured `{"errors": [...]}` and nothing is stored, so the model can fix the spec and resubmit.

### 7.4 Accept and bind
**`accept(signature_id, draft_version_id, *, by)`** requires state `ready_for_review` and a matching `draft_version_id`, so you approve exactly what you saw. In one transaction:
1. **Canonical comparison:** canonicalize the draft (`json.dumps(sort_keys=True, separators=(",", ":"))`) and compare it with every mapper version bound to an `active` signature that targets any of the same addresses. If one is identical, bind to it; the draft stays inactive as history. Otherwise activate the draft version and set its `approved_by`.
2. **Activate the signature:** `signature_registry` → `status = 'active'`, `mapper_version_id`, `approved_by`, `approved_at`.
3. **Close the review:** proposal state `accepted`, with history.
4. **Log it:** a `decision_log` row with `action_type = 'bind_signature'`, `applied = 1`, `approval = <by>`.

**After commit:** call `pipeline.retry(file_id)` for **every** ledger row with that `signature_id` in status `quarantined`. Return the list of `FileResult.to_dict()`. A retry failure is reported per file and never undoes the bind.

### 7.5 Pending list
**`pending()`** returns every `proposed` signature whose state is not `accepted`, with:
- `signature_id`, `fingerprint`, `state`;
- the suggested `path` from `suggest()`;
- the top three candidate summaries;
- `waiting_files` (count) and the triggering file (`file_id`, `original_filename`, `discovered_at`).

The CLI `importer pending` prints the same list.

### 7.6 Shared file locations (makes review machine-independent)
**Problem:** M2a records `source_path` as a resolved local path. A file quarantined on the VM can't be previewed or retried from a laptop.

**Decision:** production files are always reachable from a shared location registered in the catalog: **CIFS/SMB**, an **Azure Files share** or **Azure Blob storage**. The importer copies nothing. It records where the file lives and every machine reads it from there.

- **Locator:** `process()` takes a locator, and `file_ledger.source_path` stores it **verbatim** (it is no longer resolved to a local path):

| Locator | Form | Read by |
|---|---|---|
| CIFS/SMB share | `\\server\share\…\file.xml` | the operating system, as the current Windows user |
| Azure Files share | `\\<account>.file.core.windows.net\<share>\…` | the operating system (SMB), as the current user (Entra/Kerberos) |
| Azure Blob | `https://<account>.blob.core.windows.net/<container>/<path>` | `azure-storage-blob` with `azure-identity` (`DefaultAzureCredential`, the user's Entra identity); no keys or SAS tokens are stored: a Blob URL with a query string is refused, and the host is checked on the parsed URL |
| Local path | `C:\…` | the operating system; accepted for development and tests only, and logged with a warning that other machines can't read it |

- **One reader, `locators.read_bytes(locator)`,** used by `process`, `retry`, and every onboarding preview and sample. The Blob client is imported lazily, so machines that never touch Blob don't load it.
- **Integrity:** every read after discovery checks the SHA-256 against `idempotency_key` (as M2a's `retry` already does). A changed file is refused with "deliver it as a new file".
- **Unreachable file:** preview and accept report "file not reachable from this machine: `<locator>`" for that file. The bind itself is unaffected.
- **Catalog seam:** until the trigger-source open item is settled, the caller passes the locator. The catalog becomes the caller later, with no importer change.

## 8. MCP server (§8.19)

### Server
- **`importer mcp`** starts a stdio server using the official Python `mcp` library (FastMCP). Plan step 0 checks the current version and the prompt, tool-annotation and elicitation APIs.
- **Connection:** it opens one `MssqlStore` through `database.env` and runs as the operating-system user. `by` for every write is `getpass.getuser()`.
- **Registration:**
  - repo `.mcp.json`: `{"mcpServers": {"importer": {"command": "importer", "args": ["mcp"]}}}`;
  - or, per machine: `claude mcp add importer -- importer mcp`;
  - Claude Desktop uses the same command in its config.
- **Tool results** are JSON. The server holds no state between calls; everything lives in the database.

### Tools

| Tool | Input | Output / effect | Annotation |
|---|---|---|---|
| `list_pending` | — | `{target, pending: [...], stranded: [...]}` (§7.5; `stranded` = files still quarantined under an active signature, with their `importer retry` command) | read-only |
| `get_file_profile` | `signature_id` | observed profile, candidates with matched/missing/extra, `suggested_draft` (§7.2) | read-only |
| `search_schemas` | `columns?: [str]`, `text?: str`, `limit=10` | addresses ranked by column overlap (same score as the matcher), or by substring when `text` is given | read-only |
| `get_schema` | `address` | `{address, version_no, columns, source}` | read-only |
| `get_prior_mappings` | `address` | specs of mapper versions bound to active signatures targeting the address, with their fingerprints | read-only |
| `get_samples` | `signature_id`, `columns: [str]` | ≤ `samples.max_values` distinct non-empty values per column, each truncated to `samples.max_chars`; XML: columns are element paths | read-only |
| `get_spec_reference` | — | `mcp_reference.md` text | read-only |
| `preview_mapping` | `signature_id`, `spec` | per-target counts, `_extras` keys, reject counts by reason, ≤ 3 sample rows per target (values truncated), or `{errors}` | read-only |
| `submit_mapping` | `signature_id`, `spec`, `rationale`, `note?` | §7.3; returns `draft_version_id`, the preview and a review table (markdown) | writes a draft |
| `reject_mapping` | `signature_id`, `reason` | §7.3 | writes |
| `accept_mapping` | `signature_id`, `draft_version_id` | §7.4; returns per-file results | **sign-off** |
| `register_schema` | `address_or_pattern`, `columns?: [str]` | §6; `columns` = file source for one address | **sign-off** |

### Sign-off gate
- **Annotations:** `accept_mapping` and `register_schema` are marked destructive and non-read-only, so clients ask before running them. Read tools are marked read-only, so users may auto-approve them.
- **Elicitation:** where the client supports it, both sign-off tools also send an MCP *elicitation*, a confirm dialog shown by the client: "Bind `<fingerprint>` to `<targets>` and retry N files?" The server proceeds only on an explicit accept. This holds even if the user has auto-approved the tool. Without elicitation support, the client's tool-approval prompt is the gate. It is implemented with the SDK's resolvers (`Resolve`/`Elicit`), which work on both the handshake-era protocol and 2026-07-28 (`input_required`); the hidden approval parameter is not part of the tool's input schema.
- **Documentation:** the setup docs say never to auto-approve the two sign-off tools.
- **Audit:** the approver is recorded from the OS user, never from a model-supplied argument.

### `onboard` prompt
An MCP prompt (Claude Code: `/mcp__importer__onboard`). Its text instructs the model to:
1. Call `list_pending`, then work one signature at a time, oldest first.
2. Start from `get_file_profile`. If a `reuse` or `exact` draft exists, preview it and present it.
3. Otherwise gather evidence: `get_prior_mappings` for the candidate schemas, `search_schemas` when the candidates are weak, and `get_samples` only for columns whose names alone don't settle the mapping. Then draft the spec using `get_spec_reference`.
4. Never invent a target column. When unsure, leave the column unmapped so it lands in `_extras`, and say so.
5. Call `preview_mapping` and fix any problems. Then `submit_mapping` with a rationale for every non-exact column.
6. Show the review table (file column → target, how, evidence, confidence) and the preview summary. Ask the user to accept, correct, or reject.
7. Apply corrections with another `submit_mapping`, recording the user's words as `note`.
8. Call `accept_mapping` only after the user explicitly approves the latest draft. Then report the per-file results.
9. Never edit code or files. Use only importer tools.

### Logging
- **`decision_log`:** every tool call writes a row (`action_type = 'mcp:<tool>'`, `observed` = the arguments with `spec` included and sample values excluded, `approval` = OS user for sign-off tools).
- **Never logged:** results containing sample values or sample rows are never written to `decision_log` or to the `importer` logger (§8.12).

## 9. Control-plane DDL (`sql/001_schema.sql`, mirrored on the Schema DDL page)
- **`artifact_versions.artifact_type`:** the check becomes `IN ('schema','mapper','rule','config')`. `artifact_key` comments: schema = address, mapper = `sig.<signature_id>` or a hand-chosen name, config = `importer`.
- **New `dbo.database_identity`** (§5), created with `IF OBJECT_ID(...) IS NULL`, so rebuild never drops or re-creates it. The row is written only by `db init`.
- **`file_ledger.source_path`:** comment only. It holds the locator verbatim (§7.6).
- **Comments only (no structural change):**
  - `signature_registry.proposal` is now the §7.1 object;
  - new `decision_log.action_type` values: `config_change`, `schema_import`, `draft_mapping`, `reject_mapping`, `bind_signature`, `mcp:<tool>`;
  - `reject_repository.schema_id` holds an address.
- **Rebuild:** the drop list excludes `dbo.database_identity`. Raw and stage cleanup works by the `importer_owned` mark (§4).

## 10. CLI summary

| Command | Change |
|---|---|
| `importer config show / set / unset` | new (§5) |
| `importer db init --environment DEV\|PROD --confirm <database>` | new (§5) |
| `importer db rebuild` | guard: identity DEV **and** `DB_NAME()` contains "dev", plus typed confirmation; `--confirm` still skips only the typed step |
| `importer schema import ADDRESS\|PATTERN [--from-file F]` | new syntax (§6); the M2a `--as` option is removed |
| `importer pending` | new (§7.5) |
| `importer mcp` | new (§8) |
| `process`, `preview`, `retry`, `mapper add`, `signature register` | unchanged apart from addresses and the target banner; `process`/`retry` take and read locators (§7.6) |

## 11. Dependencies
- Add `mcp` (the official Python MCP SDK). Plan step 0 confirms a wheel for Python 3.14 and the API surface used in §8.
- Keep `python-dotenv` (it parses `database.env`).
- Add `azure-storage-blob` and `azure-identity` for Blob locators, both imported lazily (§7.6).
- `claude-agent-sdk` is not added.

## 12. Testing and acceptance (§8.20)

### Unit (no database, no model)
- **Namespace:**
  - address parsing and lowercasing;
  - physical names and length limits;
  - reserved names;
  - collision detection;
  - the allowed-destination check.
- **Settings:** versioned `config` artifact; defaults; `set`/`unset` round trip; `decision_log` row per change.
- **Environment:** the DEV guard truth table (identity DEV/PROD/missing × name with/without "dev"); `db init` refuses a second run.
- **Schema import:**
  - `FileSource` (JSON and CSV);
  - wildcard expansion against a fake live source;
  - added / unchanged / changed / refused reporting;
  - columns are never dropped.
- **Onboarding:**
  - `suggest` paths (reuse, exact, fallback, none for XML without reuse);
  - submit validation errors store nothing;
  - plugin specs refused;
  - revise creates the next inactive version;
  - reject and reopen;
  - accept requires the matching draft;
  - canonical reuse of an identical existing mapper;
  - accept retries **every** waiting file;
  - a repeated miss keeps the review state.
- **Locators:**
  - UNC and local paths stored verbatim (local paths not resolved to another form);
  - Blob URLs dispatched to the Blob reader (faked in unit tests);
  - SHA-256 mismatch refused;
  - an unreachable file is reported per file without undoing a bind.
- **MCP tools as functions** over `MemoryStore`:
  - the sample cap and truncation;
  - preview rows ≤ 3;
  - `decision_log` rows written;
  - sample values never in `decision_log`;
  - `approved_by` comes from the OS user.
- **MCP layer:** the `mcp` library's in-process client lists the tools (with annotations) and the `onboard` prompt, calls `list_pending`, and calls `accept_mapping`.
- **M2a suites:** migrated to addresses. The two invariant suites stay unchanged and green.

### Integration (DEV, `pytest -m integration`)
- **Setup:** `db init` / identity, then rebuild.
- **Live schema import:** a test table in a test schema inside `import_agent_dev` acts as a stand-in destination, configured as destination `IMPORT_AGENT_DEV`.
- **Locator round trip:** a fixture on a UNC path (e.g. `\\localhost\c$\…` or a test share) is processed, quarantined and retried through its locator.
- **Onboarding end to end, with no model:** miss → submit an exact draft → accept → the waiting files land in `<db>_<schema>.<table>`.

### M2b acceptance
1. **Pre-register:** `importer schema import` registers destination tables, live or from file.
2. **Exact path, no model:** a fixture whose columns exactly match a schema gets an auto draft, is accepted and lands.
3. **Reuse path, no model:** a new-state formulary reuses the registered mapper, is accepted and lands.
4. **Model path, live:** in Claude Code, `/mcp__importer__onboard` handles the URL-template variant whose column is `url`. Claude proposes `url → newurl`, citing the earlier mapping. The user approves at the sign-off prompt. That file, and a second file waiting under the same signature, land. `decision_log` shows the draft and the approval.
5. **First real file:** a real file from a live feed is onboarded the same way.
6. **DEV guard:** `db rebuild` is refused against a database whose identity isn't DEV, or whose name lacks "dev".
7. **Hand-mapping manual delivered:** `docs/mapping-manual.md`, mirrored to Confluence.

## 13. Risks and open items
- **Destination identifiers:** NPVCS, RX and the other destinations may contain table or column names outside `[A-Za-z0-9_]` (spaces, hyphens). These are refused and listed in the import report. If there are many, a reversible name-encoding rule becomes a follow-up decision.
- **Sign-off depends on the client:** the gate relies on client approval prompts and, where supported, elicitation. A user who auto-approves everything in a client without elicitation weakens it. Mitigation: setup documentation, and the approver is always recorded.
- **Share and Blob access:** every reviewer needs read access to the shares and containers holding waiting files (an Entra/AD group grant, managed alongside the catalog).
- **Destination servers:** the per-destination server and database values are configuration data. Their real values are entered with `importer config set` during setup, not fixed in code.
- **Complex nested files (deferred milestone):**
  - extend the declarative spec for deeply nested XML/JSON;
  - decide how a reviewed plugin reaches every machine without executing code from the database;
  - add a plugin chapter to the hand-mapping manual.

  Options already weighed: repo + redeploy; source stored in `artifact_versions` (rejected for now because of the code-injection risk); a shared plugins folder.
- **Open items unchanged:** trigger source, re-delivery, downstream handoff (§6 of the decision record).

## 14. Documentation updates (part of the work)
- **`CLAUDE.md` and `README.md`:**
  - layout;
  - `database.env` as the only local file;
  - `db init`;
  - `config`;
  - MCP setup (`.mcp.json`, `claude mcp add`);
  - "never auto-approve `accept_mapping` / `register_schema`";
  - M2b status.
- **`docs/mapping-manual.md`:** writing a spec by hand, `mapper add`, `preview`, `signature register`, `retry`.
- **Confluence:**
  - the Schema DDL page, with every 001 change;
  - Design Decisions §8.20 (testing and acceptance) and the §8.17 cleanup (agent limits removed);
  - the Blueprint: MCP replaces the Agent SDK for M2b;
  - the project page's task list as milestones land.

## 15. Refinements made while planning

1. **`python-dotenv` stays.** `db.py` parses `database.env` (a dotenv-format file) with it. Only `.env` / `IMPORTER_ENV` are retired. This corrects spec §5 and §11.
2. **No `revising` state.** A revision is simply another `submit_mapping` with a `note`, so `revising` is never set. The states used are `needs_mapping`, `ready_for_review`, `accepted` and `rejected`.
3. **Relaxed spec targets.** `parse_spec` accepts targets of one to three `[A-Za-z0-9_]+` parts separated by dots, so unit tests can keep short inline targets. In practice targets are addresses, because the target must be a registered schema whenever `known_schemas` is given (always, outside unit tests).
4. **`get_file_profile` reads the file.** It returns the full profile (XML element paths with counts), because the registry row only stores the root and namespace for XML.
5. **Re-import that only loses columns reports `unchanged`.** It lists the losses in `dropped_at_source`. Columns are never dropped, so a new version would be identical.

### Changes from execution and the final review (2026-10-05)

6. **MCP SDK.** `mcp` 2.3.0 (v2 API) is used as planned. Elicitation uses `Resolve`/`Elicit` resolvers instead of `ctx.elicit()`: on protocol 2026-07-28 a server cannot send `elicitation/create` mid-call. Elicitation support is read from `ctx.client_capabilities`.
7. **Clean stdio.** DataEngine prints while building engines; that runs with stdout redirected to stderr, so `importer mcp`'s JSON-RPC stream stays clean.
8. **Destinations are read-only by construction.** `db.engine_for` engines refuse every statement but a single SELECT, and live sources are disposed after each import.
9. **`db init` guard.** Typed database-name confirmation and target banner; refuses a database holding tables the importer doesn't own; nothing is written until every check passes.
10. **No stranded files.** `accept` re-scans the signature's quarantined files after its retries; files still quarantined under an active signature are listed as `stranded` by `list_pending` and `importer pending`.
11. **Blob locators.** URLs with a SAS token or other query string are refused (without echoing it); the Blob host is checked on the parsed URL.
12. **No quoted samples.** The onboarding prompt and spec reference tell the model never to quote sample values in rationale, notes or reasons (they are kept in `decision_log`).
13. **Row values never cross MCP (masking).** First real file: a mostly-unmapped preview of a 2-row file returned every value through `_extras`, because the sample cap counted rows, not width. Now everything derived from rows (preview samples incl. `_extras`, `get_samples`, previews stored in proposals) is masked to its shape (letter -> `a`, digit -> `9`, punctuation kept, lowercase) under fixed caps (3 rows x 20 fields x 40 characters per preview; `get_samples` 5 values x 200 characters), and `row_identity` is dropped from samples. Counts, column names, `_extras` keys and reject codes stay as metadata. Decided with the user 2026-10-05; the review on that file needed no real value.
14. **No file contents outside the importer (user rule, 2026-10-05).** At no point may an incoming file's contents reach the model outside the importer's MCP tools; any exception needs an explicit permission check. Enforced twice: the MCP server's `instructions` and the `onboard` prompt state the rule to every client, and a Claude Code `PreToolUse` hook (`importer.data_guard`, wired in `.claude/settings.json`) answers "ask" when Read/Grep/Glob/Bash/PowerShell would touch a data share, a Blob/Azure Files locator, raw/stage rows, reject payloads or `importer preview` output (fails closed on an unreadable call). The user controls it: `IMPORTER_DATA_GUARD` = `auto` (default; on only while an `importer mcp` server runs on the machine, detected by a per-process marker that `importer mcp` writes and removes, stale markers ignored), `on` or `off`, set in any Claude Code settings `env`. Whoever the MCP is deployed to can switch it off; nothing ships a guard its user cannot disable.
