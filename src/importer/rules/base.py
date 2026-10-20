"""Repair rule contract — rule-as-data (design §5).

The hard boundary: the agent never live-edits production code. It authors
versioned repair *rules* (stored in ``artifact_versions``) that this stable
code executes. A rule inspects a captured raw payload and either returns a
repaired payload (landable) or ``None`` (still not landable — stays rejected).

``try_repair`` MUST be a pure, deterministic function of its input so that
re-running against a captured payload in the sandbox is reproducible (§5.4).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class RepairRule(ABC):
    """A versioned, data-defined repair attempt for one schema's rejects."""

    #: schema this rule is attached to.
    schema_id: str
    #: version number within (schema_id, 'rule') — never edited in place (§2.3).
    version_no: int

    @abstractmethod
    def try_repair(self, raw_payload: str) -> str | None:
        """Attempt to make ``raw_payload`` landable.

        Return the repaired payload if it now lands, else ``None``. Must be
        deterministic: same input -> same output, no side effects.
        """
        raise NotImplementedError
