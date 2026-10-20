"""SQL Server connectivity through SQLAlchemy and pyodbc with optional DataEngine fallback.

DataEngine (if installed) is imported lazily inside get_engine(). Otherwise,
standard SQLAlchemy over pyodbc is used directly with ODBC Driver 18/17.
Any driver initialization messages to stdout are redirected to stderr
(stdout is the JSON-RPC stream under `importer mcp`).

Destination engines (engine_for) are read-only by construction: a cursor-level guard
refuses anything but a single SELECT (design §8.16; destinations are never written).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from .config import home

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

CONNECTION_NAME = "importer"


_SINGLE_SELECT = re.compile(r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*SELECT\b", re.IGNORECASE | re.DOTALL)
_WRITE_INSIDE = re.compile(r"\bINTO\b|;\s*\S", re.IGNORECASE)


class ReadOnlyViolation(RuntimeError):
    """A non-SELECT statement was sent over a destination connection."""


def read_only(engine: "Engine") -> "Engine":
    """Refuse every statement on `engine` that is not a single, plain SELECT."""
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(conn, cursor, statement, parameters, context, executemany):
        if not _SINGLE_SELECT.match(statement) or _WRITE_INSIDE.search(statement):
            raise ReadOnlyViolation(f"destination connections are read-only; refused: {statement.strip()[:80]!r}")

    return engine


def _detect_odbc_driver(preferred: str | None = None) -> str:
    """Find the best available SQL Server ODBC driver."""
    if preferred:
        return preferred
    try:
        import pyodbc

        available = pyodbc.drivers()
        for candidate in (
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "SQL Server Native Client 11.0",
            "SQL Server",
        ):
            if candidate in available:
                return candidate
    except Exception:
        pass
    return "ODBC Driver 18 for SQL Server"


_SESSION_PASSWORD: str | None = None


def get_session_password() -> str | None:
    return _SESSION_PASSWORD


def set_session_password(pw: str | None) -> None:
    global _SESSION_PASSWORD
    _SESSION_PASSWORD = pw


def _resolve_password(*, un: str | None, server: str, doc: dict) -> str | None:
    """Resolve password from doc, env var, cached session, or interactive prompt."""
    if doc.get("PW"):
        return doc["PW"]
    if os.environ.get("IMPORTER_PASSWORD"):
        return os.environ["IMPORTER_PASSWORD"]
    global _SESSION_PASSWORD
    if _SESSION_PASSWORD:
        return _SESSION_PASSWORD

    trusted = str(doc.get("trusted", "no")).lower() in ("yes", "true", "1")
    if un and not trusted and sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        try:
            import getpass

            pw = getpass.getpass(f"Enter SQL Server password for {un}@{server}: ")
            _SESSION_PASSWORD = pw
            doc["PW"] = pw
            return pw
        except Exception:
            pass

    return None


def _build_pyodbc_connection_string(*, server: str, database: str, doc: dict) -> str:
    """Build a standard ODBC connection string for SQL Server."""
    driver_choice = doc.get("driver") or os.environ.get("MSSQL_ODBC_DRIVER")
    driver = _detect_odbc_driver(driver_choice)
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
    ]
    trusted = str(doc.get("trusted", "no")).lower() in ("yes", "true", "1")
    un = doc.get("UN")
    pw = doc.get("PW") or _resolve_password(un=un, server=server, doc=doc)

    if un and pw:
        parts.extend([f"UID={un}", f"PWD={pw}"])
    elif trusted:
        parts.append("Trusted_Connection=yes")
    elif un and not pw:
        if "auth" in doc:
            parts.extend([f"UID={un}", f"Authentication={doc['auth']}"])
        else:
            raise ConfigError(
                f"Password is required for SQL Server user '{un}' on {server}. "
                "Provide 'PW' in database.env, set IMPORTER_PASSWORD environment variable, "
                "or run interactively in a terminal."
            )

    trust_server_cert = doc.get("TrustServerCertificate", "yes")
    parts.append(f"TrustServerCertificate={trust_server_cert}")

    return ";".join(parts) + ";"


def _connection_engine(*, name: str, server: str, database: str, doc: dict) -> "Engine":
    from sqlalchemy import create_engine

    if doc.get("url"):
        url = doc["url"]
        kwargs = {"fast_executemany": True} if "pyodbc" in url else {}
        return create_engine(url, **kwargs)

    pw = doc.get("PW") or _resolve_password(un=doc.get("UN"), server=server, doc=doc)
    doc_with_pw = {**doc, "PW": pw} if pw else doc

    with contextlib.redirect_stdout(sys.stderr):
        try:
            from DataEngine.mssql import SqlConnectionObject  # lazy on purpose

            connection = SqlConnectionObject(
                name=name,
                server=server,
                database=database,
                UN=doc.get("UN") or None,
                PW=pw or None,
                trusted=doc.get("trusted", "no"),
            )
            return connection.engine
        except (ImportError, TypeError):
            pass

    odbc_str = _build_pyodbc_connection_string(server=server, database=database, doc=doc_with_pw)
    params = urllib.parse.quote_plus(odbc_str)
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}", fast_executemany=True)


class ConfigError(RuntimeError):
    """database.env is missing, malformed, or missing required connection attributes."""


def database_env_path() -> Path:
    return home() / "database.env"


def load_connection_document(path: Path | None = None, name: str = CONNECTION_NAME) -> dict:
    if path is None and os.environ.get("IMPORTER_DATABASE_URL"):
        return {"type": "mssql", "url": os.environ["IMPORTER_DATABASE_URL"], "server": "env", "database": "env"}
    path = path or database_env_path()
    if not path.exists():
        raise ConfigError(f"{path} not found")
    raw = dotenv_values(path).get("databases")
    if not raw:
        raise ConfigError(f"{path} has no 'databases' entry")
    try:
        docs = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: 'databases' is not valid JSON: {exc}") from exc
    doc = docs.get(name)
    if doc is None:
        raise ConfigError(f"{path}: no '{name}' connection")
    if doc.get("type") != "mssql":
        raise ConfigError(f"connection '{name}' must be type 'mssql'")
    if not doc.get("PW") and os.environ.get("IMPORTER_PASSWORD"):
        doc["PW"] = os.environ["IMPORTER_PASSWORD"]
    for key in ("server", "database"):
        if not doc.get(key):
            raise ConfigError(f"connection '{name}' is missing '{key}'")
    return doc


def get_engine(path: Path | None = None, name: str = CONNECTION_NAME) -> "Engine":
    doc = load_connection_document(path, name)
    return _connection_engine(name=name, server=doc["server"], database=doc["database"], doc=doc)


def engine_for(server: str, database: str, path: Path | None = None) -> "Engine":
    """An engine to a destination database, authenticating like the local `importer` entry.

    Used only for schema import (design §8.16); the caller's own Windows/Entra identity
    needs VIEW DEFINITION there. Nothing about the destination is stored locally, and
    the engine refuses every statement except a single SELECT.
    """
    doc = load_connection_document(path)
    return read_only(_connection_engine(name=f"dest_{database}", server=server, database=database, doc=doc))
