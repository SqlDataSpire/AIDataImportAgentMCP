---
name: data-import-agent
description: Standup, first-run configuration, and operational guide for Data Import Agent MCP (SQL Server ELT).
---

# Data Import Agent MCP — Skill & Operational Guide

This skill guides you through setting up, configuring, and operating the **Data Import Agent MCP**.

> [!TIP]
> For the complete, authoritative manual covering environment promotion (DEV vs PROD), credential security models, and architecture deep dives, see [INSTRUCTIONS.md](file:///f:/Projects/AIDataImportAgentMCP/AIDataImportAgentMCP/INSTRUCTIONS.md).

---

## 1. What This System Does

The **Data Import Agent** is an ELT (Extract-Load-Transform) machine for landing structured and semi-structured files (CSV, Excel, JSON, NDJSON, XML) directly into Microsoft SQL Server:
- **Fast Path (Deterministic, 0 Tokens):** Known file signatures are routed instantly into raw SQL Server tables via high-performance batch staging (`fast_executemany`).
- **Slow Path (Agentic MCP):** Unrecognized files or unlandable rows trigger the MCP agent. The agent inspects masked shapes, matches destination table schemas, drafts declarative mappings, dry-runs with live previews, and submits versioned mapper artifacts for human approval.

---

## 2. Prerequisites & SQL Server Setup

The importer requires:
1. **Python 3.12 or 3.14** (Python 3.13 is not supported).
2. **Microsoft ODBC Driver 18 (or 17) for SQL Server**.
3. **A Microsoft SQL Server instance** (for the control database).

### "I don't have a SQL Server yet — what do I do?"
If you do not have a SQL Server instance running, choose one of these options:

#### Option A: Local Docker Container (Recommended for quick testing)
Run an official Microsoft SQL Server 2022 container in 30 seconds:
```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=YourStrongPassword123!" \
           -p 1433:1433 --name mssql-importer \
           -d mcr.microsoft.com/mssql/server:2022-latest
```
- **Server:** `localhost`
- **Database:** `import_agent_dev`
- **User:** `sa`
- **Password:** `YourStrongPassword123!`

#### Option B: Microsoft SQL Server Express (Free)
Download and install Microsoft SQL Server Express (with LocalDB or a named instance):
[SQL Server Downloads](https://www.microsoft.com/sql-server/sql-server-downloads)

#### Option C: Cloud SQL Database
Azure SQL Database or AWS RDS for SQL Server.

---

## 3. First-Run Standup (`importer setup`)

The project includes an interactive setup wizard that verifies connectivity, creates the database if it doesn't exist, configures `database.env`, marks the environment, and deploys the control schema.

### Interactive Wizard
In your project virtual environment, run:
```bash
importer setup
```

The wizard will guide you through:
1. **SQL Server instance check:** Offers the Docker command if you don't have a server running.
2. **Connection details:** Host, database name (e.g. `import_agent_dev`), authentication mode (SQL Auth, Windows Trusted, or Entra ID).
3. **Password handling:**
   - Prompts for password securely without echoing to the screen.
   - Asks whether to save the password to disk (`database.env`) or keep it in session/environment only.
4. **Database provisioning:** If the database does not exist on the server, offers to create it automatically (`CREATE DATABASE [<db>]` on `master`).
5. **Schema deployment:** Initializes the environment as `DEV` and applies `sql/001_schema.sql`.
6. **MCP instructions:** Displays the exact JSON configuration block to copy into Claude Desktop, Cursor, or Antigravity.

### Scripted / Non-Interactive Standup
For automation, CI/CD, or headless scripts:
```bash
importer setup --server localhost --database import_agent_dev --auth sql -u sa -p "YourStrongPassword123!" --create-db --non-interactive
```

---

## 4. Password Security & Unattended / MCP Execution

### Interactive CLI Mode
If `UN` (username) is defined in `database.env` but `PW` is omitted:
- Whenever an interactive CLI command runs (`importer process`, `importer preview`, `importer db ...`), it prompts:
  `Enter SQL Server password for <user>@<server>:`
- The password is read securely via Python's standard `getpass` module and cached in memory for that process session only (`_SESSION_PASSWORD`).
- **The password is never written to disk.**

### Unattended / MCP Mode (`importer mcp`)
MCP servers communicate with AI clients (Claude Desktop, Claude Code, Cursor, Antigravity) via JSON-RPC over standard input/output (`sys.stdin` / `sys.stdout`).
- An MCP server **cannot prompt on `sys.stdin`** without corrupting the JSON-RPC message framing and hanging the client.
- To run unattended or over MCP without storing plaintext passwords in `database.env`, provide the password via the `IMPORTER_PASSWORD` environment variable.

#### Configuring MCP Clients:

**Option 1: In Client MCP Settings (Claude Desktop / Cursor / Antigravity)**
```json
{
  "mcpServers": {
    "importer": {
      "command": "F:/Projects/AIDataImportAgentMCP/AIDataImportAgentMCP/.venv/Scripts/python.exe",
      "args": ["-m", "importer.cli", "mcp"],
      "env": {
        "IMPORTER_PASSWORD": "YourStrongPassword123!"
      }
    }
  }
}
```

**Option 2: In Shell Environment (PowerShell)**
```powershell
$env:IMPORTER_PASSWORD = "YourStrongPassword123!"
importer mcp
```

**Option 3: Stored in `database.env`**
If you choose to store the password in `database.env`:
- Ensure `database.env` is in `.gitignore` (the setup wizard automatically checks and ensures this).
- Restrict file permissions on `database.env`.

---

## 5. Architecture: Control vs. Destination Databases

Understanding the boundary between databases is critical:

| Database | Role | Read/Write Policy |
| :--- | :--- | :--- |
| **Control Database** (`import_agent_dev`) | Ledger, audit trail, versioned mappers, signatures, rejects, quarantined files. | **Full Read/Write.** Managed exclusively by `importer`. |
| **Destination Database(s)** (e.g. `APP_DB`) | Your enterprise tables where data ultimately belongs. | **READ-ONLY.** Guarded at the engine level (`read_only`). The importer NEVER writes to destination databases. It only reads `sys.tables` and `sys.columns` during schema import. |

> [!NOTE]
> **Landing schemas are created strictly on-demand, never pre-inserted:**
> The control database starts with only the core control tables (`dbo.file_ledger`, `dbo.artifact_versions`, etc.). Raw landing tables (`[<dest_db>_<dest_schema>].[<table>]`) and staging tables are dynamically generated only when the user or agent imports/registers destination schemas (via `register_schema` or `importer schema import`). If no schemas have been imported yet, zero landing schemas exist.

---

## 6. End-to-End Operational Workflow

### Step 1: Register Destination Database
Tell the importer where your target tables live:
```bash
importer config set destination my_app --server localhost --database my_app_db
```

### Step 2: Import Destination Table Schema
Pull column definitions into the control database's versioned registry:
```bash
# Live extraction from SQL Server:
importer schema import my_app.dbo.customers

# Or import from a column definition file:
importer schema import my_app.dbo.customers --from-file ./schema.json
```

### Step 3: Fast-Path Ingestion (Production Run)
When an incoming file arrives and its layout has an active signature:
```bash
importer process ./incoming/customers_20261019.csv \
  --project CRM \
  --class CUSTOMER \
  --type CSV \
  --source sftp_feed \
  --context run_id=101 batch_date=2026-10-19
```
Output:
```json
{
  "status": "completed",
  "rows_landed": 500,
  "rows_rejected": 0,
  "percent_loaded": "100.00"
}
```

### Step 4: Slow-Path Edge Onboarding (New / Unknown Files)
When a file arrives with an unknown header or structure, it is safely quarantined:
```json
{
  "status": "quarantined",
  "quarantine_reason": "unroutable",
  "file_id": 42
}
```

To resolve it with your AI assistant:
1. Run `importer mcp` (or open your MCP-enabled editor/chat).
2. The agent uses the MCP tool suite:
   - `list_pending`: Lists quarantined files.
   - `get_file_profile`: Inspects header names, sample row counts, and structural characteristics (values are masked; real data never leaves SQL Server).
   - `search_schemas`: Finds matching destination tables.
   - `preview_mapping`: Dry-runs a proposed mapping and checks column alignments.
   - `submit_mapping`: Saves the proposed mapping draft.
   - `accept_mapping`: User signs off on the mapping (human-in-the-loop gate).
3. Re-enter the quarantined file:
```bash
importer retry 42
```
The file re-enters the machine, matches the newly approved signature, and lands raw!

---

## 7. Useful Reference Commands

| Command | Description |
| :--- | :--- |
| `importer setup` | Interactive standup wizard. |
| `importer mcp` | Launch MCP server over stdio for AI assistants. |
| `importer pending` | View files waiting for onboarding. |
| `importer config show` | View all shared settings stored in the database. |
| `importer config set samples.max_values 100` | Adjust sampling thresholds in the database. |
| `importer db init --environment DEV --confirm <db>` | Mark database environment (once). |
| `importer db rebuild --confirm <db>` | DEV only: Drop and re-deploy schema from `sql/001_schema.sql`. |
| `pytest` | Run complete unit and property test suites (364 tests). |
| `pytest -m integration` | Run live SQL Server integration tests against DEV database. |
