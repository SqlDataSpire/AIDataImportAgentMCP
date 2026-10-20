# Architecture Review: AIDataImportAgentMCP

**Date:** 2026-10-20 · **Inputs:** c4-facts.json, review-evidence.json, README.md, INSTRUCTIONS.md, sql/001_schema.sql, src/importer/pipeline.py, src/importer/mssql_store.py, src/importer/db.py, src/importer/setup_wizard.py, src/importer/schema_tables.py, src/importer/readers/delimited.py, Dockerfile, smithery.yaml, .github/workflows/ci.yml, .github/workflows/release.yml

## Executive summary

Data Agent: Importer is a well-structured ELT state-machine system operating over SQL Server, featuring a deterministic fast path for known files and an MCP-guided slow path for onboarding unrecognized formats. The core architectural invariant—strict data isolation through masking preview data and protecting production environments with database-level guards—is soundly enforced in code. The most significant architectural limitations center on schema lifecycle management, where control-plane tables rely on in-place DDL and drop/rebuild scripts without an incremental migration tool for non-DEV environments, and the persistence of plaintext database credentials on local disk. Priority actions are to introduce versioned migration tracking for control-plane upgrades, establish integrated/token-based credential options, and add preflight connectivity health probes for containerized MCP execution.

## Goals and quality attributes

The primary business goal is reliable, auditable ingestion of heterogeneous data files (CSV, TSV, Excel, JSON, NDJSON, XML) into SQL Server landing zones with minimal operator intervention. The quality attributes that matter most are:
- **Data Integrity and Ledger Auditability (explicit in design record):** Every file processing step must be recorded in an immutable ledger with monotonic parsing/loading metrics and idempotent reject handling.
- **Security & Data Confidentiality (explicit in design and README):** Incoming raw data must never leave the execution environment unmasked, and LLM clients must only see structural shapes.
- **Operability & Safety (explicit in environment guards):** Destructive operations (such as table drops or schema wipes) must be programmatically impossible against non-DEV databases.
- **Changeability (inferred):** The system must allow adding new mappers, shapers, and format readers without modifying core state-machine pipeline logic.

## Scope and inputs

The review covers the entire repository: the `importer` Python package (`src/importer/`), the SQL Server DDL (`sql/001_schema.sql`), containerization assets (`Dockerfile`, `smithery.yaml`), CI/CD workflows (`.github/workflows/`), and architectural models (`docs/architecture/c4/c4-facts.json`, `c4-model.json`). The review is static; live database connections were inspected during prior simulation tests but runtime memory utilization was inferred from code structure.

## Overall architecture

The system is deployed as a single containerized deployable exposing two primary entry points: the `importer` CLI for batch execution and administrative tasks, and `importer mcp`, a stdio JSON-RPC server enabling AI assistants (such as Claude Desktop or Cursor) to diagnose unknown files and propose mapping specifications. The system depends on a central SQL Server control database (`import_agent_dev` in development) that stores the state-machine ledger, schema registry, versioned artifact definitions, and dynamically created landing tables (`raw.*` and `stage.*`).

External dependencies include destination operational/warehouse databases (accessed strictly via SELECT-only SQLAlchemy engines with SQL execution listeners), file shares (SMB/Azure Files), and Azure Blob storage. Trust boundaries are cleanly delineated: incoming files remain quarantined until structural signatures match registered artifacts, and data boundaries are enforced by `src/importer/masking.py`, which strips raw values to character masks (`a` for letters, `9` for digits) before any preview reaches the MCP stream.

## Analysis by lens

### Security

The data boundary design is exemplary: the MCP server communicates over standard I/O, and row-level previews are irreversibly masked to abstract data types before transmission to the model, preventing data leakage. Furthermore, destination warehouse engines are wrapped in execution listeners (`src/importer/db.py:41-50`) that actively intercept and reject any non-SELECT statements. The primary security exposure is credential persistence (F2): database connection strings with passwords are saved in plaintext `database.env` files on disk, and documentation examples in `smithery.yaml:8` expose standard credential URL formats.

### Data

The state-machine design provides deterministic idempotency and lineage tracking through surrogate keys (`_file_id`, `_signature_id`, `_artifact_version_id`) on all landing tables. However, schema management has a clear maintenance debt (F1): the repository lacks an incremental database migration framework. While development databases can be dropped and rebuilt via `importer db rebuild`, upgrading a staging or production control database requires manual DDL delta scripts.

### Application

The application structure strictly adheres to layered unidirectional dependencies: entry points depend on onboarding and pipeline modules, which in turn depend on readers, shapers, and repository protocols. No circular dependencies exist. However, input readers (F4) read entire file contents into memory rather than streaming chunks, presenting memory exhaustion risks when large files (e.g. multi-hundred megabyte delimited or Excel files) are processed in resource-constrained container environments.

### Infrastructure

Container packaging is cleanly implemented in `Dockerfile` using a minimal `python:3.12-slim-bookworm` base image with official Microsoft ODBC Driver 18 packages and unprivileged user execution (`USER importeruser`). Because the process operates over stdio, standard HTTP health checks are absent (F3); introducing a CLI-based ping/preflight command would improve orchestrator observability.

## Key findings and action items

### F1: Control plane lacks a versioned migration tool, relying on in-place DDL and drop/rebuild scripts

- **Priority:** Medium — will cost manual effort and risk schema drift when deploying updates to non-DEV environments
- **Lens:** Data
- **Evidence:** `sql/001_schema.sql:10-12`, `src/importer/schema_tables.py:30-43`, `src/importer/environment.py:28-34`, `dat-01`
- **Scenario:** The control plane (`dbo.artifact_versions`, `dbo.file_ledger`, `dbo.reject_repository`) is declared in a single in-place DDL script (`sql/001_schema.sql`). For development environments, `importer db rebuild` drops and recreates all tables (`src/importer/schema_tables.py:30-43`). However, non-DEV databases marked as `PROD` are protected by `src/importer/environment.py:28-34`, which strictly prohibits rebuilds. When a new release introduces changes to control-plane schemas, operators have no automated migration tool (such as Alembic or Flyway) to apply versioned forward deltas or track schema versions, forcing manual SQL script execution.
- **Fix:** Introduce an incremental schema version table and runner (or integrate Alembic) to execute sequential migration scripts (e.g. `002_migration.sql`) rather than relying exclusively on full drops and re-creations.
- **Tradeoff:** Adds migration script authoring and testing overhead for schema changes, but guarantees safe upgrades in production and staging databases.
- **Confidence:** High — confirmed by explicit prototype policy comments in `sql/001_schema.sql:10-12` and guard enforcement in `src/importer/environment.py:28-34`.

### F2: Database credentials persisted in plaintext configuration files on local disk

- **Priority:** Medium — unencrypted database passwords on disk risk exposure if local filesystem permissions are broad
- **Lens:** Security
- **Evidence:** `src/importer/db.py:87-109`, `src/importer/setup_wizard.py:54-68`, `smithery.yaml:8`, `sec-02`
- **Scenario:** The standup wizard (`src/importer/setup_wizard.py:54-68`) and connection loader (`src/importer/db.py:87-109`) persist SQL Server connection credentials including plaintext passwords into `database.env`. While `.gitignore` correctly prevents committing `database.env` to version control, local developer workstations, CI runners, or container volume mounts hold unencrypted credentials. In multi-user systems or environments where local storage is backed up, passwords can be leaked. Additionally, schema documentation in `smithery.yaml:8` illustrates connection URL formats with plaintext passwords.
- **Fix:** Support OS credential storage (DPAPI on Windows, Keychain on macOS, Secret Service on Linux) or Azure Entra ID / Windows Integrated Authentication (`trusted=yes`) as preferred options, prompting for passwords interactively during CLI runs when not stored in a secret manager.
- **Tradeoff:** Integrated/Entra authentication requires appropriate domain or identity infrastructure, and interactive password prompts require operator interaction in non-daemon modes.
- **Confidence:** High — code inspection of `src/importer/db.py:87-109` confirms fallback to plaintext `database.env`.

### F3: Docker container omits startup diagnostic or preflight connectivity probe for MCP stdio operation

- **Priority:** Low — stdio process cleanly exits on EOF, but orchestrators cannot verify database readiness before client tool calls
- **Lens:** Infrastructure
- **Evidence:** `Dockerfile:1`, `Dockerfile:35`, `src/importer/cli.py:270-280`, `inf-01`
- **Scenario:** The container entrypoint is configured as `["importer", "mcp"]` (`Dockerfile:35`), which launches the JSON-RPC stdio protocol loop. Because the server communicates via standard input/output without an HTTP port, Docker's native `HEALTHCHECK` directive is omitted (`Dockerfile:1`). If a deployed container experiences network isolation, expired credentials, or an unreachable SQL Server, the process will remain running in the container waiting for input, and will only surface connection failures when an MCP client issues an actual tool call.
- **Fix:** Add a dedicated preflight command (e.g. `importer db check` or `importer ping`) and document a container healthcheck instruction using `HEALTHCHECK CMD importer db check || exit 1`.
- **Tradeoff:** Adds a small CLI check command, with minimal maintenance impact.
- **Confidence:** High — `Dockerfile:35` directly starts `importer mcp` without pre-start probe.

### F4: Full file memory buffering in readers creates potential memory pressure on large datasets

- **Priority:** Low — delimited and document readers buffer all rows in memory before bisection and staging
- **Lens:** Application
- **Evidence:** `src/importer/readers/delimited.py:25-37`, `src/importer/pipeline.py:50`
- **Scenario:** The delimited text reader (`src/importer/readers/delimited.py:25-37`) decodes the incoming byte payload, splits all lines into an in-memory list, and constructs a list of `Record` tuples before returning. Similarly, `src/importer/pipeline.py:50` reads the entire locator payload into memory via `locators.read_bytes()`. If an incoming file exceeds available RAM (such as a multi-gigabyte export processed in a container limited to 512MB RAM), the process will encounter an Out-Of-Memory (OOM) abort prior to staging rows into SQL Server.
- **Fix:** Refactor readers to yield records as a streaming generator or batched iterator, reading from disk in configurable chunk sizes directly into the staging loader.
- **Tradeoff:** Streaming readers complicate line-number indexing and bisection boundary calculations for error triage.
- **Confidence:** Medium — observed in `src/importer/readers/delimited.py:25-37`; impact depends on expected incoming file sizes.

## Action plan

| Order | Action | Findings | Effort |
|---|---|---|---|
| 1 | Introduce a lightweight migration runner for control-plane schema updates | F1 | days |
| 2 | Add Entra ID token and OS credential store support to avoid plaintext password storage | F2 | days |
| 3 | Add `importer db check` preflight command and optional Docker healthcheck | F3 | hours |
| 4 | Stream large delimited/NDJSON inputs to reduce peak memory consumption | F4 | days |

## What I inspected and what I did not

I inspected the core state machine (`src/importer/pipeline.py`), repository stores (`src/importer/mssql_store.py`, `src/importer/memory_store.py`), database and environment safeguards (`src/importer/db.py`, `src/importer/environment.py`), format readers (`src/importer/readers/`), setup wizard (`src/importer/setup_wizard.py`), DDL definitions (`sql/001_schema.sql`), and deployment specifications (`Dockerfile`, `smithery.yaml`, `.github/workflows/`).

### Signals evaluated and set aside:
- **`sec-01` (`.github/workflows/release.yml:27` / lines 8-10):** The automated scanner flagged `id-token` as a hardcoded secret default. Inspection confirms this is the GitHub Actions `permissions: id-token: write` directive required for OpenID Connect (OIDC) authentication with PyPI Trusted Publishing. It is an IAM permission declaration, not an embedded credential. Set aside as a false alarm.

### Limitations of static inspection:
This review is static; live SQL Server throughput under heavy concurrent load, OS filesystem performance over SMB shares, and Azure Blob latency were not profiled. Runtime memory consumption was evaluated from code structure rather than under full-scale multi-gigabyte import runs.
