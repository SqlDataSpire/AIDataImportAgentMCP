/* ============================================================================
   Data Agent: Importer — control-plane schema (MSSQL / T-SQL)
   Maps to design record §2 (five tables), §3 status lifecycle, §8.11 (M2a).
   Raw landing-zone tables are named by destination address (§8.15):
   [<db>_<schema>].[<table>] and [stage].[<db>_<schema>__<table>], in schemas the
   importer creates and marks importer_owned. They are generated from versioned
   schema definitions.

   Authoritative copy for control-plane DDL.
   Prototype policy (§8.11): this create script is edited in place and git is
   its history. `importer db rebuild` (DEV only) drops and re-creates it all.
   ========================================================================== */

/* 0. DATABASE IDENTITY — DEV | PROD, written once by `importer db init`; never dropped by rebuild. §8.17 */
IF OBJECT_ID(N'dbo.database_identity') IS NULL
CREATE TABLE dbo.database_identity (
    id          TINYINT NOT NULL CONSTRAINT pk_database_identity PRIMARY KEY
                CONSTRAINT ck_identity_single CHECK (id = 1),
    environment VARCHAR(8) NOT NULL CONSTRAINT ck_identity_env CHECK (environment IN ('DEV','PROD')),
    created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    created_by  NVARCHAR(128) NOT NULL
);

/* 3. ARTIFACT VERSIONS — schema, mapper, repair-rule & config versions; never edited in place. §2,§5,§8.9-8.11,§8.17 */
CREATE TABLE dbo.artifact_versions (
    version_id        BIGINT IDENTITY(1,1) PRIMARY KEY,
    artifact_type     VARCHAR(8) NOT NULL
        CONSTRAINT ck_artifact_type CHECK (artifact_type IN ('schema','mapper','rule','config')),
    artifact_key      NVARCHAR(128) NOT NULL,          -- schema: address | mapper: sig.<signature_id> or a name | rule: schema address | config: 'importer'
    version_no        INT NOT NULL,
    definition        NVARCHAR(MAX) NOT NULL,          -- JSON: {columns, source} | mapping spec | rule-as-data | settings
    is_active         BIT NOT NULL DEFAULT 0,
    content_sha256    CHAR(64) NULL,                   -- plugin mappers: pinned file hash
    created_by        NVARCHAR(128) NULL,              -- 'agent' | user
    approved_by       NVARCHAR(128) NULL,
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_artifact UNIQUE (artifact_type, artifact_key, version_no)
);

/* 2. SIGNATURE REGISTRY — PROJECT|CLASS|FILE_TYPE|<physical> -> mapper version. §2,§4,§8.4,§8.6 */
CREATE TABLE dbo.signature_registry (
    signature_id      BIGINT IDENTITY(1,1) PRIMARY KEY,
    fingerprint       NVARCHAR(512) NOT NULL,          -- e.g. DC|PC|XML|prescriptionDrugTemplateVO
    physical_part     NVARCHAR(256) NOT NULL,          -- XML root local name | h:<16 hex>
    xml_namespace     NVARCHAR(512) NULL,              -- must also match for an XML hit
    mapper_version_id BIGINT NULL REFERENCES dbo.artifact_versions(version_id),
    status            VARCHAR(16) NOT NULL DEFAULT 'proposed',
        CONSTRAINT ck_sig_status CHECK (status IN ('proposed','active','retired')),
    observed_schema   NVARCHAR(MAX) NULL,              -- JSON: header / sheets / keys / XML root, namespace and nesting tree (§8.24)
    proposal          NVARCHAR(MAX) NULL,              -- JSON: {candidates: [...], review: {state, draft_version_id, path, rationale, preview, notes, history}} (§8.18)
    landing_contract  NVARCHAR(MAX) NULL,              -- JSON: structural landability rules (reserved)
    provenance        NVARCHAR(MAX) NULL,              -- who/what added it, from which file
    approved_by       NVARCHAR(128) NULL,
    approved_at       DATETIME2 NULL,
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_sig_fingerprint UNIQUE (fingerprint)
);
CREATE INDEX ix_sig_physical ON dbo.signature_registry(physical_part);

/* 1. FILE LEDGER — one row per file; trigger source + audit trail + dedupe. §2,§3,§8.3,§8.12 */
CREATE TABLE dbo.file_ledger (
    file_id           BIGINT IDENTITY(1,1) PRIMARY KEY,
    source_path       NVARCHAR(1024) NOT NULL,
    original_filename NVARCHAR(256) NOT NULL,
    file_type         VARCHAR(16) NOT NULL,            -- CSV | TSV | PIPE | EXCEL | JSON | NDJSON | XML
    idempotency_key   CHAR(64) NOT NULL,               -- SHA-256 of file bytes
    project           VARCHAR(64) NOT NULL,
    class             VARCHAR(64) NOT NULL,
    trigger_source    NVARCHAR(128) NOT NULL,          -- manual | sftp | watcher | rerun
    request_context   NVARCHAR(MAX) NULL,              -- JSON, e.g. batch_id, run_id
    discovered_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id),
    status            VARCHAR(16) NOT NULL DEFAULT 'discovered',
        CONSTRAINT ck_ledger_status CHECK (status IN ('discovered','detected','landed','promoted','completed','failed','quarantined')),
    rows_total        INT NULL,
    rows_landed       INT NULL,
    rows_rejected     INT NULL,
    percent_loaded    DECIMAL(5,2) NULL,               -- Floor monotonic: only rises (§8.1)
    quarantine_reason VARCHAR(64) NULL,
    last_error        NVARCHAR(MAX) NULL,
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_ledger_idempotency ON dbo.file_ledger(idempotency_key);
CREATE INDEX ix_ledger_status ON dbo.file_ledger(status);

/* 4. REJECT REPOSITORY — every row that failed pre-check or load, for audit & reclaim. §2,§5,§8.2 */
CREATE TABLE dbo.reject_repository (
    reject_id         BIGINT IDENTITY(1,1) PRIMARY KEY,
    file_id           BIGINT NOT NULL REFERENCES dbo.file_ledger(file_id),
    target_address    VARCHAR(128) NOT NULL,           -- database.schema.table
    row_identity      NVARCHAR(256) NOT NULL,          -- L<n> | sheet!R<n> | <xpath>
    raw_payload       NVARCHAR(MAX) NOT NULL,          -- JSON: {field: value, ...}
    reason_code       VARCHAR(64) NOT NULL,            -- type_error | length | missing_required | check_violation
    reason_detail     NVARCHAR(MAX) NULL,              -- technical error / column name
    reclaimed         BIT NOT NULL DEFAULT 0,          -- 1 once a rule successfully lands it (§8.2)
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_reject_file_target ON dbo.reject_repository(file_id, target_address);
CREATE INDEX ix_reject_unreclaimed ON dbo.reject_repository(target_address) WHERE reclaimed = 0;

/* 6. FILE QUARANTINE — unroutable or unparseable files waiting for manual or agent onboarding. §2,§8.4 */
CREATE TABLE dbo.file_quarantine (
    quarantine_id     BIGINT IDENTITY(1,1) PRIMARY KEY,
    file_id           BIGINT NOT NULL REFERENCES dbo.file_ledger(file_id),
    reason_code       VARCHAR(64) NOT NULL,            -- unroutable | unparseable | corrupt
    reason_detail     NVARCHAR(MAX) NULL,              -- unroutable: the "looks like" summary
    signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id),
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

/* 5. DECISION LOG — every agent (and profile/match) action, for audit. §2,§8.4 */
CREATE TABLE dbo.decision_log (
    decision_id       BIGINT IDENTITY(1,1) PRIMARY KEY,
    file_id           BIGINT NULL REFERENCES dbo.file_ledger(file_id),
    action_type       NVARCHAR(64) NOT NULL,           -- profile_match | bind_signature | activate_schema |
                                                       -- diagnose | propose_rule | add_signature | reclaim |
                                                       -- config_change | schema_import | draft_mapping | reject_mapping | tree_drift | mcp:<tool>
    observed          NVARCHAR(MAX) NULL,
    proposed          NVARCHAR(MAX) NULL,
    applied           BIT NOT NULL DEFAULT 0,
    approval          NVARCHAR(128) NULL,              -- 'auto' | user id (new-schema gate)
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
