from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from importer.data_guard import decide

SHARE = r"\\datawarehouse\incoming_share\templates\run_1\file_1.xlsx"


@pytest.fixture(autouse=True)
def guard_on(monkeypatch):
    """These cases test what the guard catches; the mode tests below override this."""
    monkeypatch.setenv("IMPORTER_DATA_GUARD", "on")


def _call(tool, **tool_input):
    return decide({"tool_name": tool, "tool_input": tool_input})


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": SHARE}),
    ("Read", {"file_path": r"Z:\templates\run_1\file.xlsx"}),
    ("Read", {"file_path": "J:/templates/x.xml"}),
    ("Grep", {"pattern": "SAMPLE", "path": "//datawarehouse/incoming_share"}),
    ("Glob", {"pattern": "**/*.xlsx", "path": r"Z:\\"}),
    ("Bash", {"command": f"cat '{SHARE}'"}),
    ("PowerShell", {"command": "Get-Content 'Z:\\templates\\a.csv'"}),
    ("Bash", {"command": "curl https://acct.blob.core.windows.net/incoming/a.csv"}),
    ("Bash", {"command": "python -c \"c.execute(text('SELECT * FROM [app_relational].[url_template]'))\""}),
    ("Bash", {"command": "sqlcmd -Q \"SELECT TOP 5 * FROM stage.app_excel__plan_variant_benefit\""}),
    ("Bash", {"command": "python -c \"c.execute(text('SELECT raw_payload FROM dbo.reject_repository'))\""}),
    ("Bash", {"command": f".venv/Scripts/importer preview '{SHARE}' --mapper-version 3 --project DC --class PC --type EXCEL"}),
])
def test_reading_file_contents_outside_the_importer_asks(tool, tool_input):
    out = _call(tool, **tool_input)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "file contents" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": r"C:\Users\me\repo\src\importer\onboarding.py"}),
    ("Read", {"file_path": r"C:\Users\me\repo\tests\fixtures\url_template_a.csv"}),
    ("Grep", {"pattern": "app_relational", "path": "src"}),
    ("Bash", {"command": "git log --oneline -3"}),
    ("Bash", {"command": "grep -n app_relational tests/test_schema_tables.py"}),
    ("Bash", {"command": "python -c \"c.execute(text('SELECT COUNT(*) FROM dbo.decision_log'))\""}),
    ("Bash", {"command": ".venv/Scripts/importer pending"}),
])
def test_ordinary_work_is_not_interrupted(tool, tool_input):
    assert _call(tool, **tool_input) is None


def test_hook_entry_point_speaks_the_hook_protocol():
    import os
    env = {**os.environ, "IMPORTER_DATA_GUARD": "on"}
    run = lambda payload: subprocess.run([sys.executable, "-m", "importer.data_guard"], input=payload,
                                         capture_output=True, text=True, timeout=60, env=env)
    asked = run(json.dumps({"tool_name": "Read", "tool_input": {"file_path": SHARE}}))
    assert asked.returncode == 0
    assert json.loads(asked.stdout)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    quiet = run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
    assert quiet.returncode == 0 and quiet.stdout == ""
    broken = run("not json")
    assert json.loads(broken.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


READ_SHARE = {"tool_name": "Read", "tool_input": {"file_path": SHARE}}


def test_off_lets_everything_through(monkeypatch):
    monkeypatch.setenv("IMPORTER_DATA_GUARD", "off")
    assert decide(READ_SHARE) is None


def test_auto_guards_only_while_an_importer_mcp_server_runs(monkeypatch, tmp_path):
    import os

    from importer import data_guard

    monkeypatch.delenv("IMPORTER_DATA_GUARD")  # default: auto
    monkeypatch.setattr(data_guard, "MARKER_DIR", tmp_path)
    assert decide(READ_SHARE) is None                       # no server running: nothing to guard
    marker = data_guard.mark_server_running()               # a live server (this process)
    assert marker.exists() and decide(READ_SHARE)["hookSpecificOutput"]["permissionDecision"] == "ask"
    data_guard.clear_marker(marker)
    assert decide(READ_SHARE) is None and not marker.exists()
    stale = tmp_path / "999999999"                          # a server that died without cleaning up
    stale.write_text("", encoding="utf-8")
    assert decide(READ_SHARE) is None and not stale.exists()


def test_importer_mcp_marks_itself_running_and_clears_on_exit(monkeypatch, tmp_path):
    from importer import cli, data_guard
    from importer.memory_store import MemoryStore

    monkeypatch.setattr(data_guard, "MARKER_DIR", tmp_path)
    seen = []

    class FakeServer:
        def run(self, transport):
            seen.extend(p.name for p in tmp_path.iterdir())

    monkeypatch.setattr("importer.mcp_server.create_server", lambda store, **kw: FakeServer())
    assert cli.main(["mcp"], store=MemoryStore()) == 0
    assert seen == [str(os.getpid())] and list(tmp_path.iterdir()) == []
