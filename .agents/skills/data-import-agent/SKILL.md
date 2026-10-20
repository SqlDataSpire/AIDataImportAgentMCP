---
name: data-import-agent
description: Standup, first-run configuration, and operational guide for Data Import Agent MCP (SQL Server ELT).
---

# Data Import Agent MCP — Skill & Operational Guide

This skill guides you through configuring, operating, and facilitating imports with the **Data Import Agent MCP**.

> [!TIP]
> For the complete, authoritative manual covering environment promotion (DEV vs PROD), credential security models, and architecture deep dives, see [INSTRUCTIONS.md](file:///f:/Projects/AIDataImportAgentMCP/AIDataImportAgentMCP/INSTRUCTIONS.md).

---

## 1. What This System Does

The **Data Import Agent** is an ELT (Extract-Load-Transform) machine for landing structured and semi-structured files (CSV, Excel, JSON, NDJSON, XML) directly into Microsoft SQL Server:
- **Fast Path (Deterministic, 0 Tokens):** Known file signatures are routed instantly into raw SQL Server tables via high-performance batch staging (`fast_executemany` with automatic driver buffer fallback).
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

## 3. Configuration & Python-DataEngine Integration

The importer connects to SQL Server using SQLAlchemy over pyodbc or `python-DataEngine`.

### No Need for `database.env` or `alchemyObjects` Array
`python-DataEngine` does **not** require the legacy `alchemyObjects[X]` array or a complex JSON document in `database.env`. `DataEngine.mssql.SqlConnectionObject` directly accepts parameters:
```python
SqlConnectionObject(name="importer", server=server, database=database, UN=user, PW=password, trusted=trusted)
```

The importer resolves connection parameters seamlessly in this priority order:
1. **MCP Client / CLI Flags:** `--server`, `--database`, `--trusted`, `--user`, `--password`, `--url`
2. **Standard Environment Variables:** `IMPORTER_SERVER`, `IMPORTER_DATABASE`, `IMPORTER_USER`, `IMPORTER_PASSWORD`, `IMPORTER_TRUSTED`, `IMPORTER_DATABASE_URL`
3. **Flat `.env` File in Workspace:**
   ```env
   SERVER=localhost
   DATABASE=import_agent_dev
   TRUSTED=yes
   ```
4. **Legacy `database.env` JSON:** Kept for backwards compatibility (`databases='{"importer": {...}}'`).

---

## 4. MCP Client Setup & Session Password Facilitation

### Configuring MCP Clients (`.mcp.json`, Claude Desktop, Cursor, Antigravity)

#### Configuration Pattern A: Windows / Integrated Authentication (No Passwords)
```json
{
  "mcpServers": {
    "importer": {
      "command": "python",
      "args": [
        "-m", "importer.cli", "mcp",
        "--server", "localhost",
        "--database", "import_agent_dev",
        "--trusted"
      ]
    }
  }
}
```

#### Configuration Pattern B: SQL Authentication with In-Chat Session Password (Recommended)
You do **not** need to put passwords in `.mcp.json` or commit them to disk! Simply specify the username:
```json
{
  "mcpServers": {
    "importer": {
      "command": "python",
      "args": [
        "-m", "importer.cli", "mcp",
        "--server", "sqldb.internal",
        "--database", "import_agent_dev",
        "-u", "sa"
      ]
    }
  }
}
```

### The AI Agent Session Password Flow (UN/PW Connections)

Whenever a chat, project, or session starts:
1. **Status Check:** The AI agent calls `get_connection_status`.
2. **Chat Prompt:** If SQL Authentication is used and `authenticated: false`, the AI agent directly asks the user in chat:
   > *"Your SQL Server connection for user `<username>` on `<server>` requires authentication for this session. What is your password?"*
3. **In-Memory Authentication:** When the user replies with their password in chat, the AI agent calls:
   ```json
   authenticate(password="...")
   ```
4. **Zero-Disk Leakage:** The password is kept strictly in-memory in the active Python process. It is **never** written to `database.env`, `.mcp.json`, or logged to `dbo.decision_log`.
5. **Clean JSON-RPC:** This keeps the stdio JSON-RPC stream 100% clean, avoiding process hangs and input blocking.

---

## 5. First-Run Standup (`importer setup`)

For automated database creation and schema initialization:

### Interactive Wizard
```bash
importer setup
```
The wizard checks connectivity, creates the database if it doesn't exist (`CREATE DATABASE`), initializes the environment as `DEV`, and applies `sql/001_schema.sql`.

### Scripted Standup
```bash
importer setup --server localhost --database import_agent_dev --auth sql -u sa -p "YourStrongPassword123!" --create-db --non-interactive
```

---

## 6. Architecture: Control vs. Destination Databases

Understanding the boundary between databases is critical:

| Database | Role | Read/Write Policy |
| :--- | :--- | :--- |
| **Control Database** (`import_agent_dev`) | Ledger, audit trail, versioned mappers, signatures, rejects, quarantined files. | **Full Read/Write.** Managed exclusively by `importer`. Also serves as the default implicit target. |
| **Destination Database(s)** (e.g. `APP_DB`) | Your enterprise tables where data ultimately belongs. | **READ-ONLY.** Guarded at the engine level (`read_only`). The importer NEVER writes to destination databases. It only reads `sys.tables` and `sys.columns` during schema import. |

> [!NOTE]
> **Landing schemas are created strictly on-demand, never pre-inserted:**
> The control database starts with only the core control tables (`dbo.file_ledger`, `dbo.artifact_versions`, etc.). Raw landing tables (`[<dest_db>_<dest_schema>].[<table>]`) and staging tables are dynamically generated only when the user or agent imports/registers destination schemas (via `register_schema` or `importer schema import`).

---

## 7. End-to-End Operational Workflow (Chat & CLI)

### 1. Introduce Incoming Files (`ingest_file`)
Users can introduce incoming files directly from chat:
- Call `ingest_file(path="path/to/data.csv", project="CRM", cls="CUSTOMER")`.
- If a known signature matches, the file lands immediately.
- If unrecognized or drifted, it is quarantined and listed in `list_pending`.

### 2. Inspect Profile & Candidates
- Call `list_pending` to see waiting quarantined files.
- Call `get_file_profile(signature_id=...)` to inspect headers, row counts, and structural shape (real values are masked; raw data never reaches the LLM).

### 3. Match Destination Tables & Draft Spec
- Call `search_schemas(columns=[...])` or `register_schema(address_or_pattern="APP_DB.dbo.customers")`.
- Draft a declarative mapping spec adhering to `get_spec_reference`.
- Call `preview_mapping(signature_id=..., spec=...)` to test column alignments and see sample masked output.

### 4. Human-in-the-Loop Sign-Off
- Call `submit_mapping(signature_id=..., spec=..., rationale=[...])`.
- Present the review table to the user in chat.
- When the user confirms approval in chat, call `accept_mapping(signature_id=..., draft_version_id=..., user_confirmed=True)`.
  *(Passing `user_confirmed=True` ensures approval proceeds reliably across all MCP clients, even those lacking interactive form elicitation).*

---

## 8. MCP Tool Reference

| Tool | Purpose | Annotations |
| :--- | :--- | :--- |
| `get_connection_status` | Check active server, database, user, and authentication state. | Read-Only |
| `authenticate` | Supply SQL Server password for the current session in-memory. | Writes |
| `ingest_file` | Introduce incoming files to the pipeline from chat. | Writes |
| `register_destination` | Register destination database alias (server/database) in configuration. | Writes |
| `list_pending` | List quarantined files awaiting mapping. | Read-Only |
| `get_file_profile` | Structural profile of a quarantined file (masked). | Read-Only |
| `search_schemas` | Find destination schemas by column overlap or name. | Read-Only |
| `get_schema` | Inspect column definitions for a registered schema address. | Read-Only |
| `get_prior_mappings` | Retrieve existing approved specs feeding a destination schema. | Read-Only |
| `preview_mapping` | Dry-run a spec against waiting file rows. | Read-Only |
| `submit_mapping` | Submit a draft spec with column rationales for review. | Writes |
| `accept_mapping` | Sign-off: bind draft to signature and retry quarantined files (`user_confirmed=True`). | Sign-Off |
| `reject_mapping` | Reject proposed mapping. | Writes |
| `register_schema` | Sign-off: register live destination tables or explicit column list. | Sign-Off |
