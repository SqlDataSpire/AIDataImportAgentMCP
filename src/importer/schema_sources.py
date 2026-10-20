"""Schema import from a destination database or a column file (design §8.16).

Target access is needed only when a schema is captured; afterwards everything runs
from the registered copy. Live reads run inside the destination database (no
three-part names), so they work on Azure SQL Database too. The agent never
connects to a destination.
"""

from __future__ import annotations

import csv
import json
from fnmatch import fnmatchcase
from pathlib import Path

from sqlalchemy import text

from .addresses import AddressError
from .admin import SchemaRefused, register_schema
from .settings import load as load_settings

TABLES_SQL = (
    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
)
COLUMNS_SQL = (
    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = :s AND TABLE_NAME = :t ORDER BY ORDINAL_POSITION"
)


class SchemaSourceError(ValueError):
    """The import request itself is malformed."""


class LiveSource:
    def __init__(self, engine):
        self.engine = engine

    def tables(self) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            return [(s, t) for s, t in conn.execute(text(TABLES_SQL)).all()]

    def columns(self, schema: str, table: str) -> list[str]:
        with self.engine.connect() as conn:
            return list(conn.execute(text(COLUMNS_SQL), {"s": schema, "t": table}).scalars())

    def close(self) -> None:
        """Release pooled connections to the destination (nothing stays open on it)."""
        self.engine.dispose()


def live_source(store, database_name: str) -> LiveSource:
    from .db import engine_for

    with store.transaction() as tx:
        destination = load_settings(tx.artifacts).destination(database_name)
    if destination is None:
        raise SchemaSourceError(f"{database_name!r} is not a configured destination (importer config set destination ...)")
    return LiveSource(engine_for(destination.server, destination.database))


def read_column_file(path) -> list[str]:
    path = Path(path)
    body = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        columns = json.loads(body)
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise SchemaSourceError(f"{path}: expected a JSON list of column names")
        return columns
    return [row[0].strip() for row in csv.reader(body.splitlines()) if row and row[0].strip()]


def _is_pattern(part: str) -> bool:
    return any(ch in part for ch in "*?[")


def expand(pattern: str, source) -> list[str]:
    parts = pattern.split(".")
    if len(parts) != 3:
        raise SchemaSourceError(f"{pattern!r}: expected DATABASE.SCHEMA.TABLE (wildcards allowed in SCHEMA and TABLE)")
    database, schema_pattern, table_pattern = parts
    if not (_is_pattern(schema_pattern) or _is_pattern(table_pattern)):
        return [pattern]
    return [
        f"{database}.{schema}.{table}" for schema, table in source.tables()
        if fnmatchcase(schema.lower(), schema_pattern.lower()) and fnmatchcase(table.lower(), table_pattern.lower())
    ]


def import_schemas(store, pattern: str, *, by: str, source=None, columns: list[str] | None = None) -> dict:
    report: dict[str, list] = {"added": [], "unchanged": [], "changed": [], "refused": []}
    if columns is not None:
        if any(_is_pattern(p) for p in pattern.split(".")[1:]):
            raise SchemaSourceError("a column file describes one explicit DATABASE.SCHEMA.TABLE, not a pattern")
        addresses, kind = [pattern], "file"
    else:
        if source is None:
            raise SchemaSourceError("a live import needs a source")
        addresses, kind = expand(pattern, source), "live"
    for address in addresses:
        try:
            if columns is not None:
                cols = list(columns)
            else:
                _, schema, table = address.split(".")
                cols = source.columns(schema, table)
            if not cols:
                raise SchemaRefused(f"{address}: no columns (or the table does not exist)")
            with store.transaction() as tx:
                outcome = register_schema(tx, address, cols, created_by=by, source=kind)
            report[outcome.result].append(outcome.to_dict())
        except (AddressError, SchemaRefused, ValueError) as exc:
            report["refused"].append({"address": address, "reason": str(exc)})
    with store.transaction() as tx:
        tx.audit.decision(None, "schema_import", observed={"pattern": pattern, "source": kind},
                          proposed={key: len(value) for key, value in report.items()}, applied=True, approval=by)
    return report
