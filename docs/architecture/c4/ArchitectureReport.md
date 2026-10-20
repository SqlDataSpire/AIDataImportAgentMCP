# Architecture Report

## Repository Information

**Repository Name:** DataImportAgent

**Technology Stack:**

- Python

---

## Executive Summary

DataImportAgent has 34 projects (33 production, 1 test) with 123 project references between them. The analysis found 2 concern(s).

---

[//]: # (c4:start)

## Architecture (C4 model)

Lands incoming files into a raw zone; Claude-assisted onboarding for unknown files

### Level 1: System context

```mermaid
---
config:
  layout: elk
---
flowchart TB
    requestor("`**Requestor**
[Person]
Submits a file with its project,
class and type`")
    steward("`**Data steward**
[Person]
Approves mappings; registers
destination schemas`")
    system["`**Data Agent: Importer**
[Software System]
Lands incoming files into a raw
zone; Claude-assisted onboarding
for unknown files`"]
    claude_client["`**User's Claude client**
[Software System]
Claude Code / Desktop: the
onboarding agent (masked data
only)`"]
    file_shares["`**Shared file locations**
[Software System]
SMB / Azure Files shares with
incoming files`"]
    azure_blob["`**Azure Blob Storage**
[Software System]
Blob containers with incoming
files (Entra auth)`"]
    destination_dbs["`**Destination warehouse databases**
[Software System]
NPVCS, JSON_INDEX, RX,
NPIDATA_IMPORT, ZIPCODE (read
only)`"]
    requestor -->|"`Submits files to
[CLI]`"| system
    steward -->|"`Approves mappings in`"| claude_client
    steward -->|"`Configures and imports
schemas with
[CLI]`"| system
    claude_client -->|"`Calls onboarding tools on
[MCP over stdio]`"| system
    system -->|"`Reads files from
[SMB]`"| file_shares
    system -->|"`Reads files from
[azure-storage-blob]`"| azure_blob
    system -->|"`Reads schemas from (SELECT
only)
[pyodbc]`"| destination_dbs
    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef shared fill:#dbe9f6,stroke:#5d82a8,color:#000000,stroke-dasharray:4 3
    class requestor,steward person
    class system system
    class claude_client,file_shares,azure_blob,destination_dbs external
```

### Level 2: Containers

```mermaid
---
config:
  layout: elk
---
flowchart TB
    requestor("`**Requestor**
[Person]
Submits a file with its project,
class and type`")
    steward("`**Data steward**
[Person]
Approves mappings; registers
destination schemas`")
    subgraph boundary["`**Data Agent: Importer** [Software System]`"]
        data_import_agent["`**importer (CLI + MCP server)**
[Container: Python 3.14, SQLAlchemy Core, pyodbc, MCP SDK]
The importer CLI and the importer
mcp server`"]
        db_sql_server[("`**Control database (import_agent_dev)**
[Container: SQL Server]
Ledger, registry, versioned
artifacts, raw zone`")]
    end
    destination_dbs["`**Destination warehouse databases**
[Software System]
NPVCS, JSON_INDEX, RX,
NPIDATA_IMPORT, ZIPCODE (read
only)`"]
    claude_client["`**User's Claude client**
[Software System]
Claude Code / Desktop: the
onboarding agent (masked data
only)`"]
    file_shares["`**Shared file locations**
[Software System]
SMB / Azure Files shares with
incoming files`"]
    azure_blob["`**Azure Blob Storage**
[Software System]
Blob containers with incoming
files (Entra auth)`"]
    requestor -->|"`Submits files to
[CLI]`"| data_import_agent
    steward -->|"`Approves mappings in`"| claude_client
    steward -->|"`Configures and imports
schemas with
[CLI]`"| data_import_agent
    claude_client -->|"`Calls onboarding tools on
[MCP over stdio]`"| data_import_agent
    data_import_agent -->|"`Reads files from
[SMB]`"| file_shares
    data_import_agent -->|"`Reads files from
[azure-storage-blob]`"| azure_blob
    data_import_agent -->|"`Records state and lands rows
in
[pyodbc]`"| db_sql_server
    data_import_agent -->|"`Reads schemas from (SELECT
only)
[pyodbc]`"| destination_dbs
    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef shared fill:#dbe9f6,stroke:#5d82a8,color:#000000,stroke-dasharray:4 3
    class requestor,steward person
    class data_import_agent,db_sql_server container
    class destination_dbs,claude_client,file_shares,azure_blob external
    style boundary fill:none,stroke:#444444,stroke-dasharray:6 4
```

| Container | Technology | Responsibility | Code |
|---|---|---|---|
| **importer (CLI + MCP server)** (container) | Python 3.14, SQLAlchemy Core, pyodbc, MCP SDK | The importer CLI and the importer mcp server | `.` |
| **Control database (import_agent_dev)** (data store) | SQL Server | Ledger, registry, versioned artifacts, raw zone | — |
| **Destination warehouse databases** (data store) | SQL Server (CSSNPI) | NPVCS, JSON_INDEX, RX, NPIDATA_IMPORT, ZIPCODE (read only) | — |

### Level 3: Components

For each container: the modules it is built from (when it has several), then the code structure inside them (packages or namespaces and the imports between them).

#### importer (CLI + MCP server)

> 44 of 92 dependencies are hidden because a longer path already shows them (A → C is left out when A → B → C is drawn). The full list is in the dependency graph appendix.
> 33 components: above the 20 that stay readable in one diagram.

```mermaid
---
config:
  layout: elk
---
flowchart TB
    subgraph boundary["`**importer (CLI + MCP server)** [Container]`"]
        importer_addresses["`**addresses**
[Component]`"]
        importer_admin["`**admin**
[Component]
Registration helpers: schemas,
mappers, signatures`"]
        importer_agent["`**agent**
[Component]`"]
        importer_cli["`**cli**
[Component]
The importer command line:
process, retry, pending, config,
schema import, db init/rebuild,
mcp`"]
        importer_config["`**config**
[Component]`"]
        importer_data_guard["`**data_guard**
[Component]
Claude Code hook asking permission
before any direct read of file
contents`"]
        importer_db["`**db**
[Component]`"]
        importer_detector["`**detector**
[Component]
Signature lookup in the registry
(model-free)`"]
        importer_environment["`**environment**
[Component]
DEV/PROD marker and the DEV guard`"]
        importer_fingerprint["`**fingerprint**
[Component]`"]
        importer_loader["`**loader**
[Component]
Stages rows with bisection, then
promotes them transactionally`"]
        importer_locators["`**locators**
[Component]
Reads files from SMB, Azure Files
or Blob locators; verifies the
SHA-256`"]
        importer_mappers["`**mappers**
[Component]
Declarative specs, shapers and
hash-pinned plugins extract rows`"]
        importer_masking["`**masking**
[Component]
Reduces every row-derived value to
its shape before it can leave
(letter a, digit 9)`"]
        importer_mcp_server["`**mcp_server**
[Component]
MCP tools and the onboard prompt
for the user's Claude client;
sign-off gate on accept_mapping /
register_schema`"]
        importer_memory_store["`**memory_store**
[Component]
In-memory repositories used by the
unit tests`"]
        importer_metrics["`**metrics**
[Component]
percent_parsed / percent_loaded
with the monotonic floor
(invariant #1)`"]
        importer_models["`**models**
[Component]
Statuses, row types and results
shared by every layer`"]
        importer_mssql_store["`**mssql_store**
[Component]
SQL Server repositories: ledger,
registry, artifacts, rejects,
audit, raw zone`"]
        importer_onboarding["`**onboarding**
[Component]
Confirm-and-bind: reuse / exact /
fallback drafts, previews, submit,
reject, accept and bind`"]
        importer_pipeline["`**pipeline**
[Component]
The ledger state machine:
process() and retry()`"]
        importer_precheck["`**precheck**
[Component]`"]
        importer_profiler["`**profiler**
[Component]
Miss profiling and 'looks like'
candidates`"]
        importer_proposals["`**proposals**
[Component]`"]
        importer_readers["`**readers**
[Component]
Readers for delimited, Excel,
JSON, NDJSON and XML`"]
        importer_reclaim["`**reclaim**
[Component]
Idempotent reclaim of rejects
(invariant #2)`"]
        importer_reject_store["`**reject_store**
[Component]`"]
        importer_repositories["`**repositories**
[Component]`"]
        importer_rules["`**rules**
[Component]`"]
        importer_schema_sources["`**schema_sources**
[Component]
Destination schema import, live
(read-only) or from a column file`"]
        importer_schema_tables["`**schema_tables**
[Component]`"]
        importer_settings["`**settings**
[Component]
Shared settings stored as a
versioned config artifact`"]
        importer_shapers["`**shapers**
[Component]`"]
    end
    importer_admin --> importer_fingerprint
    importer_admin --> importer_mappers
    importer_admin --> importer_schema_tables
    importer_admin --> importer_settings
    importer_cli --> importer_data_guard
    importer_cli --> importer_environment
    importer_cli --> importer_mcp_server
    importer_cli --> importer_mssql_store
    importer_db --> importer_config
    importer_detector --> importer_fingerprint
    importer_detector --> importer_repositories
    importer_environment --> importer_schema_tables
    importer_fingerprint --> importer_readers
    importer_loader --> importer_repositories
    importer_mappers --> importer_repositories
    importer_mappers --> importer_shapers
    importer_mcp_server --> importer_onboarding
    importer_mcp_server --> importer_schema_sources
    importer_memory_store --> importer_metrics
    importer_memory_store --> importer_proposals
    importer_memory_store --> importer_repositories
    importer_mssql_store --> importer_proposals
    importer_mssql_store --> importer_repositories
    importer_mssql_store --> importer_schema_tables
    importer_onboarding --> importer_addresses
    importer_onboarding --> importer_masking
    importer_onboarding --> importer_pipeline
    importer_onboarding --> importer_proposals
    importer_onboarding --> importer_settings
    importer_pipeline --> importer_detector
    importer_pipeline --> importer_loader
    importer_pipeline --> importer_locators
    importer_pipeline --> importer_mappers
    importer_pipeline --> importer_metrics
    importer_pipeline --> importer_precheck
    importer_pipeline --> importer_profiler
    importer_precheck --> importer_models
    importer_profiler --> importer_readers
    importer_readers --> importer_models
    importer_reclaim --> importer_reject_store
    importer_reclaim --> importer_rules
    importer_reject_store --> importer_models
    importer_repositories --> importer_reject_store
    importer_schema_sources --> importer_admin
    importer_schema_sources --> importer_db
    importer_schema_tables --> importer_addresses
    importer_settings --> importer_repositories
    importer_shapers --> importer_readers
    db_sql_server[("`**Control database (import_agent_dev)**
[Container: SQL Server]
Ledger, registry, versioned
artifacts, raw zone`")]
    importer_mssql_store -->|"`Records state and lands rows
in
[pyodbc]`"| db_sql_server
    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef shared fill:#dbe9f6,stroke:#5d82a8,color:#000000,stroke-dasharray:4 3
    class importer_addresses,importer_admin,importer_agent,importer_cli,importer_config,importer_data_guard,importer_db,importer_detector,importer_environment,importer_fingerprint,importer_loader,importer_locators,importer_mappers,importer_masking,importer_mcp_server,importer_memory_store,importer_metrics,importer_models,importer_mssql_store,importer_onboarding,importer_pipeline,importer_precheck,importer_profiler,importer_proposals,importer_readers,importer_reclaim,importer_reject_store,importer_repositories,importer_rules,importer_schema_sources,importer_schema_tables,importer_settings,importer_shapers component
    class db_sql_server container
    style boundary fill:none,stroke:#444444,stroke-dasharray:6 4
```

### Key flows

How a request moves through the system, step by step.

#### A known file lands (no model)

importer process on a file whose signature is registered.

```mermaid
sequenceDiagram
    actor requestor as Requestor
    participant data_import_agent as importer (CLI + MCP server)
    participant importer_cli as importer.cli
    participant importer_pipeline as importer.pipeline
    participant importer_locators as importer.locators
    participant file_shares as Shared file locations
    participant importer_detector as importer.detector
    participant importer_mappers as importer.mappers
    participant importer_loader as importer.loader
    participant importer_mssql_store as importer.mssql_store
    participant db_sql_server as Control database (import_agent_dev)
    requestor->>data_import_agent: importer process ‹locator› --project --class --type [CLI]
    importer_cli->>importer_pipeline: process()
    importer_pipeline->>importer_locators: Read the file and hash it
    importer_locators->>file_shares: Read bytes [SMB]
    importer_pipeline->>importer_detector: Look up the signature (hit)
    importer_pipeline->>importer_mappers: Extract land / reject rows
    importer_pipeline->>importer_loader: Stage, then promote
    importer_loader->>importer_mssql_store: Stage batches#59; promote to raw in one transaction
    importer_mssql_store->>db_sql_server: Raw rows, ledger metrics [pyodbc]
    data_import_agent-->>requestor: FileResult with percent_loaded
```

#### An unknown file is onboarded with Claude

The file was quarantined with a 'looks like' proposal; the steward maps it in a Claude session.

```mermaid
sequenceDiagram
    actor steward as Data steward
    participant claude_client as User's Claude client
    participant importer_mcp_server as importer.mcp_server
    participant importer_onboarding as importer.onboarding
    participant importer_masking as importer.masking
    participant importer_mssql_store as importer.mssql_store
    participant importer_pipeline as importer.pipeline
    steward->>claude_client: /mcp__importer__onboard
    claude_client->>importer_mcp_server: list_pending, get_file_profile, get_prior_mappings [MCP]
    importer_mcp_server->>importer_onboarding: Profile, candidates, deterministic draft
    claude_client->>importer_mcp_server: preview_mapping, submit_mapping (draft spec) [MCP]
    importer_onboarding->>importer_masking: Mask preview sample values
    importer_mcp_server-->>claude_client: Review table (metadata and masked shapes only)
    steward->>claude_client: Approves accept_mapping
    Note over claude_client: the sign-off gate
    claude_client->>importer_mcp_server: accept_mapping (client asks the steward first) [MCP]
    importer_mcp_server->>importer_onboarding: accept()
    importer_onboarding->>importer_mssql_store: Bind signature to mapper (one transaction)
    importer_onboarding->>importer_pipeline: retry() every waiting file
    importer_mcp_server-->>claude_client: Each waiting file's status and percent_loaded
```

#### Destination schemas are registered

importer schema import 'NPVCS.*.*'; destinations are only read.

```mermaid
sequenceDiagram
    actor steward as Data steward
    participant data_import_agent as importer (CLI + MCP server)
    participant importer_cli as importer.cli
    participant importer_schema_sources as importer.schema_sources
    participant destination_dbs as Destination warehouse databases
    participant importer_admin as importer.admin
    participant importer_mssql_store as importer.mssql_store
    participant db_sql_server as Control database (import_agent_dev)
    steward->>data_import_agent: importer schema import 'NPVCS.*.*' [CLI]
    importer_cli->>importer_schema_sources: import_schemas()
    importer_schema_sources->>destination_dbs: List tables and columns (SELECT-only engine) [INFORMATION_SCHEMA]
    importer_schema_sources->>importer_admin: register_schema() per table
    importer_admin->>importer_mssql_store: New schema version (never edited in place)
    importer_mssql_store->>db_sql_server: artifact_versions rows [pyodbc]
    data_import_agent-->>steward: Added / unchanged / changed / refused report
```

### Configuration

| Variable | Default | Used by | Declared in |
|---|---|---|---|
| `IMPORTER_DATA_GUARD` | — | importer (CLI + MCP server) | `src/importer/data_guard.py:114` |
| `IMPORTER_HOME` | — | importer (CLI + MCP server) | `src/importer/config.py:15` |

### Technology inventory

Runtime, frameworks and key libraries declared in each container's build files.

| Container | Declared |
|---|---|
| **importer (CLI + MCP server)** | `Python >=3.11`, `Python-DataEngine>=2.4`, `sqlalchemy>=2.0`, `pyodbc>=5`, `lxml>=5`, `openpyxl>=3.1`, `xlrd>=2.0`, `python-dotenv>=1.0`, `mcp>=2`, `azure-storage-blob>=12.19`, `azure-identity>=1.15` |

### C4 files

- [c4-context.mmd](c4-context.mmd)
- [c4-container.mmd](c4-container.mmd)
- [c4-component-data-import-agent.mmd](c4-component-data-import-agent.mmd)
- [c4-flow-process-known-file.mmd](c4-flow-process-known-file.mmd)
- [c4-flow-onboard-unknown-file.mmd](c4-flow-onboard-unknown-file.mmd)
- [c4-flow-import-destination-schemas.mmd](c4-flow-import-destination-schemas.mmd)
- [c4-facts.json](c4-facts.json)
- [c4-model.json](c4-model.json)

[//]: # (c4:end)

---

## Architectural Observations

### Strengths

- No circular project references.
- Projects with no project references (the core that the rest builds on): importer.addresses, importer.agent, importer.config, importer.data_guard, importer.locators, importer.masking, importer.metrics, importer.models, importer.proposals, importer.rules.

### Concerns

- Production projects that no test project references: importer.agent, importer.config.
- Test projects that reference more than one production project (check whether unit and integration tests are mixed): tests.

---

## Recommendations

- Add test projects for the untested production projects.
- Keep each test project focused on one layer, and move cross-layer tests to a dedicated integration test project.

---

## Appendix: Project Dependency Graph

Every project or module and every reference between them, tests included. The automated checks above (cycles, stray projects, untested projects) are based on this graph.

**Solutions / build roots:** none found

**Project folders:** src

<details>
<summary>Full dependency graph</summary>

```mermaid
graph TD
    tests["tests"]
    subgraph group_src["src"]
        importer_addresses["importer.addresses"]
        importer_admin["importer.admin"]
        importer_agent["importer.agent"]
        importer_cli["importer.cli"]
        importer_config["importer.config"]
        importer_data_guard["importer.data_guard"]
        importer_db["importer.db"]
        importer_detector["importer.detector"]
        importer_environment["importer.environment"]
        importer_fingerprint["importer.fingerprint"]
        importer_loader["importer.loader"]
        importer_locators["importer.locators"]
        importer_mappers["importer.mappers"]
        importer_masking["importer.masking"]
        importer_mcp_server["importer.mcp_server"]
        importer_memory_store["importer.memory_store"]
        importer_metrics["importer.metrics"]
        importer_models["importer.models"]
        importer_mssql_store["importer.mssql_store"]
        importer_onboarding["importer.onboarding"]
        importer_pipeline["importer.pipeline"]
        importer_precheck["importer.precheck"]
        importer_profiler["importer.profiler"]
        importer_proposals["importer.proposals"]
        importer_readers["importer.readers"]
        importer_reclaim["importer.reclaim"]
        importer_reject_store["importer.reject_store"]
        importer_repositories["importer.repositories"]
        importer_rules["importer.rules"]
        importer_schema_sources["importer.schema_sources"]
        importer_schema_tables["importer.schema_tables"]
        importer_settings["importer.settings"]
        importer_shapers["importer.shapers"]
    end
    importer_admin --> importer_addresses
    importer_admin --> importer_fingerprint
    importer_admin --> importer_mappers
    importer_admin --> importer_models
    importer_admin --> importer_readers
    importer_admin --> importer_repositories
    importer_admin --> importer_schema_tables
    importer_admin --> importer_settings
    importer_cli --> importer_admin
    importer_cli --> importer_config
    importer_cli --> importer_data_guard
    importer_cli --> importer_db
    importer_cli --> importer_environment
    importer_cli --> importer_mappers
    importer_cli --> importer_mcp_server
    importer_cli --> importer_models
    importer_cli --> importer_mssql_store
    importer_cli --> importer_onboarding
    importer_cli --> importer_pipeline
    importer_cli --> importer_readers
    importer_cli --> importer_schema_sources
    importer_cli --> importer_schema_tables
    importer_cli --> importer_settings
    importer_db --> importer_config
    importer_detector --> importer_fingerprint
    importer_detector --> importer_models
    importer_detector --> importer_readers
    importer_detector --> importer_repositories
    importer_environment --> importer_schema_tables
    importer_fingerprint --> importer_models
    importer_fingerprint --> importer_readers
    importer_loader --> importer_models
    importer_loader --> importer_repositories
    importer_mappers --> importer_models
    importer_mappers --> importer_readers
    importer_mappers --> importer_repositories
    importer_mappers --> importer_shapers
    importer_mcp_server --> importer_addresses
    importer_mcp_server --> importer_admin
    importer_mcp_server --> importer_locators
    importer_mcp_server --> importer_onboarding
    importer_mcp_server --> importer_schema_sources
    importer_memory_store --> importer_metrics
    importer_memory_store --> importer_models
    importer_memory_store --> importer_proposals
    importer_memory_store --> importer_reject_store
    importer_memory_store --> importer_repositories
    importer_mssql_store --> importer_addresses
    importer_mssql_store --> importer_models
    importer_mssql_store --> importer_proposals
    importer_mssql_store --> importer_repositories
    importer_mssql_store --> importer_schema_tables
    importer_onboarding --> importer_addresses
    importer_onboarding --> importer_locators
    importer_onboarding --> importer_mappers
    importer_onboarding --> importer_masking
    importer_onboarding --> importer_models
    importer_onboarding --> importer_pipeline
    importer_onboarding --> importer_profiler
    importer_onboarding --> importer_proposals
    importer_onboarding --> importer_readers
    importer_onboarding --> importer_repositories
    importer_onboarding --> importer_settings
    importer_onboarding --> importer_shapers
    importer_pipeline --> importer_detector
    importer_pipeline --> importer_loader
    importer_pipeline --> importer_locators
    importer_pipeline --> importer_mappers
    importer_pipeline --> importer_metrics
    importer_pipeline --> importer_models
    importer_pipeline --> importer_precheck
    importer_pipeline --> importer_profiler
    importer_pipeline --> importer_readers
    importer_pipeline --> importer_repositories
    importer_precheck --> importer_models
    importer_profiler --> importer_models
    importer_profiler --> importer_readers
    importer_readers --> importer_models
    importer_reclaim --> importer_models
    importer_reclaim --> importer_reject_store
    importer_reclaim --> importer_rules
    importer_reject_store --> importer_models
    importer_repositories --> importer_models
    importer_repositories --> importer_reject_store
    importer_schema_sources --> importer_addresses
    importer_schema_sources --> importer_admin
    importer_schema_sources --> importer_db
    importer_schema_sources --> importer_settings
    importer_schema_tables --> importer_addresses
    importer_settings --> importer_models
    importer_settings --> importer_repositories
    importer_shapers --> importer_readers
    tests --> importer_addresses
    tests --> importer_admin
    tests --> importer_cli
    tests --> importer_data_guard
    tests --> importer_db
    tests --> importer_detector
    tests --> importer_environment
    tests --> importer_fingerprint
    tests --> importer_loader
    tests --> importer_locators
    tests --> importer_mappers
    tests --> importer_masking
    tests --> importer_mcp_server
    tests --> importer_memory_store
    tests --> importer_metrics
    tests --> importer_models
    tests --> importer_mssql_store
    tests --> importer_onboarding
    tests --> importer_pipeline
    tests --> importer_precheck
    tests --> importer_profiler
    tests --> importer_proposals
    tests --> importer_readers
    tests --> importer_reclaim
    tests --> importer_reject_store
    tests --> importer_repositories
    tests --> importer_rules
    tests --> importer_schema_sources
    tests --> importer_schema_tables
    tests --> importer_settings
    tests --> importer_shapers
```

</details>

### Components

| Project | Ecosystem | Folder | Kind | Depends on | Used by |
|---|---|---|---|---|---|
| importer.addresses | Python | src | production | 0 | 7 |
| importer.admin | Python | src | production | 8 | 4 |
| importer.agent | Python | src | production | 0 | 0 |
| importer.cli | Python | src | production | 15 | 1 |
| importer.config | Python | src | production | 0 | 2 |
| importer.data_guard | Python | src | production | 0 | 2 |
| importer.db | Python | src | production | 1 | 3 |
| importer.detector | Python | src | production | 4 | 2 |
| importer.environment | Python | src | production | 1 | 2 |
| importer.fingerprint | Python | src | production | 2 | 3 |
| importer.loader | Python | src | production | 2 | 2 |
| importer.locators | Python | src | production | 0 | 4 |
| importer.mappers | Python | src | production | 4 | 5 |
| importer.masking | Python | src | production | 0 | 2 |
| importer.mcp_server | Python | src | production | 5 | 2 |
| importer.memory_store | Python | src | production | 5 | 1 |
| importer.metrics | Python | src | production | 0 | 3 |
| importer.models | Python | src | production | 0 | 18 |
| importer.mssql_store | Python | src | production | 5 | 2 |
| importer.onboarding | Python | src | production | 12 | 3 |
| importer.pipeline | Python | src | production | 10 | 3 |
| importer.precheck | Python | src | production | 1 | 2 |
| importer.profiler | Python | src | production | 2 | 3 |
| importer.proposals | Python | src | production | 0 | 4 |
| importer.readers | Python | src | production | 1 | 10 |
| importer.reclaim | Python | src | production | 3 | 1 |
| importer.reject_store | Python | src | production | 1 | 4 |
| importer.repositories | Python | src | production | 2 | 10 |
| importer.rules | Python | src | production | 0 | 2 |
| importer.schema_sources | Python | src | production | 4 | 3 |
| importer.schema_tables | Python | src | production | 1 | 5 |
| importer.settings | Python | src | production | 2 | 5 |
| importer.shapers | Python | src | production | 1 | 3 |
| tests | Python | - | test | 31 | 0 |

### Dependency Analysis

### importer.addresses
- no project references
- used by importer.admin, importer.mcp_server, importer.mssql_store, importer.onboarding, importer.schema_sources, importer.schema_tables, tests

### importer.admin
- depends on importer.addresses
- depends on importer.fingerprint
- depends on importer.mappers
- depends on importer.models
- depends on importer.readers
- depends on importer.repositories
- depends on importer.schema_tables
- depends on importer.settings
- used by importer.cli, importer.mcp_server, importer.schema_sources, tests

### importer.agent
- no project references

### importer.cli
- depends on importer.admin
- depends on importer.config
- depends on importer.data_guard
- depends on importer.db
- depends on importer.environment
- depends on importer.mappers
- depends on importer.mcp_server
- depends on importer.models
- depends on importer.mssql_store
- depends on importer.onboarding
- depends on importer.pipeline
- depends on importer.readers
- depends on importer.schema_sources
- depends on importer.schema_tables
- depends on importer.settings
- used by tests

### importer.config
- no project references
- used by importer.cli, importer.db

### importer.data_guard
- no project references
- used by importer.cli, tests

### importer.db
- depends on importer.config
- used by importer.cli, importer.schema_sources, tests

### importer.detector
- depends on importer.fingerprint
- depends on importer.models
- depends on importer.readers
- depends on importer.repositories
- used by importer.pipeline, tests

### importer.environment
- depends on importer.schema_tables
- used by importer.cli, tests

### importer.fingerprint
- depends on importer.models
- depends on importer.readers
- used by importer.admin, importer.detector, tests

### importer.loader
- depends on importer.models
- depends on importer.repositories
- used by importer.pipeline, tests

### importer.locators
- no project references
- used by importer.mcp_server, importer.onboarding, importer.pipeline, tests

### importer.mappers
- depends on importer.models
- depends on importer.readers
- depends on importer.repositories
- depends on importer.shapers
- used by importer.admin, importer.cli, importer.onboarding, importer.pipeline, tests

### importer.masking
- no project references
- used by importer.onboarding, tests

### importer.mcp_server
- depends on importer.addresses
- depends on importer.admin
- depends on importer.locators
- depends on importer.onboarding
- depends on importer.schema_sources
- used by importer.cli, tests

### importer.memory_store
- depends on importer.metrics
- depends on importer.models
- depends on importer.proposals
- depends on importer.reject_store
- depends on importer.repositories
- used by tests

### importer.metrics
- no project references
- used by importer.memory_store, importer.pipeline, tests

### importer.models
- no project references
- used by importer.admin, importer.cli, importer.detector, importer.fingerprint, importer.loader, importer.mappers, importer.memory_store, importer.mssql_store, importer.onboarding, importer.pipeline, importer.precheck, importer.profiler, importer.readers, importer.reclaim, importer.reject_store, importer.repositories, importer.settings, tests

### importer.mssql_store
- depends on importer.addresses
- depends on importer.models
- depends on importer.proposals
- depends on importer.repositories
- depends on importer.schema_tables
- used by importer.cli, tests

### importer.onboarding
- depends on importer.addresses
- depends on importer.locators
- depends on importer.mappers
- depends on importer.masking
- depends on importer.models
- depends on importer.pipeline
- depends on importer.profiler
- depends on importer.proposals
- depends on importer.readers
- depends on importer.repositories
- depends on importer.settings
- depends on importer.shapers
- used by importer.cli, importer.mcp_server, tests

### importer.pipeline
- depends on importer.detector
- depends on importer.loader
- depends on importer.locators
- depends on importer.mappers
- depends on importer.metrics
- depends on importer.models
- depends on importer.precheck
- depends on importer.profiler
- depends on importer.readers
- depends on importer.repositories
- used by importer.cli, importer.onboarding, tests

### importer.precheck
- depends on importer.models
- used by importer.pipeline, tests

### importer.profiler
- depends on importer.models
- depends on importer.readers
- used by importer.onboarding, importer.pipeline, tests

### importer.proposals
- no project references
- used by importer.memory_store, importer.mssql_store, importer.onboarding, tests

### importer.readers
- depends on importer.models
- used by importer.admin, importer.cli, importer.detector, importer.fingerprint, importer.mappers, importer.onboarding, importer.pipeline, importer.profiler, importer.shapers, tests

### importer.reclaim
- depends on importer.models
- depends on importer.reject_store
- depends on importer.rules
- used by tests

### importer.reject_store
- depends on importer.models
- used by importer.memory_store, importer.reclaim, importer.repositories, tests

### importer.repositories
- depends on importer.models
- depends on importer.reject_store
- used by importer.admin, importer.detector, importer.loader, importer.mappers, importer.memory_store, importer.mssql_store, importer.onboarding, importer.pipeline, importer.settings, tests

### importer.rules
- no project references
- used by importer.reclaim, tests

### importer.schema_sources
- depends on importer.addresses
- depends on importer.admin
- depends on importer.db
- depends on importer.settings
- used by importer.cli, importer.mcp_server, tests

### importer.schema_tables
- depends on importer.addresses
- used by importer.admin, importer.cli, importer.environment, importer.mssql_store, tests

### importer.settings
- depends on importer.models
- depends on importer.repositories
- used by importer.admin, importer.cli, importer.onboarding, importer.schema_sources, tests

### importer.shapers
- depends on importer.readers
- used by importer.mappers, importer.onboarding, tests

### tests
- depends on importer.addresses
- depends on importer.admin
- depends on importer.cli
- depends on importer.data_guard
- depends on importer.db
- depends on importer.detector
- depends on importer.environment
- depends on importer.fingerprint
- depends on importer.loader
- depends on importer.locators
- depends on importer.mappers
- depends on importer.masking
- depends on importer.mcp_server
- depends on importer.memory_store
- depends on importer.metrics
- depends on importer.models
- depends on importer.mssql_store
- depends on importer.onboarding
- depends on importer.pipeline
- depends on importer.precheck
- depends on importer.profiler
- depends on importer.proposals
- depends on importer.readers
- depends on importer.reclaim
- depends on importer.reject_store
- depends on importer.repositories
- depends on importer.rules
- depends on importer.schema_sources
- depends on importer.schema_tables
- depends on importer.settings
- depends on importer.shapers

---

## Generated Artifacts

- C4 diagrams: listed under Architecture (C4 model) above
- [Mermaid source](dependency-graph.mmd)
- [Dependency data (JSON)](dependency-graph.json)
- [Repository scan (JSON)](repository-scan.json)
- [Summary (JSON)](summary.json)

---

## Report Metadata

Generated On: 2026-10-06T14:28:34.573823+00:00

Generator Version: 2.2
