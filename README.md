# Data Agent: Importer

An **ELT** import agent: it lands as much of each incoming file as possible, then
leaves conversion/coercion/cleaning to a separate downstream stage. Control flow
is a **state machine over a ledger**, not a linear pipeline:

- **Fast path (deterministic, no model):** trigger → detect & route → land raw →
  gated transactional promote. Handles the large majority of files.
- **Slow path (agent):** invoked only at the edges — an unrecognized file, or a
  file that cannot be landed. The agent diagnoses, proposes a *recorded,
  versioned* fix, and the file re-enters the machine. The agent never live-edits
  production code.

This repo is the build scaffold referenced by the **Implementation Blueprint &
Build Plan**. It stands up the structure, the authoritative DDL, and two
property-tested invariants that are **green out of the box**.

> [!TIP]
> **Complete Operator & User Manual**: See [INSTRUCTIONS.md](INSTRUCTIONS.md) for detailed architecture, credential security options, DEV vs. PROD environments, promoting mappings, and full MCP client configurations.

## Layout

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
  config.py, db.py         database.env / IMPORTER_DATABASE_URL; SQLAlchemy Core + pyodbc engine
  rules/base.py            RepairRule contract (rule-as-data)                         [contract]
  agent/harness.py         reserved (M5); M2b uses mcp_server.py instead              [stub]
tests/                     Unit + property suites; tests/integration needs DEV (pytest -m integration)
```

## What's real vs. stub

**Implemented and tested** are the two invariants most worth pinning down before
anything else is wired, because everything downstream relies on them:

1. **Monotonic floor on `percent_loaded`** (`metrics.py`). Reclaim only moves
   rows *out of* reject into landed, never the reverse, so the number handed to
   the caller only rises — a guaranteed lower bound, never wrong, occasionally
   conservative.
2. **Idempotent reclaim** (`reclaim.py` over `reject_store.py`). Re-running a
   schema's reject pile against the same rule lands each repairable row exactly
   once; the second pass lands nothing new.

**M2a — the deterministic fast path — is implemented** on top of them and
accepted against the DEV SQL Server: readers for all six formats, exact
signatures, declarative / shaper / plugin mappers, the raw zone (mirroring the
existing destination tables), the bisecting loader, miss profiling with "looks
like" proposals, quarantine / failure / re-entry, and the `importer` CLI. Every
step is unit-tested against an in-memory store with real rollback, and
integration-tested against SQL Server.

**M2b — confirm-and-bind onboarding — is implemented:** destination-address
naming for the raw zone, shared settings and the DEV guard stored in the
database, schema import (live or from a file), shared file locators, and the
`importer mcp` server through which the user's own Claude client drafts
mappings for unknown files; a person approves every binding. The unattended
agent harness (M5) is still an honest stub that raises `NotImplementedError`.
Nothing fakes success.

## Stack

Python 3.11+ (developed and tested on Python 3.14), SQL Server via SQLAlchemy 2.x Core over `pyodbc`
(with optional fallback to the in-house DataEngine library if present), lxml, openpyxl, xlrd,
and the official Model Context Protocol Python SDK (`mcp`) for onboarding;
`azure-storage-blob` / `azure-identity` for Azure Blob and Azure Files locators.
No model API key is required: the model is the user's own Claude client.

## Quickstart & Installation

```bash
# 1. Create virtual environment (Python 3.14 or 3.12)
py -3.14 -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. First-Run Setup Wizard (Interactive standup)
importer setup
```

### Don't have a SQL Server instance yet?
If you don't have a SQL Server running, start a local container in 30 seconds with Docker:
```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=YourPassword123!" \
           -p 1433:1433 --name mssql-importer \
           -d mcr.microsoft.com/mssql/server:2022-latest
```
Then run `importer setup` and enter `localhost` / `sa` / `YourPassword123!`.

---

## Configuration & First-Run Standup

The `importer setup` command automates all first-run tasks:
1. **Verifies or creates** the control database on your SQL Server.
2. **Generates `database.env`** and ensures it is added to `.gitignore`.
3. **Marks the database environment** as `DEV`.
4. **Applies the authoritative schema** from `sql/001_schema.sql`.

### Interactive Password Prompting & Security

To avoid storing plain passwords on disk:
- **Interactive CLI:** If `UN` (username) is configured in `database.env` and `PW` is omitted, the CLI prompts for your SQL password interactively with `getpass` (input is not displayed). It keeps the password in session memory for the command run and never writes it to disk.
- **Unattended / MCP Mode:** MCP clients communicate with the server via `sys.stdin`/`sys.stdout` JSON-RPC. To prevent blocking the JSON-RPC pipe, supply the password via the `IMPORTER_PASSWORD` environment variable or in your MCP client's `"env"` block (see below).

### Manual Configuration (Optional)

1. **Option A: `database.env` file**
   Copy `database.env.example` to `database.env`:
   ```bash
   cp database.env.example database.env
   ```
   Configure your SQL Server connection:
   ```json
   # SQL Server Authentication:
   databases = '{"importer": {"type": "mssql", "server": "localhost", "database": "import_agent_dev", "UN": "sa", "PW": "YourPassword123!"}}'

   # Windows Integrated Authentication:
   databases = '{"importer": {"type": "mssql", "server": "localhost", "database": "import_agent_dev", "trusted": "yes"}}'
   ```

2. **Option B: Environment Variables**
   Set `IMPORTER_DATABASE_URL` in your environment or in `.env` (see `.env.example`):
   ```bash
   IMPORTER_DATABASE_URL="mssql+pyodbc:///?odbc_connect=DRIVER={ODBC Driver 18 for SQL Server};SERVER=<server>;DATABASE=<db>;Trusted_Connection=yes;TrustServerCertificate=yes;"
   ```

### Connecting Destination Databases

Destination databases (where your business data ultimately lives) are **read-only** to the importer. Register them for schema inspection:
```bash
importer config set destination <DEST_NAME> --server <server> --database <dest_db>
importer schema import "<DEST_NAME>.*.*"
```

## Onboarding unknown files with Claude

1. Unknown files are quarantined with a "looks like ..." proposal (`importer pending` lists them, and also
   warns about any file still quarantined under an already-bound signature, with its `importer retry` command).
2. Open Claude Code in this repo. `.mcp.json` starts `importer mcp`; approve the server once.
3. Run `/mcp__importer__onboard`. Claude profiles each file, drafts a mapping (or uses the deterministic
   reuse/exact draft), previews it, and shows you a review table.
4. Correct it in plain words, or approve. Approval is the `accept_mapping` prompt; it binds the signature and
   lands every waiting file. Never auto-approve `accept_mapping` or `register_schema`.

Files are read from their shared locator: `\\server\share\...`, an Azure Files share, or
`https://<account>.blob.core.windows.net/<container>/<name>` (your Entra identity; URLs with a SAS token are refused).

Claude only ever sees metadata and masked value shapes. Reading file contents any other way (shares, raw tables,
`importer preview`) prompts for your explicit permission (`importer.data_guard` hook in `.claude/settings.json`).
The guard is yours to control: set `IMPORTER_DATA_GUARD` in the `"env"` block of any Claude Code settings file
(e.g. `.claude/settings.local.json` or `~/.claude/settings.json`) to `auto` (default: only while an `importer mcp`
server is running on the machine), `on` (always) or `off` (never), e.g. `{"env": {"IMPORTER_DATA_GUARD": "off"}}`.


## Marketplace & MCP Client Configuration

This package implements an official **Model Context Protocol (MCP)** server that exposes tools and
prompts for intelligent, data-guarded file onboarding.

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "data-import-agent": {
      "command": "python",
      "args": ["-m", "importer.cli", "mcp"],
      "env": {
        "IMPORTER_HOME": "/path/to/repo_or_config_dir"
      }
    }
  }
}
```

### Claude Code

Included `.mcp.json`:
```json
{
  "mcpServers": {
    "importer": {
      "command": ".venv/Scripts/python",
      "args": ["-m", "importer.cli", "mcp"]
    }
  }
}
```

### Exposed MCP Capabilities

| Type | Name | Description |
|---|---|---|
| **Prompt** | `onboard` | Interactive prompt guiding Claude through discovering unknown signatures, inspecting profiles, proposing drafts, and seeking user review. |
| **Tool** | `list_pending` | List quarantined unknown file signatures awaiting mapping review. |
| **Tool** | `get_file_profile` | Retrieve structural file profile (headers, sheets, miss scores, value shapes). |
| **Tool** | `get_tree` | Get XML nesting tree structure (names and counts, never raw values). |
| **Tool** | `search_schemas` | Search registered destination schemas. |
| **Tool** | `get_schema` | Retrieve column definitions for a registered destination schema. |
| **Tool** | `get_prior_mappings` | View existing active mappings for inspiration or exact reuse. |
| **Tool** | `get_samples` | Inspect masked shape samples (e.g. `aaa-999`) for column values. |
| **Tool** | `get_spec_reference` | Full JSON schema and reference guide for mapping specifications. |
| **Tool** | `preview_mapping` | Dry-run a candidate mapping against real queued files; returns masked preview. |
| **Tool** | `submit_mapping` | Submit a candidate mapping spec version with recorded rationale. |
| **Tool** | `accept_mapping` | Gated confirmation: binds the accepted mapping to the signature and lands waiting files. |
| **Tool** | `reject_mapping` | Rejects an unknown file signature with a recorded reason. |
| **Tool** | `register_schema` | Gated confirmation: registers new destination schema definitions. |

## Data Privacy & Guard Boundary

The importer is built on a strict privacy invariant:
1. **Row values never reach a model:** Over MCP, only structural metadata (sheet names, column counts, missing column scores) and masked shapes (`masking.py`: letters -> `a`, digits -> `9`, fixed punctuation) are exchanged.
2. **Data Guard Hook:** The `PreToolUse` hook in `.claude/settings.json` (`importer.data_guard`) prevents tools from reading data shares or raw files directly outside masked MCP tools. Control via `IMPORTER_DATA_GUARD` (`auto`, `on`, `off`).

## Documentation & Architecture

Full architecture specifications and design records are maintained in [`docs/`](docs/):
- **Operator & User Guide (Detailed Manual):** [`INSTRUCTIONS.md`](INSTRUCTIONS.md)
- **Architecture & C4 Diagrams:** [`docs/architecture/`](docs/architecture/)
- **Superpowers Design Specs:** [`docs/superpowers/specs/`](docs/superpowers/specs/)
- **Implementation Plans:** [`docs/superpowers/plans/`](docs/superpowers/plans/)
- **Mapping Manual:** [`docs/mapping-manual.md`](docs/mapping-manual.md)
- **Control Plane DDL:** [`sql/001_schema.sql`](sql/001_schema.sql)

## License

This project is licensed under the [MIT License](LICENSE).

