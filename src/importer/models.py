"""Core domain models for the import agent.

Shared vocabulary across the deterministic fast path and the agent slow path.
Mirrors sql/001_schema.sql and the design record (Design Decisions §2, §3, §8).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


class FileStatus(str, Enum):
    """Ledger status lifecycle for a file (design §3, §8.12).

        discovered -> detected -> landed -> promoted -> completed

    with branches to ``quarantined`` (cannot start) or ``failed`` (control-plane
    error). Both may re-enter at ``discovered`` via retry / confirm-and-bind.
    """

    DISCOVERED = "discovered"
    DETECTED = "detected"
    LANDED = "landed"
    PROMOTED = "promoted"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


LEGAL_TRANSITIONS: dict[FileStatus, frozenset[FileStatus]] = {
    FileStatus.DISCOVERED: frozenset({FileStatus.DETECTED, FileStatus.QUARANTINED, FileStatus.FAILED}),
    FileStatus.DETECTED: frozenset({FileStatus.LANDED, FileStatus.QUARANTINED, FileStatus.FAILED}),
    FileStatus.LANDED: frozenset({FileStatus.PROMOTED, FileStatus.FAILED}),
    FileStatus.PROMOTED: frozenset({FileStatus.COMPLETED, FileStatus.FAILED}),
    FileStatus.COMPLETED: frozenset(),
    FileStatus.QUARANTINED: frozenset({FileStatus.DISCOVERED}),
    FileStatus.FAILED: frozenset({FileStatus.DISCOVERED}),
}


def can_transition(src: FileStatus, dst: FileStatus) -> bool:
    """Whether ``src -> dst`` is a legal status transition (§3, §8.12)."""
    return dst in LEGAL_TRANSITIONS[src]


class ArtifactType(str, Enum):
    """Versioned artifact kinds in ``artifact_versions`` (§2, §8.10, §8.17)."""

    SCHEMA = "schema"
    MAPPER = "mapper"
    RULE = "rule"
    CONFIG = "config"


class RejectStatus(str, Enum):
    REJECTED = "rejected"
    RECLAIMED = "reclaimed"


class SignatureStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    RETIRED = "retired"


FILE_TYPES = frozenset({"CSV", "TSV", "PIPE", "DELIMITED", "EXCEL", "JSON", "NDJSON", "XML"})
DELIMITED_TYPES = frozenset({"CSV", "TSV", "PIPE", "DELIMITED"})
_PART = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidDeclaration(ValueError):
    """The requestor's declared project / class / file type is malformed (§8.2)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Declared:
    """Requestor-declared parameters (§8.2); the signature's namespace."""

    project: str
    cls: str
    file_type: str

    @classmethod
    def normalize(klass, project: str, cls: str, file_type: str) -> "Declared":
        parts: list[str] = []
        for label, value in (("project", project), ("class", cls), ("file_type", file_type)):
            value = (value or "").strip().upper()
            if not _PART.match(value):
                raise InvalidDeclaration(f"{label} must match [A-Za-z0-9_-]+, got {value!r}")
            parts.append(value)
        if parts[2] not in FILE_TYPES:
            raise InvalidDeclaration(f"file_type must be one of {sorted(FILE_TYPES)}, got {parts[2]!r}")
        return klass(*parts)

    @property
    def prefix(self) -> str:
        return f"{self.project}|{self.cls}|{self.file_type}"


@dataclass(frozen=True)
class Signature:
    """A file's computed signature (§8.6)."""

    fingerprint: str
    physical_part: str
    xml_namespace: str | None
    observed: dict


@dataclass(frozen=True)
class SignatureHit:
    """Result of a successful registry lookup (§4, §8.11)."""

    signature_id: int
    fingerprint: str
    mapper_version_id: int
    xml_namespace: str | None


@dataclass(frozen=True)
class LandRow:
    """One landable row for ``raw.<target>`` (§8.9)."""

    target: str
    row_identity: str
    fields: dict[str, str | None]
    extras: dict | None = None


@dataclass(frozen=True)
class RejectRow:
    """One row that cannot land; becomes a ``reject_repository`` row (§8.9)."""

    target: str | None
    row_identity: str
    raw_payload: str
    reason_code: str
    detail: str | None = None


@dataclass
class Reject:
    """One rejected row (grain of ``reject_repository``; design §2.4a, §7)."""

    file_id: int
    row_identity: str
    raw_payload: str
    reason_code: str
    schema_id: str | None = None
    reason_detail: str | None = None
    status: RejectStatus = RejectStatus.REJECTED
    created_at: datetime = field(default_factory=_utcnow)
    reclaimed_at: datetime | None = None

    @property
    def key(self) -> tuple[int, str]:
        """The uniqueness key ``(file_id, row_identity)`` — the reclaim key."""
        return (self.file_id, self.row_identity)


@dataclass
class FileMetrics:
    """Per-file metrics reported to the caller (design §7)."""

    rows_total: int = 0
    rows_landed: int = 0
    rows_rejected: int = 0
    percent_parsed: float | None = None
    percent_loaded: float | None = None


@dataclass(frozen=True)
class FileResult:
    """What ``process()`` returns (§8.12). ``to_dict()`` is the agent-tool payload."""

    file_id: int
    status: FileStatus
    rows_total: int = 0
    rows_landed: int = 0
    rows_rejected: int = 0
    percent_parsed: float | None = None
    percent_loaded: float | None = None
    quarantine_reason: str | None = None
    proposal_summary: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data
