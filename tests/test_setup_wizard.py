from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from importer import cli, db
from importer.setup_wizard import (
    _ensure_gitignore,
    _prompt,
    _prompt_bool,
    apply_schema,
    check_or_create_database,
    run_setup,
    write_database_env,
)


def test_ensure_gitignore_creates_file_if_missing(tmp_path: Path):
    _ensure_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "database.env" in content


def test_ensure_gitignore_appends_if_not_present(tmp_path: Path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n.venv/\n", encoding="utf-8")
    _ensure_gitignore(tmp_path)
    content = gitignore.read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert "database.env" in content


def test_ensure_gitignore_does_not_duplicate(tmp_path: Path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("database.env\n", encoding="utf-8")
    _ensure_gitignore(tmp_path)
    assert gitignore.read_text(encoding="utf-8") == "database.env\n"


def test_write_database_env_with_password(tmp_path: Path):
    doc = {
        "type": "mssql",
        "server": "myserver",
        "database": "mydb",
        "UN": "myuser",
        "PW": "secret_password",
    }
    path = write_database_env(tmp_path, doc)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "myserver" in content
    assert "secret_password" in content
    loaded = db.load_connection_document(path)
    assert loaded["server"] == "myserver"
    assert loaded["UN"] == "myuser"
    assert loaded["PW"] == "secret_password"


def test_write_database_env_without_password(tmp_path: Path):
    doc = {
        "type": "mssql",
        "server": "myserver",
        "database": "mydb",
        "UN": "myuser",
    }
    path = write_database_env(tmp_path, doc)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "secret_password" not in content
    loaded = db.load_connection_document(path)
    assert loaded["server"] == "myserver"
    assert loaded["UN"] == "myuser"
    assert "PW" not in loaded


def test_prompt_helpers(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "custom_val")
    assert _prompt("Enter value", default="default_val") == "custom_val"

    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt("Enter value", default="default_val") == "default_val"

    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert _prompt_bool("Confirm?", default=False) is True

    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert _prompt_bool("Confirm?", default=True) is False

    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_bool("Confirm?", default=True) is True


def test_check_or_create_database_existing():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.scalar_one_or_none.return_value = 5  # db_id found

    with patch("importer.db._connection_engine", return_value=mock_engine):
        exists = check_or_create_database(
            server="sql1",
            database="import_agent_dev",
            doc={"type": "mssql", "server": "sql1", "database": "import_agent_dev"},
            interactive=False,
        )
        assert exists is True


def test_check_or_create_database_creates_when_missing():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.scalar_one_or_none.return_value = None  # db_id not found

    with patch("importer.db._connection_engine", return_value=mock_engine):
        created = check_or_create_database(
            server="sql1",
            database="new_db",
            doc={"type": "mssql", "server": "sql1", "database": "new_db"},
            auto_create=True,
            interactive=False,
        )
        assert created is True
        mock_conn.execution_options().exec_driver_sql.assert_called_with("CREATE DATABASE [new_db]")


def test_missing_password_in_non_interactive_raises():
    doc = {
        "type": "mssql",
        "server": "sql1",
        "database": "mydb",
        "UN": "sqluser",
        "trusted": "no",
    }
    with patch("importer.db.os.environ.get", return_value=None):
        with patch("importer.db.get_session_password", return_value=None):
            with patch("sys.stdin.isatty", return_value=False):
                with pytest.raises(db.ConfigError, match="Password is required for SQL Server user 'sqluser'"):
                    db._build_pyodbc_connection_string(server="sql1", database="mydb", doc=doc)


def test_run_setup_non_interactive(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("importer.config.home", lambda: tmp_path)

    args = MagicMock()
    args.server = "testserver"
    args.database = "test_dev"
    args.auth = "sql"
    args.user = "sa"
    args.password = "StrongPassword!"
    args.save_password = False
    args.create_db = True
    args.skip_schema = True
    args.non_interactive = True

    with patch("importer.setup_wizard.check_or_create_database", return_value=True):
        ret = run_setup(args)
        assert ret == 0

    env_path = tmp_path / "database.env"
    assert env_path.exists()
    doc = db.load_connection_document(env_path)
    assert doc["server"] == "testserver"
    assert doc["database"] == "test_dev"
    assert doc["UN"] == "sa"
    # Password not saved on disk
    assert "PW" not in doc
