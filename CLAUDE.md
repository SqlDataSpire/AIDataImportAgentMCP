# CLAUDE.md — Data Agent: Importer

Project context for Claude Code. Read this first, then the design record on
Confluence (linked below) before changing anything.

## What this is

An **ELT** import agent. It lands as much of each incoming file as possible and
leaves conversion, coercion, and cleaning to a separate downstream stage.
Control flow is a **state machine over a ledger**, not a linear pipeline:

- **Fast path (deterministic, no model):** trigger → detect & route → land raw →
  gated transactional promote. Handles the large majority of files.
- **Slow path (agent):** invoked only at the edges — an unrecognized file, or
  rows that cannot be landed. The agent diagnoses, proposes a *recorded,
  versioned* fix, and the file re-enters the machine.

## Guiding principle (do not drift from this)

Import as much as possible; defer conversion/coercion/cleaning downstream. The
importer emits a **number, not a verdict** — `percent_loaded`, never
"percent valid". Validity is a downstream semantic judgment; reporting it here
would drag cleaning back into the importer.

## Invariants — proven green, must stay green

These two are implemented and property-tested. Treat them as load-bearing;
changes that break their tests are wrong by definition.

1. **Monotonic floor on `percent_loaded`** (`src/importer/metrics.py`). The value
   only ever rises. Quantization to `DECIMAL(5,2)` happens in `compute_percent`
   (the value producer), NOT in `monotonic_update` — rounding inside the floor
   comparison can dip below the floor. Keep it that way.
2. **Idempotent reclaim** (`src/importer/reclaim.py` over `reject_store.py`).
   Re-running a schema's reject pile against the same rule lands each repairable
   row exactly once; a second pass lands nothing new. Reclaim only ever moves
   rows *out of* reject, never back — which is what keeps #1 monotonic.

## Hard rules (from the design record)

- The agent **never live-edits production code**. It authors *versioned* rules
  (rule-as-data, stored in `artifact_versions`) that stable code executes.
- Mappers and repair rules are **versioned, never edited in place** — a change
  is a new version row.
- A rejected **row** never fails the **file**. Only files that cannot even start
  (unroutable / unparseable / corrupt) are quarantined.
- A **new schema** requires human sign-off before its signature goes `active`
  (the onboarding gate). Every agent action writes a `decision_log` row.
- `sql/001_schema.sql` is authoritative and **mirrors the Schema DDL Confluence
  page** — keep the two in sync; change both together. During the prototype it is
  edited in place (git is the history); beta adopts create + modify scripts.
- **Destination databases are never written.** Destination databases registered via `importer config set destination` are read-only for schema import; `db.engine_for` engines refuse anything but a single SELECT. Every write goes to the dedicated control database.
- **Row values never reach a model.** Over MCP only metadata (names, counts, `_extras` keys, reject codes) and masked shapes (`masking.py`: letter -> `a`, digit -> `9`, fixed caps) cross; real values never do.
- **No file contents reach a model outside the importer.** Never read incoming files, data shares (UNC paths / mapped drives), Blob URLs, raw/stage rows or reject payloads directly. Doing so needs the user's explicit permission: the `PreToolUse` hook in `.claude/settings.json` (`importer.data_guard`) answers "ask" for any such call, and the MCP server announces the rule to every client on connect. The user controls it with `IMPORTER_DATA_GUARD` in any settings file's `"env"`: `auto` (default: only while an `importer mcp` server runs), `on`, `off`. Never change that switch on your own initiative.
- `§` references in code/docs point at the design specifications in `docs/superpowers/`.

## Repo layout

```
sql/001_schema.sql         Authoritative control-plane DDL (prototype: edited in place; git is history)
scripts/                   One-off spikes (fast_executemany)
docs/superpowers/          M2a / M2b design specs and implementation plans
src/importer/
  models.py                Statuses (+ re-entry), Declared, Signature, LandRow/RejectRow, FileResult
  metrics.py               percent_parsed / percent_loaded, monotonic floor          [invariant #1]
  reject_store.py          Reject repository Protocol + in-memory reference
  reclaim.py               Idempotent reclaim                                         [invariant #2]
  readers/                 delimited, Excel, JSON, NDJSON, XML -> Document
  fingerprint.py, detector.py   PROJECT|CLASS|TYPE|<physical> signatures, model-free lookup
  mappers/                 Mapper contract, declarative specs (tabular / xml / tree), loading (hash-pinned plugins),
                           preview
  shapers/                 Reviewed structural helpers (excel.anchor_header, excel.crosstab_unpivot)
  trees.py                 Nesting trees of XML documents (names and counts, never values)
  profiler.py              Miss profiling + "looks like" matching
  precheck.py, loader.py   Row triage, stage with bisection, promote
  repositories.py          Store/Tx Protocols; memory_store.py (tests) / mssql_store.py (DEV)
  schema_tables.py         raw./stage. DDL generation, rebuild drop SQL
  pipeline.py              process() / retry() — the state machine
  addresses.py, settings.py, environment.py   destination addresses; shared settings + DEV guard (in the DB)
  schema_sources.py        live / file schema import (per-destination connections)
  locators.py              read files from SMB / Azure Files / Blob locators
  proposals.py, onboarding.py   proposal review states; reuse/exact/fallback drafts; submit/accept/bind
  mcp_server.py            `importer mcp`: onboarding tools + `onboard` prompt for the user's Claude client
  admin.py, cli.py         Registration helpers and the `importer` CLI
  config.py, db.py         database.env; DataEngine imported lazily in db.get_engine() / engine_for()
  rules/base.py            RepairRule contract (rule-as-data)                         [contract]
  agent/harness.py         reserved (M5); M2b uses mcp_server.py instead              [stub]
tests/                     Unit + property suites; tests/integration needs DEV (pytest -m integration)
```

Anything still marked `stub` raises `NotImplementedError` with a docstring
naming its design section and milestone. Nothing fakes success — keep it that way.

## Running

```bash
pip install -e ".[dev]"            # into .venv (Python 3.14 works)
importer setup                     # interactive standup: checks/creates DB, sets DEV, deploys schema
pytest                             # unit + property suites (integration deselected)
pytest -m integration              # needs database.env 'importer' entry (DEV SQL Server)
importer --help                    # setup / process / preview / retry / pending / config / schema / mapper / signature / db / mcp
```

`database.env` (gitignored, see `database.env.example`) points at the control
database. Authentication supports:
- Windows Integrated (`"trusted": "yes"`)
- SQL Server Auth (`"UN": "..."`, with password entered interactively via secure prompt, passed via `IMPORTER_PASSWORD` env var, or stored in `"PW"`)
- Microsoft Entra ID (`"UN": "user@domain.com"`, `"auth": "ActiveDirectoryInteractive"`)
- Direct connection string (`IMPORTER_DATABASE_URL`)

Every other setting lives in the database (`importer config show|set|unset`).
Onboarding: open Claude Code in the repo (`.mcp.json` registers `importer mcp`) and run
`/mcp__importer__onboard`. Never auto-approve `accept_mapping` or `register_schema`.

## Current status & next milestone

**M2a and M2b are implemented.** M2b: destination-address namespace, shared settings + DEV guard in the
database, schema import (live/file), shared file locators, confirm-and-bind onboarding, and the `importer mcp`
server through which the user's own Claude client drafts mappings (no API key). Design Decisions §8.15–8.22;
spec and plan in `docs/superpowers/`. M2c-1 (nesting trees for XML, the "tree" spec kind) is implemented.

Full sequence M1–M8 and acceptance criteria are documented in `docs/superpowers/`.

## Open items (resolve as they block work; capture answers back in the design record)

- **Trigger source** — the dataset/mechanism that drives `discovered` (catalog procedures or file event stream).
- **Re-delivery handling** — idempotency-key design when a file is re-sent (interim: SHA-256 of bytes).
- **Downstream handoff** — the raw zone → transform/clean stage contract.

## Design record & Documentation

Design specifications and build plans are maintained in `docs/`:
- `docs/superpowers/specs/`: Architectural and milestone design specifications
- `docs/superpowers/plans/`: Detailed milestone implementation plans
- `docs/architecture/`: Architecture diagrams, C4 models, and layer definitions
- `docs/mapping-manual.md`: Manual mapping reference and specification guide
- `sql/001_schema.sql`: Authoritative control-plane DDL

