"""Raw/stage DDL generation from schema definitions (design §8.10, §8.11, §8.15).

Stable code generates every physical table from versioned schema data; the agent
never writes DDL. Names come from the destination address; identifiers are
validated and bracket-quoted. Schemas the importer creates carry the extended
property importer_owned = 1, which is what `db rebuild` drops by.
"""

from __future__ import annotations

import re

from .addresses import Address

IDENT = re.compile(r"^[A-Za-z0-9_]+$")
OWNED_PROPERTY = "importer_owned"
LINEAGE_COLUMNS = ["_file_id", "_row_identity", "_signature_id", "_artifact_version_id", "_extras"]
_LINEAGE_TYPES = {
    "_file_id": "BIGINT NOT NULL",
    "_row_identity": "NVARCHAR(256) NOT NULL",
    "_signature_id": "BIGINT NOT NULL",
    "_artifact_version_id": "BIGINT NOT NULL",
    "_extras": "NVARCHAR(MAX) NULL",
}

OWNED_SCHEMAS_PREDICATE = f"""(s.name IN (N'raw', N'stage') OR EXISTS (
    SELECT 1 FROM sys.extended_properties AS ep
    WHERE ep.class = 3 AND ep.major_id = s.schema_id AND ep.name = N'{OWNED_PROPERTY}'))"""

REBUILD_DROP_SQL = f"""
DECLARE @sql NVARCHAR(MAX) = N'';
SELECT @sql += N'DROP TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N';'
FROM sys.tables AS t JOIN sys.schemas AS s ON s.schema_id = t.schema_id
WHERE {OWNED_SCHEMAS_PREDICATE};
EXEC sp_executesql @sql;
SET @sql = N'';
SELECT @sql += N'DROP SCHEMA ' + QUOTENAME(s.name) + N';'
FROM sys.schemas AS s
WHERE {OWNED_SCHEMAS_PREDICATE};
EXEC sp_executesql @sql;
DROP TABLE IF EXISTS dbo.decision_log, dbo.file_quarantine, dbo.reject_repository,
    dbo.file_ledger, dbo.signature_registry, dbo.artifact_versions;
"""


def validate_identifier(name: str, what: str = "identifier") -> None:
    if not isinstance(name, str) or not IDENT.match(name):
        raise ValueError(f"{what} {name!r} must match [A-Za-z0-9_]+")


def q(name: str) -> str:
    validate_identifier(name)
    return f"[{name}]"


def validate_columns(columns: list[str]) -> None:
    seen: set[str] = set()
    for column in columns:
        validate_identifier(column, "column")
        if column.startswith("_"):
            raise ValueError(f"column {column!r}: names starting with '_' are reserved for lineage")
        if column.lower() in seen:
            raise ValueError(f"duplicate column {column!r} (SQL Server names are case-insensitive)")
        seen.add(column.lower())


def raw_name(a: Address) -> str:
    return f"{q(a.raw_schema)}.{q(a.table)}"


def stage_name(a: Address) -> str:
    return f"[stage].{q(a.stage_table)}"


def ensure_schema_sql(name: str) -> str:
    validate_identifier(name, "schema")
    return (
        f"IF SCHEMA_ID(N'{name}') IS NULL BEGIN "
        f"EXEC(N'CREATE SCHEMA {q(name)}'); "
        f"EXEC sys.sp_addextendedproperty @name = N'{OWNED_PROPERTY}', @value = 1, "
        f"@level0type = N'SCHEMA', @level0name = N'{name}'; END"
    )


def _columns_sql(columns: list[str]) -> list[str]:
    validate_columns(columns)
    return [f"{q(c)} NVARCHAR(MAX) NULL" for c in columns]


def create_raw_sql(a: Address, columns: list[str]) -> str:
    parts = [f"{q(n)} {_LINEAGE_TYPES[n]}" for n in LINEAGE_COLUMNS]
    parts.append("[_landed_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
    parts += _columns_sql(columns)
    parts.append(f"CONSTRAINT {q(a.pk_name)} PRIMARY KEY CLUSTERED ([_file_id], [_row_identity])")
    return f"CREATE TABLE {raw_name(a)} (\n    " + ",\n    ".join(parts) + "\n)"


def create_stage_sql(a: Address, columns: list[str]) -> str:
    parts = [f"{q(n)} {_LINEAGE_TYPES[n]}" for n in LINEAGE_COLUMNS] + _columns_sql(columns)
    return f"CREATE TABLE {stage_name(a)} (\n    " + ",\n    ".join(parts) + "\n)"


def add_column_sql(zone: str, a: Address, column: str) -> str:
    if zone not in ("raw", "stage"):
        raise ValueError(f"zone must be raw or stage, got {zone!r}")
    table = raw_name(a) if zone == "raw" else stage_name(a)
    return f"ALTER TABLE {table} ADD {q(column)} NVARCHAR(MAX) NULL"

