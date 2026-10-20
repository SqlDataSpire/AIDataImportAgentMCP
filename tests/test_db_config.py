"""database.env parsing and the lazy-DataEngine rule (spec §11)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from importer.db import ConfigError, load_connection_document


def _env(tmp_path, body: str):
    path = tmp_path / "database.env"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_importer_entry(tmp_path):
    path = _env(
        tmp_path,
        """databases = '{"importer": {"type": "mssql", "server": "dev01", "database": "imp_dev", "trusted": "yes"}}'\n""",
    )
    doc = load_connection_document(path)
    assert doc["server"] == "dev01"
    assert doc["database"] == "imp_dev"
    assert doc["trusted"] == "yes"


def test_loads_importer_entry_with_password(tmp_path):
    path = _env(
        tmp_path,
        """databases = '{"importer": {"type": "mssql", "server": "s", "database": "d", "UN": "u", "PW": "secret"}}'\n""",
    )
    doc = load_connection_document(path)
    assert doc["UN"] == "u"
    assert doc["PW"] == "secret"


def test_env_password_override(tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORTER_PASSWORD", "envsecret")
    path = _env(
        tmp_path,
        """databases = '{"importer": {"type": "mssql", "server": "s", "database": "d", "UN": "u"}}'\n""",
    )
    doc = load_connection_document(path)
    assert doc["UN"] == "u"
    assert doc["PW"] == "envsecret"


def test_missing_entry(tmp_path):
    path = _env(tmp_path, """databases = '{"main": {"type": "mssql", "server": "s", "database": "d"}}'\n""")
    with pytest.raises(ConfigError, match="no 'importer'"):
        load_connection_document(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_connection_document(tmp_path / "nope.env")


def test_requires_mssql_type(tmp_path):
    path = _env(tmp_path, """databases = '{"importer": {"type": "postgres", "server": "s", "database": "d"}}'\n""")
    with pytest.raises(ConfigError, match="mssql"):
        load_connection_document(path)


def test_importing_db_does_not_import_dataengine():
    code = "import sys, importer.db; print('DataEngine' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_read_only_guard_allows_selects_and_refuses_writes():
    from sqlalchemy import create_engine, text

    from importer.db import ReadOnlyViolation, read_only

    engine = read_only(create_engine("sqlite://"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
        assert conn.execute(text("  -- comment\n select 2")).scalar_one() == 2
        for sql in ("CREATE TABLE t (a INT)", "INSERT INTO t VALUES (1)", "SELECT 1 INTO t2",
                    "SELECT 1; DROP TABLE t", "EXEC sp_who", "UPDATE t SET a = 1"):
            with pytest.raises(ReadOnlyViolation):
                conn.exec_driver_sql(sql)


def test_engine_for_is_read_only_and_keeps_stdout_clean(tmp_path, monkeypatch, capsys):
    import types

    from sqlalchemy import create_engine

    from importer.db import ReadOnlyViolation, engine_for

    class FakeConnection:
        def __init__(self, **kwargs):
            print("using: ODBC Driver 18 for SQL Server")
            self.engine = create_engine("sqlite://")

    fake = types.ModuleType("DataEngine.mssql")
    fake.SqlConnectionObject = FakeConnection
    monkeypatch.setitem(sys.modules, "DataEngine", types.ModuleType("DataEngine"))
    monkeypatch.setitem(sys.modules, "DataEngine.mssql", fake)
    path = _env(tmp_path, """databases = '{"importer": {"type": "mssql", "server": "s", "database": "d", "trusted": "yes"}}'\n""")
    engine = engine_for("TEST_SERVER", "APP_DB", path)
    assert capsys.readouterr().out == ""
    with engine.connect() as conn, pytest.raises(ReadOnlyViolation):
        conn.exec_driver_sql("CREATE TABLE t (a INT)")


def test_native_pyodbc_connection_string_builder():
    from importer.db import _build_pyodbc_connection_string

    conn_str = _build_pyodbc_connection_string(
        server="myserver",
        database="mydb",
        doc={"trusted": "yes"}
    )
    assert "SERVER=myserver" in conn_str
    assert "DATABASE=mydb" in conn_str
    assert "Trusted_Connection=yes" in conn_str
    assert "TrustServerCertificate=yes" in conn_str

    auth_str = _build_pyodbc_connection_string(
        server="sql1",
        database="mydb",
        doc={"UN": "sa", "PW": "secret"}
    )
    assert "SERVER=sql1" in auth_str
    assert "UID=sa" in auth_str
    assert "PWD=secret" in auth_str


def test_native_connection_engine_without_dataengine(monkeypatch):
    import sys
    from importer.db import _connection_engine

    monkeypatch.delitem(sys.modules, "DataEngine", raising=False)
    monkeypatch.delitem(sys.modules, "DataEngine.mssql", raising=False)

    doc = {"url": "sqlite:///:memory:"}
    engine = _connection_engine(name="test", server="s", database="d", doc=doc)
    assert engine.dialect.name == "sqlite"


def test_flat_env_file_support(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SERVER=flat-server\nDATABASE=flat-db\nTRUSTED=yes\n", encoding="utf-8")
    doc = load_connection_document(env_file)
    assert doc["server"] == "flat-server"
    assert doc["database"] == "flat-db"
    assert doc["trusted"] == "yes"


def test_current_connection_info(monkeypatch, tmp_path):
    from importer import db

    env_file = tmp_path / "database.env"
    env_file.write_text("SERVER=test-srv\nDATABASE=test-db\nUN=test-user\n", encoding="utf-8")
    monkeypatch.setattr(db, "database_env_path", lambda: env_file)

    info = db.current_connection_info()
    assert info["server"] == "test-srv"
    assert info["database"] == "test-db"
    assert info["user"] == "test-user"
    assert info["authenticated"] is False

    db.set_session_password("test-pwd")
    info2 = db.current_connection_info()
    assert info2["authenticated"] is True
    db.set_session_password(None)


