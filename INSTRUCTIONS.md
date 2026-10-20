# Data Import Agent MCP: Operator & User Guide

Welcome to the **Data Import Agent MCP** comprehensive operator manual. This guide covers the complete lifecycle of the import agent: architecture, first-run standup, credential security, environment management (DEV vs. PROD), AI-assisted onboarding with Model Context Protocol (MCP), and day-to-day operations.

---

## Table of Contents

1. [Architecture & Database Roles](#1-architecture--database-roles)
2. [Prerequisites & System Requirements](#2-prerequisites--system-requirements)
3. [Installation & First-Run Setup](#3-installation--first-run-setup)
4. [Authentication & Credential Security](#4-authentication--credential-security)
5. [Environment Management: DEV vs. PROD](#5-environment-management-dev-vs-prod)
6. [Promoting Mappings from DEV to PROD](#6-promoting-mappings-from-dev-to-prod)
7. [Operational Workflows](#7-operational-workflows)
   - [Workflow 1: Registering Destination Databases & Schemas](#workflow-1-registering-destination-databases--schemas)
   - [Workflow 2: Fast-Path Automated File Ingestion](#workflow-2-fast-path-automated-file-ingestion)
   - [Workflow 3: AI-Assisted Onboarding via MCP](#workflow-3-ai-assisted-onboarding-via-mcp)
   - [Workflow 4: Reprocessing & Reclaiming Data](#workflow-4-reprocessing--reclaiming-data)
8. [MCP Client Configuration (Antigravity & Claude)](#8-mcp-client-configuration-antigravity--claude)
9. [CLI Command Reference](#9-cli-command-reference)
10. [Troubleshooting & FAQs](#10-troubleshooting--faqs)

---

## 1. Architecture & Database Roles

The system uses an **ELT (Extract, Load, Transform)** pattern. Instead of running transformations before landing, incoming data lands raw in a dedicated SQL Server database, leaving downstream coercion, validation, and curation to subsequent stages.

```
                      +---------------------------------------+
                      |       INCOMING DATA SOURCES           |
                      | (CSV, TSV, Excel, JSON, NDJSON, XML)  |
                      +---------------------------------------+
                                          |
                                          v
+---------------------------------------------------------------------------------+
|                       CONTROL DATABASE (e.g. import_agent_dev)                   |
|                                                                                 |
|  [dbo] Control Plane (Never pre-inserted with user landing tables):             |
|    - dbo.database_identity    (Environment marker: DEV | PROD)                  |
|    - dbo.artifact_versions    (Versioned schemas, mapping specs, configurations)|
|    - dbo.signature_registry   (Fingerprint to mapper bindings)                  |
|    - dbo.file_ledger          (Audit trail of all discovered files)             |
|    - dbo.reject_repository    (Unparseable or unmapped rows)                    |
|    - dbo.file_quarantine      (Unroutable files waiting for onboarding)         |
|    - dbo.decision_log         (Complete AI agent & human decision audit log)    |
|                                                                                 |
|  [<dest_db>_<dest_schema>] Raw Landing Zone (Dynamically created on demand):   |
|    - Holds raw ingested rows with lineage (_file_id, _row_identity, etc.)       |
|                                                                                 |
|  [stage] Staging Zone (Dynamically created on demand):                          |
|    - High-throughput landing buffer before transactional promotion              |
+---------------------------------------------------------------------------------+
                                          |
                                          | (READ-ONLY Schema Inspection)
                                          v
+---------------------------------------------------------------------------------+
|                  DESTINATION DATABASES (e.g. APP_DB, WAREHOUSE)                 |
|                                                                                 |
|  Your enterprise tables. The importer NEVER writes to destination databases!    |
|  Protected by an engine-level read-only guard (SELECT statements only).          |
+---------------------------------------------------------------------------------+
```

### Key Architectural Invariants

1. **Two Distinct Database Roles**:
   - **Control Database**: Dedicated to the importer. Holds the ledger, audit trail, versioned mappers, and the raw landing tables (`[<db>_<schema>].[<table>]`).
   - **Destination Database**: Your business database where data belongs. The importer connects to it with a **strict read-only guard** solely to discover table definitions.
2. **Landing Schemas Are Never Pre-Inserted**:
   - The control database starts with **only** the 7 core `dbo` tables.
   - Raw landing tables (`[<dest_db>_<dest_schema>].[<table>]`) are dynamically created only when you capture destination schemas and land data into them. If you have not registered any schemas, zero landing tables exist.
3. **Data Boundary Privacy Guard**:
   - AI models connected via MCP never see raw file contents.
   - They only receive structural metadata (column names, counts) and **masked character shapes** (e.g., `user@domain.com` becomes `aaaa@aaaaaa.aaa`, `TX12345` becomes `aa99999`).

---

## 2. Prerequisites & System Requirements

### Host Environment
- **Operating System**: Windows 10/11 / Windows Server, Linux (Ubuntu 20.04+, Debian, RHEL), or macOS.
- **Python**: Version **3.10** or higher (tested up to Python 3.14).
- **Microsoft ODBC Driver**:
  - Windows: **ODBC Driver 18 for SQL Server** (downloadable from Microsoft).
  - Linux: `msodbcsql18` (via Microsoft package repositories).
  - macOS: `brew install msodbcsql18`.

### SQL Server Instance
You need access to a Microsoft SQL Server instance (2016 or newer, Azure SQL Database, or SQL Server Express). If you do not have one ready, run a local developer container:

```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=YourPassword123!" \
           -p 1433:1433 --name mssql-importer \
           -d mcr.microsoft.com/mssql/server:2022-latest
```

---

## 3. Installation & First-Run Setup

### Step 1: Clone and Set Up Virtual Environment

```bash
git clone <your-repo-url>
cd AIDataImportAgentMCP

# Create virtual environment
python -m venv .venv

# Activate virtual environment:
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Install dependencies in editable mode
pip install -e ".[dev]"
```

### Step 2: Run the First-Run Setup Wizard

The interactive wizard tests your connection, creates the control database if missing, generates your configuration, marks the database environment, and applies the base schema:

```bash
importer setup
```

The wizard prompts you for:
1. SQL Server host (`localhost`, `10.0.0.5`, `sql1.corp.local`).
2. Authentication mode (Windows Integrated vs. SQL Server Authentication).
3. Database name (default: `import_agent_dev`).
4. Password storage preference (interactive memory vs. environment variable vs. stored in `database.env`).

Once complete, the wizard confirms connection and reports the environment identity.

---

## 4. Authentication & Credential Security

The importer provides 5 ways to handle SQL Server authentication, allowing you to comply with strict security policies:

| Method | Best For | Security Level | Plaintext on Disk? |
| :--- | :--- | :--- | :--- |
| **Interactive Prompt** | Local interactive CLI runs | **Highest** | **No** (Stored in RAM only for session) |
| **`IMPORTER_PASSWORD` Env Var** | CI/CD pipelines & background tasks | **High** | **No** |
| **Windows Authentication** | Enterprise Windows domain environments | **Highest** | **No** (uses Kerberos/NTLM token) |
| **Stored in `database.env`** | Local developer convenience | **Moderate** | **Yes** (file in `.gitignore`) |
| **`IMPORTER_DATABASE_URL`** | 12-factor apps & container deployments | **High** | **No** (set via environment) |

### 1. Interactive Password Prompting (Recommended for CLI)
In `database.env`, provide `UN` (username) but leave `PW` out:
```json
databases = '{"importer": {"type": "mssql", "server": "sql1", "database": "import_agent_dev", "UN": "sa"}}'
```
When you run any `importer` command, the CLI securely prompts:
```text
Enter password for SQL Server user 'sa' on 'sql1': 
```
The password is kept in memory during the execution and is never written to disk or logs.

### 2. Environment Variable
Provide the password via environment variable:
```bash
# Windows PowerShell:
$env:IMPORTER_PASSWORD="YourPassword123!"

# Linux / macOS:
export IMPORTER_PASSWORD="YourPassword123!"
```

### 3. Windows Integrated Authentication
If running inside an Active Directory / Windows domain with access to the SQL Server:
```json
databases = '{"importer": {"type": "mssql", "server": "sql1", "database": "import_agent_dev", "trusted": "yes"}}'
```

---

## 5. Environment Management: DEV vs. PROD

The importer tracks environment boundaries **inside the database itself**, in `dbo.database_identity`:

```sql
SELECT environment, created_at, created_by FROM dbo.database_identity WHERE id = 1;
```

### DEV vs. PROD Safeguards

| Feature / Command | DEV Environment | PROD Environment |
| :--- | :--- | :--- |
| `importer db init` | `--environment DEV` | `--environment PROD` |
| `importer db rebuild` | **Allowed** (requires typing database name) | **BLOCKED** (`dev_guard` refuses) |
| `pytest -m integration` | **Allowed** (runs integration tests) | **BLOCKED** (skips execution) |
| `importer process` (Ingestion) | **Allowed** | **Allowed** |
| `importer mcp` (Agent Server) | **Allowed** | **Allowed** |
| `importer schema import` | **Allowed** | **Allowed** |

### How to Stand Up a Production Control Database

1. Create an empty database in your production SQL Server (e.g. `data_import_prod`).
2. Deploy the core schema:
   Execute [`sql/001_schema.sql`](sql/001_schema.sql) in SQL Server Management Studio (SSMS), Azure Data Studio, or via `sqlcmd`.
3. Mark the database identity as `PROD`:
   ```bash
   importer db init --environment PROD
   ```
4. Verify the banner:
   ```bash
   importer config show
   ```
   Output: `importer @ prod-sql.corp/data_import_prod [PROD]`

From that moment on, all destructive commands (`db rebuild`) are permanently blocked by `dev_guard`.

---

## 6. Promoting Mappings from DEV to PROD

Once you have onboarded an unknown file in your DEV environment and confirmed that its mapping spec works cleanly, use one of the following promotion workflows:

### Method A: Promoting Versioned Mapping Specs (Recommended)

1. **Export the reviewed spec**:
   Save your mapping spec to a version-controlled JSON file (e.g. `specs/customers_csv_v1.json`).
2. **Point your environment to the PROD database**:
   Update `database.env` or set `IMPORTER_DATABASE_URL` to your production control database.
3. **Register the destination and schema in PROD**:
   ```bash
   importer config set destination APP_DB --server prod-db.corp --database APP_DB
   importer schema import APP_DB.dbo.customers
   ```
4. **Register the mapper and bind the signature in PROD**:
   ```bash
   importer mapper add specs/customers_csv_v1.json --key customers_mapper
   importer signature register "\\fileserver\drop\sample.csv" \
       --project CRM --class CUST --type CSV --mapper-version 1
   ```

### Method B: Migrating Control Tables

If you have tens or hundreds of approved signatures and mappers in DEV:
1. Export rows from the metadata tables in DEV:
   - `dbo.artifact_versions` (where `artifact_type IN ('schema', 'mapper', 'rule')`)
   - `dbo.signature_registry` (fingerprints and bindings)
2. Insert them into the PROD control database.
3. Keep transaction history fresh in PROD:
   - Do **not** copy `dbo.file_ledger`, `dbo.file_quarantine`, or `dbo.reject_repository`.

---

## 7. Operational Workflows

### Workflow 1: Registering Destination Databases & Schemas

The importer needs to know what your target tables look like.

1. **Register the Destination Database**:
   ```bash
   importer config set destination APP_DB --server sql1 --database APP_DB
   ```
2. **Import Schemas**:
   ```bash
   # Live import: inspects SQL Server sys.tables and sys.columns directly (read-only)
   importer schema import "APP_DB.dbo.customers"

   # Wildcard import: imports all tables in a schema
   importer schema import "APP_DB.dbo.*"

   # Offline import: import from a column list JSON file
   importer schema import APP_DB.dbo.customers --from-file ./customer_columns.json
   ```

---

### Workflow 2: Fast-Path Automated File Ingestion

When a file matches a known, active signature, it executes the deterministic fast path (no AI required):

```bash
importer process "\\fileserver\incoming\customers_20261019.csv" \
    --project CRM \
    --class CUST \
    --type CSV \
    --source sftp_feed \
    --context run_id=5001 batch_date=2026-10-19
```

Output:
```json
{
  "file_id": 142,
  "status": "completed",
  "percent_loaded": 100.0,
  "rows_total": 5000,
  "rows_landed": 5000,
  "rows_rejected": 0
}
```

Raw data lands in `[app_db_dbo].[customers]` with lineage metadata:
- `_file_id`: Links back to `dbo.file_ledger`.
- `_row_identity`: Line number or XPath identity.
- `_landed_at`: UTC timestamp.
- `_signature_id`: The signature used.
- `_artifact_version_id`: The mapper version used.
- `_extras`: Any unmapped columns captured losslessly as JSON.

---

### Workflow 3: AI-Assisted Onboarding via MCP

When an unknown file layout arrives:
1. **The file is quarantined**:
   `importer process` marks it `QUARANTINED` with reason `unroutable` and generates a structural fingerprint.
2. **Start or connect your AI Agent**:
   Connect Antigravity, Claude Desktop, or Claude Code to the MCP server (`importer mcp`).
3. **The Agent executes the onboarding sequence**:
   - `list_pending`: Discovers the waiting file.
   - `get_file_profile`: Inspects header columns, sheets, or XML root tags.
   - `get_samples`: Inspects masked value shapes (e.g., `aaaaa://...`).
   - `search_schemas`: Finds matching destination tables.
   - `preview_mapping`: Dry-runs candidate mappings against the real file.
   - `submit_mapping`: Submits a versioned mapping spec with explicit rationale.
   - `accept_mapping`: **Requires human sign-off**. Binds the signature and automatically reprocesses the waiting file to `completed`.

---

### Workflow 4: Reprocessing & Reclaiming Data

- **Check Stranded Files**:
  ```bash
  importer pending
  ```
  If a file arrived while its signature was being onboarded, it prints the retry command.
- **Retry a Quarantined File**:
  ```bash
  importer retry <file_id>
  ```
- **Idempotent Reclaim**:
  If rows were rejected into `dbo.reject_repository` due to a missing mapping rule and you add a repair rule:
  ```bash
  importer reclaim APP_DB.dbo.customers
  ```
  Reclaim moves repairable rows from reject into landed without re-ingesting the whole file.

---

## 8. MCP Client Configuration (Antigravity & Claude)

### Claude Code (.mcp.json)
The project includes `.mcp.json` out of the box:
```json
{
  "mcpServers": {
    "importer": {
      "command": ".venv/Scripts/python",
      "args": ["-m", "importer.cli", "mcp"],
      "env": {
        "IMPORTER_PASSWORD": "YourPasswordHere"
      }
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "data-import-agent": {
      "command": "F:\\Projects\\AIDataImportAgentMCP\\AIDataImportAgentMCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "importer.cli", "mcp"],
      "env": {
        "IMPORTER_HOME": "F:\\Projects\\AIDataImportAgentMCP\\AIDataImportAgentMCP",
        "IMPORTER_PASSWORD": "YourPasswordHere"
      }
    }
  }
}
```

### Antigravity IDE
In Antigravity IDE, register the MCP server in your workspace configuration or `mcp_config.json`:
```json
{
  "mcpServers": {
    "data-import-agent": {
      "command": "python",
      "args": ["-m", "importer.cli", "mcp"]
    }
  }
}
```

---

## 9. CLI Command Reference

| Command | Usage | Description |
| :--- | :--- | :--- |
| `importer setup` | `importer setup` | Interactive first-run wizard. |
| `importer db init` | `importer db init --environment DEV\|PROD` | Marks the control database environment. |
| `importer db rebuild` | `importer db rebuild --confirm <dbname>` | **DEV ONLY**: Drops all tables and restores initial schema. |
| `importer process` | `importer process <path> --project <P> --class <C> --type <T>` | Ingests a file through the pipeline. |
| `importer pending` | `importer pending` | Lists quarantined files awaiting mapping. |
| `importer retry` | `importer retry <file_id>` | Re-runs a quarantined file after binding. |
| `importer config show` | `importer config show` | Displays active connection, banner, and destinations. |
| `importer config set destination` | `importer config set destination <NAME> --server <S> --database <D>` | Registers a destination database. |
| `importer schema import` | `importer schema import <address>` | Captures table schema definitions. |
| `importer mapper add` | `importer mapper add <spec.json> --key <name>` | Registers a mapping specification version. |
| `importer signature register` | `importer signature register <sample> --project <P> --class <C> --type <T> --mapper-version <V>` | Binds a physical file fingerprint to a mapper version. |
| `importer reclaim` | `importer reclaim <target_address>` | Reprocesses rejects after a repair rule is added. |
| `importer mcp` | `importer mcp` | Launches the Model Context Protocol (MCP) server over stdin/stdout. |

---

## 10. Troubleshooting & FAQs

### Q: Why do I get `database is not a configured destination` when registering a schema?
**A**: Destination databases must be declared before importing tables from them. Run:
```bash
importer config set destination APP_DB --server your_server --database APP_DB
```

### Q: Why did `importer db rebuild` fail with `database name does not contain 'dev'`?
**A**: `db rebuild` is destructive (it wipes the entire control database). As a safeguard, it refuses to run unless `dbo.database_identity.environment = 'DEV'` **and** the database name contains the string `dev`.

### Q: Can my destination database be on a different server than the control database?
**A**: **Yes!** When you configure a destination with `importer config set destination <NAME> --server <server_name> --database <db_name>`, you can point to any reachable SQL Server. The importer connects directly to it in read-only mode.

### Q: How do I know if an incoming file is quarantined?
**A**: Run `importer pending` or check `dbo.file_quarantine`. The CLI and MCP tools display the file ID, fingerprint, and a proposal detailing what schema the file most closely resembles.

### Q: How can I run integration tests?
**A**: Integration tests run against your live DEV SQL Server using the credentials in `database.env`:
```bash
pytest -m integration
```
(Unit tests run completely in-memory with `pytest -m "not integration"`).
