"""In-memory Store with real rollback semantics (unit tests, design §8.11).

Each transaction works on a deep copy of the state and swaps it in only on a
clean exit, so a test can prove that a failed step left nothing behind.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from .metrics import monotonic_update
from .models import (
    ArtifactType,
    Declared,
    FileStatus,
    Signature,
    SignatureHit,
    SignatureStatus,
    can_transition,
)
from .proposals import with_candidates
from .reject_store import InMemoryRejectStore
from .repositories import (
    ArtifactRow,
    ConcurrentTransition,
    IllegalTransition,
    LedgerRow,
    NotFound,
    RegistryRow,
    RowDataError,
    StagedRow,
)


@dataclass
class RawFault:
    """Test hook: make staging/promote fail on purpose."""

    bad_identities: set[str] = field(default_factory=set)
    infra_on_stage: bool = False
    infra_on_promote: bool = False


@dataclass
class _State:
    ledger: dict[int, LedgerRow] = field(default_factory=dict)
    registry: dict[int, RegistryRow] = field(default_factory=dict)
    artifacts: dict[int, ArtifactRow] = field(default_factory=dict)
    rejects: InMemoryRejectStore = field(default_factory=InMemoryRejectStore)
    columns: dict[str, list[str]] = field(default_factory=dict)
    stage: dict[str, list[StagedRow]] = field(default_factory=dict)
    raw: dict[str, dict[tuple[int, str], StagedRow]] = field(default_factory=dict)
    quarantine: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: {"file": 0, "sig": 0, "art": 0})

    def next_id(self, kind: str) -> int:
        self.counters[kind] += 1
        return self.counters[kind]


class MemoryLedger:
    def __init__(self, state: _State):
        self.s = state

    def _row(self, file_id: int) -> LedgerRow:
        try:
            return self.s.ledger[file_id]
        except KeyError:
            raise NotFound(file_id) from None

    def create_or_get(self, *, idempotency_key, source, original_filename, source_path, declared: Declared, request_context):
        for row in self.s.ledger.values():
            if row.idempotency_key == idempotency_key:
                return replace(row), False
        file_id = self.s.next_id("file")
        row = LedgerRow(
            file_id=file_id, idempotency_key=idempotency_key, source=source,
            original_filename=original_filename, project=declared.project, cls=declared.cls,
            file_type=declared.file_type, request_context=request_context, source_path=source_path,
        )
        self.s.ledger[file_id] = row
        return replace(row), True

    def get(self, file_id):
        return replace(self._row(file_id))

    def transition(self, file_id, src, dst):
        if not can_transition(src, dst):
            raise IllegalTransition(f"{src.value} -> {dst.value}")
        row = self._row(file_id)
        if row.status is not src:
            raise ConcurrentTransition(f"file {file_id} is {row.status.value}, not {src.value}")
        row.status = dst

    def begin_reentry(self, file_id):
        row = self._row(file_id)
        if row.status not in (FileStatus.QUARANTINED, FileStatus.FAILED):
            raise IllegalTransition(f"re-entry needs quarantined or failed, file {file_id} is {row.status.value}")
        self.transition(file_id, row.status, FileStatus.DISCOVERED)
        row.attempts += 1
        row.last_error = None
        return replace(row)

    def set_signature(self, file_id, signature_id):
        self._row(file_id).signature_id = signature_id

    def set_encoding(self, file_id, encoding):
        self._row(file_id).encoding = encoding

    def set_error(self, file_id, message):
        self._row(file_id).last_error = message

    def record_metrics(self, file_id, *, rows_total, rows_landed, rows_rejected, percent_parsed, percent_loaded):
        row = self._row(file_id)
        row.rows_total = rows_total
        row.rows_landed = rows_landed
        row.rows_rejected = rows_rejected
        row.percent_parsed = percent_parsed
        row.percent_loaded = monotonic_update(row.percent_loaded, percent_loaded)

    def files_for_signature(self, signature_id):
        return [replace(r) for r in sorted(self.s.ledger.values(), key=lambda r: r.file_id)
                if r.signature_id == signature_id]


class MemoryRegistry:
    def __init__(self, state: _State):
        self.s = state

    def _by_fp(self, fingerprint: str) -> RegistryRow | None:
        return next((r for r in self.s.registry.values() if r.fingerprint == fingerprint), None)

    def lookup(self, fingerprint, xml_namespace):
        row = self._by_fp(fingerprint)
        if row is None or row.status is not SignatureStatus.ACTIVE or row.mapper_version_id is None:
            return None
        if row.xml_namespace != xml_namespace:
            return None
        return SignatureHit(row.signature_id, row.fingerprint, row.mapper_version_id, row.xml_namespace)

    def _row(self, signature_id) -> RegistryRow:
        try:
            return self.s.registry[signature_id]
        except KeyError:
            raise NotFound(signature_id) from None

    def get(self, signature_id):
        return copy.deepcopy(self._row(signature_id))

    def find_active_by_physical_part(self, physical_part, xml_namespace):
        return [
            copy.deepcopy(r) for r in self.s.registry.values()
            if r.physical_part == physical_part and r.status is SignatureStatus.ACTIVE and r.xml_namespace == xml_namespace
        ]

    def propose(self, sig: Signature, candidates):
        existing = self._by_fp(sig.fingerprint)
        if existing is not None:
            if existing.status is SignatureStatus.PROPOSED:
                existing.observed_schema = sig.observed
                existing.proposal = with_candidates(existing.proposal, candidates)
            return existing.signature_id
        sid = self.s.next_id("sig")
        self.s.registry[sid] = RegistryRow(
            signature_id=sid, fingerprint=sig.fingerprint, physical_part=sig.physical_part,
            xml_namespace=sig.xml_namespace, status=SignatureStatus.PROPOSED,
            observed_schema=sig.observed, proposal=with_candidates(None, candidates),
        )
        return sid

    def list_by_status(self, status):
        return [copy.deepcopy(r) for r in sorted(self.s.registry.values(), key=lambda r: r.signature_id)
                if r.status is status]

    def set_proposal(self, signature_id, proposal):
        self._row(signature_id).proposal = copy.deepcopy(proposal)

    def set_observed(self, signature_id, observed):
        self._row(signature_id).observed_schema = copy.deepcopy(observed)

    def activate(self, signature_id, mapper_version_id, approved_by):
        row = self._row(signature_id)
        row.status = SignatureStatus.ACTIVE
        row.mapper_version_id = mapper_version_id
        row.approved_by = approved_by

    def register(self, sig: Signature, mapper_version_id, approved_by):
        existing = self._by_fp(sig.fingerprint)
        if existing is not None:
            existing.status = SignatureStatus.ACTIVE
            existing.mapper_version_id = mapper_version_id
            existing.physical_part = sig.physical_part
            existing.xml_namespace = sig.xml_namespace
            existing.observed_schema = sig.observed
            existing.approved_by = approved_by
            return existing.signature_id
        sid = self.s.next_id("sig")
        self.s.registry[sid] = RegistryRow(
            signature_id=sid, fingerprint=sig.fingerprint, physical_part=sig.physical_part,
            xml_namespace=sig.xml_namespace, status=SignatureStatus.ACTIVE,
            mapper_version_id=mapper_version_id, observed_schema=sig.observed, approved_by=approved_by,
        )
        return sid


class MemoryArtifacts:
    def __init__(self, state: _State):
        self.s = state

    def put_version(self, artifact_type, artifact_key, definition, *, created_by, activate=True, content_sha256=None):
        same = [a for a in self.s.artifacts.values() if a.artifact_type is artifact_type and a.artifact_key == artifact_key]
        version_no = 1 + max((a.version_no for a in same), default=0)
        if activate:
            for a in same:
                a.is_active = False
        vid = self.s.next_id("art")
        row = ArtifactRow(
            version_id=vid, artifact_type=artifact_type, artifact_key=artifact_key, version_no=version_no,
            definition=copy.deepcopy(definition), is_active=activate, created_by=created_by,
            content_sha256=content_sha256,
        )
        self.s.artifacts[vid] = row
        return replace(row)

    def get(self, version_id):
        try:
            return replace(self.s.artifacts[version_id])
        except KeyError:
            raise NotFound(version_id) from None

    def active(self, artifact_type, key):
        return next(
            (replace(a) for a in self.s.artifacts.values()
             if a.artifact_type is artifact_type and a.artifact_key == key and a.is_active),
            None,
        )

    def activate(self, version_id, *, approved_by):
        try:
            row = self.s.artifacts[version_id]
        except KeyError:
            raise NotFound(version_id) from None
        for a in self.s.artifacts.values():
            if a.artifact_type is row.artifact_type and a.artifact_key == row.artifact_key:
                a.is_active = a.version_id == version_id
        row.approved_by = approved_by
        return replace(row)

    def active_schema(self, schema_id):
        return next(
            (replace(a) for a in self.s.artifacts.values()
             if a.artifact_type is ArtifactType.SCHEMA and a.artifact_key == schema_id and a.is_active),
            None,
        )

    def active_schemas(self):
        return [replace(a) for a in self.s.artifacts.values() if a.artifact_type is ArtifactType.SCHEMA and a.is_active]


class MemoryRawZone:
    def __init__(self, state: _State, fault: RawFault | None):
        self.s = state
        self.fault = fault

    def ensure_tables(self, schema_id, columns):
        existing = self.s.columns.get(schema_id, [])
        self.s.columns[schema_id] = existing + [c for c in columns if c not in existing]

    def clear_stage(self, file_id, target):
        self.s.stage[target] = [r for r in self.s.stage.get(target, []) if r.file_id != file_id]

    def stage_batch(self, target, rows):
        if target not in self.s.columns:
            raise RuntimeError(f"stage.{target} does not exist")
        if self.fault and self.fault.infra_on_stage:
            raise RuntimeError("simulated connection failure while staging")
        if self.fault and any(r.row_identity in self.fault.bad_identities for r in rows):
            raise RowDataError("simulated SQLSTATE 22001: string data, right truncation")
        self.s.stage.setdefault(target, []).extend(rows)

    def promote(self, file_id, target):
        if self.fault and self.fault.infra_on_promote:
            raise RuntimeError("simulated connection failure while promoting")
        landed = self.s.raw.setdefault(target, {})
        inserted = 0
        for row in self.s.stage.get(target, []):
            if row.file_id != file_id:
                continue
            key = (file_id, row.row_identity)
            if key not in landed:
                landed[key] = row
                inserted += 1
        self.clear_stage(file_id, target)
        return inserted

    def count_landed(self, file_id, target):
        return sum(1 for (fid, _) in self.s.raw.get(target, {}) if fid == file_id)


class MemoryAudit:
    def __init__(self, state: _State):
        self.s = state

    def quarantine(self, file_id, reason_code, detail, signature_id):
        self.s.quarantine.append(
            {"file_id": file_id, "reason_code": reason_code, "detail": detail, "signature_id": signature_id}
        )

    def decision(self, file_id, action_type, observed, proposed, applied, approval):
        self.s.decisions.append({
            "file_id": file_id, "action_type": action_type, "observed": observed,
            "proposed": proposed, "applied": applied, "approval": approval,
        })


class MemoryTx:
    def __init__(self, state: _State, fault: RawFault | None):
        self.ledger = MemoryLedger(state)
        self.registry = MemoryRegistry(state)
        self.artifacts = MemoryArtifacts(state)
        self.rejects = state.rejects
        self.raw = MemoryRawZone(state, fault)
        self.audit = MemoryAudit(state)


class MemoryStore:
    def __init__(self, fault: RawFault | None = None):
        self._state = _State()
        self.fault = fault

    @property
    def state(self) -> _State:
        return self._state

    @contextmanager
    def transaction(self) -> Iterator[MemoryTx]:
        work = copy.deepcopy(self._state)
        yield MemoryTx(work, self.fault)
        self._state = work
