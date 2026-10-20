"""Agent harness — reserved (design §1, §4, §5; M5).

M2b does NOT use this module: onboarding's model is the user's own Claude client
working through the importer's MCP server (mcp_server.py; Design Decisions §8.19).
This stub stays for a possible later unattended runner (Claude Agent SDK) that
would call the same onboarding functions. Its outputs would be recorded as data
(proposed signatures, versioned rules, decision-log entries), never live code.

Two entry points, both producing *proposals* that deterministic code later
verifies and a human (for new schemas) activates:

* :func:`diagnose_unknown` — a detector miss. Classify the file (deterministic
  hints first, model fallback for rare "must-analyze-first" files) and, if it is
  a new schema, draft a signature + mapper + landing contract as a ``proposed``
  registry row for sign-off.
* :func:`diagnose_rejects` — a cluster of rejects sharing a reason code.
  Propose a versioned repair rule and **verify it against the captured payloads**
  (``reclaim.select_newly_landable``) before it is activated.

Wiring target: ``claude-agent-sdk`` with a sandboxed, read-only-against-captured
-payload execution slot. Every action writes a ``decision_log`` row.
"""

from __future__ import annotations

from pathlib import Path


def diagnose_unknown(path: str | Path):
    """Diagnose a detector miss; propose a signature (gated). STATUS: stub (M5/M7)."""
    raise NotImplementedError("agent slow path (unknown signature) is milestone M5/M7")


def diagnose_rejects(schema_id: str):
    """Diagnose a reject cluster; propose + verify a repair rule. STATUS: stub (M5)."""
    raise NotImplementedError("agent slow path (reject repair) is milestone M5")
