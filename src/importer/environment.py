"""The database's own environment marker and the DEV guard (design §8.17).

Safeguards live in the database, not in local files: dev-only operations need
dbo.database_identity = 'DEV' AND a server-reported database name containing
'dev'. The identity row is written once by `importer db init` and never changed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from .schema_tables import OWNED_SCHEMAS_PREDICATE

ENVIRONMENTS = ("DEV", "PROD")
IDENTITY_TABLE_SQL = """IF OBJECT_ID(N'dbo.database_identity') IS NULL
CREATE TABLE dbo.database_identity (
    id          TINYINT NOT NULL CONSTRAINT pk_database_identity PRIMARY KEY
                CONSTRAINT ck_identity_single CHECK (id = 1),
    environment VARCHAR(8) NOT NULL CONSTRAINT ck_identity_env CHECK (environment IN ('DEV','PROD')),
    created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    created_by  NVARCHAR(128) NOT NULL
);"""

CONTROL_TABLES = ("artifact_versions", "signature_registry", "file_ledger", "reject_repository",
                  "file_quarantine", "decision_log", "database_identity")
FOREIGN_TABLES_SQL = (
    "SELECT COUNT(*) FROM sys.tables AS t JOIN sys.schemas AS s ON s.schema_id = t.schema_id "
    "WHERE NOT (s.name = N'dbo' AND t.name IN (" + ", ".join(f"N'{n}'" for n in CONTROL_TABLES) + ")) "
    f"AND NOT {OWNED_SCHEMAS_PREDICATE}"
)


class GuardError(RuntimeError):
    """An environment rule refused the operation."""


@dataclass(frozen=True)
class Identity:
    server: str
    database: str
    environment: str | None


def read(conn) -> Identity:
    server, database = conn.execute(text("SELECT @@SERVERNAME, DB_NAME()")).one()
    exists = conn.execute(text("SELECT OBJECT_ID(N'dbo.database_identity')")).scalar_one() is not None
    environment = None
    if exists:
        environment = conn.execute(text("SELECT environment FROM dbo.database_identity WHERE id = 1")).scalar_one_or_none()
    return Identity(server, database, environment)


def dev_guard(environment: str | None, database: str | None) -> str | None:
    if environment != "DEV":
        return f"database_identity is {environment or 'not set'}, not DEV (importer db init --environment DEV on a DEV database)"
    if "dev" not in (database or "").lower():
        return f"database name {database!r} does not contain 'dev'"
    return None


def banner(identity: Identity) -> str:
    return f"importer @ {identity.server}/{identity.database} [{identity.environment or 'UNSET'}]"


def foreign_tables(conn) -> int:
    """Tables in this database the importer does not own (a destination has many)."""
    return conn.execute(text(FOREIGN_TABLES_SQL)).scalar_one()


def init(conn, environment: str, *, by: str) -> None:
    """Mark a dedicated control database once. Nothing is written until every check passes."""
    env = (environment or "").upper()
    if env not in ENVIRONMENTS:
        raise GuardError(f"environment must be one of {ENVIRONMENTS}, got {environment!r}")
    if conn.execute(text("SELECT OBJECT_ID(N'dbo.database_identity')")).scalar_one() is not None:
        existing = conn.execute(text("SELECT environment FROM dbo.database_identity WHERE id = 1")).scalar_one_or_none()
        if existing is not None:
            raise GuardError(f"database_identity is already {existing}; it cannot be changed")
    foreign = foreign_tables(conn)
    if foreign:
        raise GuardError(f"this database has {foreign} table(s) the importer does not own; only a dedicated "
                         "importer control database can be marked (destinations are never written)")
    conn.exec_driver_sql(IDENTITY_TABLE_SQL)
    conn.execute(text("INSERT INTO dbo.database_identity (id, environment, created_by) VALUES (1, :e, :by)"),
                 {"e": env, "by": by})


def target_of(store) -> Identity | None:
    engine = getattr(store, "engine", None)
    if engine is None:
        return None
    with engine.connect() as conn:
        return read(conn)
