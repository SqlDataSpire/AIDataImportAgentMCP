from __future__ import annotations

import json

import pytest

from importer import cli
from importer.memory_store import MemoryStore
from tests.support import URL_SCHEMA, URL_SPEC_A, fixture


@pytest.fixture
def store():
    s = MemoryStore()
    from importer.admin import register_schema
    from tests.support import configure
    configure(s, "app_db")
    with s.transaction() as tx:
        register_schema(tx, "app_db.relational.url_template", URL_SCHEMA["app_db.relational.url_template"], created_by="test")
    return s


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def test_mapper_add_signature_register_and_process(store, tmp_path, capsys):
    spec = tmp_path / "urls.json"
    spec.write_text(json.dumps(URL_SPEC_A), encoding="utf-8")
    assert cli.main(["mapper", "add", str(spec), "--key", "urls_a"], store=store) == 0
    mapper = _json_out(capsys)
    sample = str(fixture("url_template_a.csv"))
    assert cli.main(["signature", "register", sample, "--project", "vt", "--class", "pc", "--type", "csv",
                     "--mapper-version", str(mapper["version_id"])], store=store) == 0
    assert _json_out(capsys)["fingerprint"].startswith("VT|PC|CSV|h:")
    code = cli.main(["process", sample, "--project", "VT", "--class", "PC", "--type", "CSV", "--source", "cli",
                     "--context", "run_id=9"], store=store)
    result = _json_out(capsys)
    assert code == 0 and result["status"] == "completed" and result["rows_landed"] == 2


def test_process_unknown_file_exits_2(store, capsys):
    code = cli.main(["process", str(fixture("url_template_b.csv")), "--project", "VT", "--class", "PC",
                     "--type", "CSV", "--source", "cli"], store=store)
    result = _json_out(capsys)
    assert code == 2 and result["quarantine_reason"] == "unroutable"
    assert result["proposal_summary"].startswith("looks like app_db.relational.url_template")


def test_preview(store, tmp_path, capsys):
    spec = tmp_path / "urls.json"
    spec.write_text(json.dumps(URL_SPEC_A), encoding="utf-8")
    cli.main(["mapper", "add", str(spec), "--key", "urls_a"], store=store)
    version = _json_out(capsys)["version_id"]
    assert cli.main(["preview", str(fixture("url_template_a.csv")), "--mapper-version", str(version),
                     "--project", "VT", "--class", "PC", "--type", "CSV"], store=store) == 0
    assert _json_out(capsys)["counts"] == {"app_db.relational.url_template": 2}


def test_bad_context_pair_is_rejected(store):
    with pytest.raises(SystemExit):
        cli.main(["process", str(fixture("url_template_a.csv")), "--project", "VT", "--class", "PC",
                  "--type", "CSV", "--source", "cli", "--context", "novalue"], store=store)


class _FakeEngine:
    def connect(self):
        from contextlib import nullcontext
        return nullcontext(object())


def test_rebuild_refuses_without_dev_identity(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("s", "import_agent_dev", "PROD"))
    assert cli.main(["db", "rebuild", "--confirm", "import_agent_dev"]) == 1
    assert "not DEV" in capsys.readouterr().err


def test_rebuild_refuses_when_the_name_lacks_dev(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("s", "import_agent", "DEV"))
    assert cli.main(["db", "rebuild", "--confirm", "import_agent"]) == 1
    assert "does not contain 'dev'" in capsys.readouterr().err


def test_rebuild_requires_matching_database_name(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("s", "imp_dev", "DEV"))
    assert cli.main(["db", "rebuild", "--confirm", "wrong"]) == 1
    assert "did not match" in capsys.readouterr().err


def test_config_set_show_unset(store, capsys):
    assert cli.main(["config", "set", "destination", "event_db", "--server", "test_server", "--database", "EVENT_DB"], store=store) == 0
    capsys.readouterr()
    assert cli.main(["config", "set", "samples.max_values", "3"], store=store) == 0
    capsys.readouterr()
    assert cli.main(["config", "show"], store=store) == 0
    shown = _json_out(capsys)
    assert shown["destinations"]["EVENT_DB"] == {"server": "test_server", "database": "EVENT_DB"}
    assert shown["samples"]["max_values"] == 3
    assert cli.main(["config", "unset", "destination", "EVENT_DB"], store=store) == 0
    capsys.readouterr()
    cli.main(["config", "show"], store=store)
    assert "EVENT_DB" not in _json_out(capsys)["destinations"]


def test_schema_import_from_file(store, tmp_path, capsys):
    cols = tmp_path / "cols.json"
    cols.write_text('["ndc", "name"]', encoding="utf-8")
    cli.main(["config", "set", "destination", "RX", "--server", "s", "--database", "RX"], store=store)
    capsys.readouterr()
    assert cli.main(["schema", "import", "RX.dbo.drugs", "--from-file", str(cols)], store=store) == 0
    assert _json_out(capsys)["added"][0]["address"] == "rx.dbo.drugs"


def test_pending(store, capsys):
    cli.main(["process", str(fixture("url_template_b.csv")), "--project", "VT", "--class", "PC",
              "--type", "CSV", "--source", "cli"], store=store)
    capsys.readouterr()
    assert cli.main(["pending"], store=store) == 0
    [item] = _json_out(capsys)
    assert item["suggested_path"] == "fallback" and item["waiting_files"] == 1


def test_mcp_keeps_stdout_clean_for_the_protocol(monkeypatch, capsys):
    # DataEngine prints "using: ODBC Driver ..." to stdout when the engine is built; on
    # `importer mcp` stdout is the JSON-RPC stream, so nothing else may be written there.
    def noisy_engine(*args, **kwargs):
        print("using: ODBC Driver 18 for SQL Server")
        return object()

    class FakeServer:
        def run(self, transport):
            assert transport == "stdio"

    monkeypatch.setattr("importer.db.get_engine", noisy_engine)
    monkeypatch.setattr("importer.mssql_store.MssqlStore", lambda engine: MemoryStore())
    monkeypatch.setattr("importer.mcp_server.create_server", lambda store, **kw: FakeServer())
    assert cli.main(["mcp"]) == 0
    out = capsys.readouterr()
    assert out.out == "" and "using: ODBC" in out.err


def test_db_init_requires_the_typed_database_name(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("TEST_SERVER", "APP_DB", None))
    monkeypatch.setattr("importer.environment.init", lambda *a, **k: pytest.fail("must not write"))
    assert cli.main(["db", "init", "--environment", "DEV", "--confirm", "import_agent_dev"]) == 1
    err = capsys.readouterr().err
    assert "importer @ TEST_SERVER/APP_DB [UNSET]" in err and "did not match" in err


def test_mcp_cli_connection_parameters(monkeypatch):
    from importer import db

    configured = {}

    def mock_configure(**kwargs):
        configured.update(kwargs)

    class FakeServer:
        def run(self, transport):
            pass

    monkeypatch.setattr(db, "configure_connection", mock_configure)
    monkeypatch.setattr("importer.mssql_store.MssqlStore", lambda engine: MemoryStore())
    monkeypatch.setattr("importer.mcp_server.create_server", lambda store, **kw: FakeServer())

    cli.main(["mcp", "--server", "test-srv", "--database", "test-db", "--trusted"])
    assert configured.get("server") == "test-srv"
    assert configured.get("database") == "test-db"
    assert configured.get("trusted") is True
    db.reset_runtime_config()

