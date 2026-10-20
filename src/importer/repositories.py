"""Repository contracts (design §8.11): Protocols the pipeline codes against.

Two implementations: memory_store (unit tests, real rollback) and mssql_store
(SQL Server via DataEngine/SQLAlchemy Core).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from .models import (
    ArtifactType,
    Declared,
    FileStatus,
    Signature,
    SignatureHit,
    SignatureStatus,
)
from .reject_store import RejectStore


class IllegalTransition(ValueError):
    """The requested status change is not in LEGAL_TRANSITIONS."""


class ConcurrentTransition(RuntimeError):
    """The file is no longer in the expected source status."""


class NotFound(KeyError):
    """No row with that id."""


class RowDataError(Exception):
    """A staging insert failed because of row data (SQLSTATE class 22/23)."""


@dataclass
class LedgerRow:
    file_id: int
    idempotency_key: str
    source: str
    original_filename: str
    project: str
    cls: str
    file_type: str
    status: FileStatus = FileStatus.DISCOVERED
    signature_id: int | None = None
    request_context: dict | None = None
    source_path: str | None = None
    encoding: str | None = None
    attempts: int = 1
    rows_total: int | None = None
    rows_landed: int | None = None
    rows_rejected: int | None = None
    percent_parsed: float | None = None
    percent_loaded: float | None = None
    last_error: str | None = None

    @property
    def declared(self) -> Declared:
        return Declared(self.project, self.cls, self.file_type)


@dataclass
class RegistryRow:
    signature_id: int
    fingerprint: str
    physical_part: str
    xml_namespace: str | None
    status: SignatureStatus
    mapper_version_id: int | None = None
    observed_schema: dict | None = None
    proposal: dict | None = None
    approved_by: str | None = None


@dataclass
class ArtifactRow:
    version_id: int
    artifact_type: ArtifactType
    artifact_key: str
    version_no: int
    definition: dict
    is_active: bool
    created_by: str | None = None
    content_sha256: str | None = None
    approved_by: str | None = None


@dataclass(frozen=True)
class StagedRow:
    file_id: int
    row_identity: str
    signature_id: int
    artifact_version_id: int
    fields: dict
    extras_json: str | None


class LedgerRepository(Protocol):
    def create_or_get(
        self, *, idempotency_key: str, source: str, original_filename: str,
        source_path: str | None, declared: Declared, request_context: dict | None,
    ) -> tuple[LedgerRow, bool]: ...
    def get(self, file_id: int) -> LedgerRow: ...
    def transition(self, file_id: int, src: FileStatus, dst: FileStatus) -> None: ...
    def begin_reentry(self, file_id: int) -> LedgerRow: ...
    def set_signature(self, file_id: int, signature_id: int | None) -> None: ...
    def set_encoding(self, file_id: int, encoding: str | None) -> None: ...
    def set_error(self, file_id: int, message: str | None) -> None: ...
    def files_for_signature(self, signature_id: int) -> list[LedgerRow]: ...
    def record_metrics(
        self, file_id: int, *, rows_total: int, rows_landed: int, rows_rejected: int,
        percent_parsed: float, percent_loaded: float,
    ) -> None: ...


class SignatureRegistry(Protocol):
    def lookup(self, fingerprint: str, xml_namespace: str | None) -> SignatureHit | None: ...
    def get(self, signature_id: int) -> RegistryRow: ...
    def find_active_by_physical_part(self, physical_part: str, xml_namespace: str | None) -> list[RegistryRow]: ...
    def propose(self, sig: Signature, candidates: list[dict]) -> int: ...
    def list_by_status(self, status: SignatureStatus) -> list[RegistryRow]: ...
    def set_proposal(self, signature_id: int, proposal: dict) -> None: ...
    def set_observed(self, signature_id: int, observed: dict) -> None: ...
    def activate(self, signature_id: int, mapper_version_id: int, approved_by: str) -> None: ...
    def register(self, sig: Signature, mapper_version_id: int, approved_by: str) -> int: ...


class ArtifactRepository(Protocol):
    def put_version(
        self, artifact_type: ArtifactType, artifact_key: str, definition: dict, *,
        created_by: str | None, activate: bool = True, content_sha256: str | None = None,
    ) -> ArtifactRow: ...
    def get(self, version_id: int) -> ArtifactRow: ...
    def active_schema(self, schema_id: str) -> ArtifactRow | None: ...
    def active_schemas(self) -> list[ArtifactRow]: ...
    def active(self, artifact_type: ArtifactType, key: str) -> ArtifactRow | None: ...
    def activate(self, version_id: int, *, approved_by: str | None) -> ArtifactRow: ...


class RawZone(Protocol):
    def ensure_tables(self, schema_id: str, columns: list[str]) -> None: ...
    def clear_stage(self, file_id: int, target: str) -> None: ...
    def stage_batch(self, target: str, rows: list[StagedRow]) -> None: ...
    def promote(self, file_id: int, target: str) -> int: ...
    def count_landed(self, file_id: int, target: str) -> int: ...


class AuditLog(Protocol):
    def quarantine(self, file_id: int, reason_code: str, detail: str | None, signature_id: int | None) -> None: ...
    def decision(
        self, file_id: int | None, action_type: str, observed: object, proposed: object,
        applied: bool, approval: str | None,
    ) -> None: ...


class Tx(Protocol):
    ledger: LedgerRepository
    registry: SignatureRegistry
    artifacts: ArtifactRepository
    rejects: RejectStore
    raw: RawZone
    audit: AuditLog


class Store(Protocol):
    def transaction(self) -> AbstractContextManager[Tx]: ...
