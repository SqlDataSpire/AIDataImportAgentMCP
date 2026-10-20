"""Integration fixtures: real DEV SQL Server via database.env (opt-in: pytest -m integration)."""

from __future__ import annotations

import pytest

from importer.db import ConfigError, load_connection_document


@pytest.fixture(scope="session")
def mssql_store():
    try:
        doc = load_connection_document()
    except ConfigError as exc:
        pytest.skip(f"no DEV database configured: {exc}")
    from importer import environment, settings
    from importer.db import get_engine
    from importer.mssql_store import MssqlStore

    store = MssqlStore(get_engine())
    with store.engine.connect() as conn:
        identity = environment.read(conn)
    reason = environment.dev_guard(identity.environment, identity.database)
    if reason:
        pytest.skip(f"integration tests only run against a DEV database: {reason}")
    with store.transaction() as tx:
        have = settings.load(tx.artifacts)
        for name in ("APP_DB", "EVENT_DB"):
            if have.destination(name) is None:
                settings.set_destination(tx, name, server=doc["server"], database=name, by="integration")
    return store


@pytest.fixture(scope="session")
def dev_identity(mssql_store):
    from importer import environment

    with mssql_store.engine.connect() as conn:
        return environment.read(conn)
