from __future__ import annotations

from pathlib import Path

import pytest

from importer.environment import IDENTITY_TABLE_SQL, Identity, banner, dev_guard

DDL = (Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql").read_text(encoding="utf-8")


@pytest.mark.parametrize("environment,database,allowed", [
    ("DEV", "import_agent_dev", True),
    ("DEV", "IMPORT_AGENT_DEV", True),
    ("DEV", "import_agent", False),
    ("PROD", "import_agent_dev", False),
    (None, "import_agent_dev", False),
    (None, "import_agent", False),
])
def test_dev_guard_truth_table(environment, database, allowed):
    assert (dev_guard(environment, database) is None) is allowed


def test_banner():
    assert banner(Identity("2018NPI", "import_agent_dev", "DEV")) == "importer @ 2018NPI/import_agent_dev [DEV]"
    assert banner(Identity("s", "d", None)) == "importer @ s/d [UNSET]"


def test_identity_table_is_in_the_create_script_and_never_rebuilt():
    assert IDENTITY_TABLE_SQL in DDL
    from importer.schema_tables import REBUILD_DROP_SQL
    assert "database_identity" not in REBUILD_DROP_SQL


class _FakeConn:
    """Answers the identity / foreign-table probes and records every statement sent."""

    def __init__(self, *, identity_exists=False, environment=None, foreign_tables=0):
        self.identity_exists, self.environment, self.foreign_tables = identity_exists, environment, foreign_tables
        self.sent: list[str] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.sent.append(sql)
        if "OBJECT_ID(N'dbo.database_identity')" in sql and sql.lstrip().upper().startswith("SELECT OBJECT_ID"):
            value = 1 if self.identity_exists else None
        elif sql.lstrip().upper().startswith("SELECT ENVIRONMENT"):
            value = self.environment
        elif "sys.tables" in sql:
            value = self.foreign_tables
        else:
            value = None
        return _Result(value)

    def exec_driver_sql(self, sql):
        self.sent.append(sql)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


def _writes(conn):
    return [s for s in conn.sent if not s.lstrip().upper().startswith("SELECT")]


def test_init_refuses_a_database_with_tables_it_does_not_own_and_writes_nothing():
    from importer.environment import GuardError, init

    conn = _FakeConn(foreign_tables=412)
    with pytest.raises(GuardError, match="412 table"):
        init(conn, "DEV", by="tony")
    assert _writes(conn) == []


def test_init_refuses_a_second_marking_and_writes_nothing():
    from importer.environment import GuardError, init

    conn = _FakeConn(identity_exists=True, environment="DEV")
    with pytest.raises(GuardError, match="already DEV"):
        init(conn, "DEV", by="tony")
    assert _writes(conn) == []


def test_init_marks_an_empty_database():
    from importer.environment import init

    conn = _FakeConn()
    init(conn, "dev", by="tony")
    writes = _writes(conn)
    assert len(writes) == 2 and "CREATE TABLE dbo.database_identity" in writes[0] and "INSERT" in writes[1]
