"""Claude Code PreToolUse guard: file contents reach a model only through the importer (design §8.19).

Rule: no incoming file's contents are sent to the model outside the importer's MCP
tools, which mask every value. A direct read — of a data share, a Blob URL, raw/stage
rows, reject payloads, or `importer preview` output — needs the user's explicit
permission, so this hook answers "ask" for it. Everything else passes untouched.

Run as `python -m importer.data_guard` with the hook payload on stdin. Payloads that
cannot be parsed are answered with "ask" (fail closed).

The user decides when it applies, through IMPORTER_DATA_GUARD (set it in the "env"
block of any Claude Code settings file):
  auto (default)  guard only while an `importer mcp` server is running on this machine;
  on              always guard;
  off             never guard.
`importer mcp` marks itself running with a marker file named after its process id;
markers of processes that are gone are ignored and removed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

MARKER_DIR = Path(tempfile.gettempdir()) / "importer-mcp"

# Where incoming files live (configurable via IMPORTER_DATA_LOCATIONS env var).
_EXTRA_LOCATIONS = os.environ.get("IMPORTER_DATA_LOCATIONS", "").strip()
_EXTRA_LOC_PART = f"| {_EXTRA_LOCATIONS}" if _EXTRA_LOCATIONS else ""

DATA_LOCATIONS = re.compile(
    rf"""(?ix)
    (?:^|[\s'"=(\[,])[zj]:[\\/]              # mapped data drives
    | [\\/]{{2}}[a-z0-9_.-]+[\\/][a-z0-9_.-]+ # generic UNC shares (\\server\share)
    | \.blob\.core\.windows\.net              # Azure Blob locators
    | \.file\.core\.windows\.net              # Azure Files locators
    {_EXTRA_LOC_PART}
    """
)
# Rows landed from files: raw/stage tables and reject payloads in the control database.
_EXTRA_SCHEMAS = os.environ.get("IMPORTER_GUARDED_SCHEMAS", "").strip()
_EXTRA_SCHEMA_PART = f"| {_EXTRA_SCHEMAS}" if _EXTRA_SCHEMAS else ""

RAW_ZONE = re.compile(
    rf"""(?ix)
    \[?[a-z0-9_]+_[a-z0-9_]+\]?\s*\.\s*\[?[a-z0-9_]+\]?
    | \[?stage\]?\s*\.\s*\[?[a-z0-9_]+__
    | reject_repository | raw_payload
    {_EXTRA_SCHEMA_PART}
    """
)
QUERY = re.compile(r"(?i)\bselect\b|\bsqlcmd\b|read_sql|exec_driver_sql|\.execute\(")
PRINTS_ROWS = re.compile(r"(?i)\bimporter(?:\.exe)?\s+preview\b|importer\.cli\s+preview\b")

REASON = ("Data boundary (design §8.19): this would send incoming file contents to the model outside the "
          "importer's masked MCP tools{what}. Allow only if you explicitly want the model to see real values.")


def _texts(tool_input) -> list[str]:
    if isinstance(tool_input, dict):
        return [v for v in tool_input.values() if isinstance(v, str)]
    return [str(tool_input)]


def _ask(what: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                   "permissionDecisionReason": REASON.format(what=what)}}


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def mark_server_running() -> Path:
    """Called by `importer mcp` at start: the guard's 'auto' mode keys off this marker."""
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    marker = MARKER_DIR / str(os.getpid())
    marker.write_text("", encoding="utf-8")
    return marker


def clear_marker(marker: Path) -> None:
    marker.unlink(missing_ok=True)


def server_running() -> bool:
    if not MARKER_DIR.is_dir():
        return False
    running = False
    for marker in MARKER_DIR.iterdir():
        if marker.name.isdigit() and _alive(int(marker.name)):
            running = True
        else:
            clear_marker(marker)  # left behind by a server that died
    return running


def _active() -> bool:
    mode = os.environ.get("IMPORTER_DATA_GUARD", "auto").strip().lower()
    if mode == "off":
        return False
    if mode == "on":
        return True
    return server_running()


def decide(payload: dict) -> dict | None:
    """The hook's answer for one tool call, or None to let it through unchanged."""
    if not _active():
        return None
    texts = _texts(payload.get("tool_input", {}))
    joined = "\n".join(texts)
    if DATA_LOCATIONS.search(joined):
        return _ask(" (a data share or storage locator)")
    if PRINTS_ROWS.search(joined):
        return _ask(" (`importer preview` prints real rows)")
    if QUERY.search(joined) and RAW_ZONE.search(joined):
        return _ask(" (a query of raw/stage rows or reject payloads)")
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        answer = decide(payload if isinstance(payload, dict) else {})
    except Exception as exc:  # fail closed: an unreadable payload gets a prompt, never a pass
        answer = _ask(f" (the guard could not read this call: {type(exc).__name__})")
    if answer is not None:
        sys.stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
