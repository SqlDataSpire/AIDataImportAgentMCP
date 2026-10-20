"""Fast-path orchestrator — the state machine over the ledger (design §8.3).

Each step is its own transaction and moves the ledger exactly one status:
discovered → detected → landed → promoted → completed, or → quarantined /
failed. A failed step rolls back its own writes; the file is then marked
failed (control-plane problem) and can re-enter via retry(). The model is
never in this path.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import replace
from pathlib import Path

from . import locators
from .detector import detect
from .loader import DEFAULT_BATCH_SIZE, promote, stage
from .mappers.base import RunContext
from .mappers.loading import PLUGINS_DIR, MapperLoadError, load_mapper
from .metrics import compute_percent
from .models import Declared, FileResult, FileStatus, Reject, RejectRow
from .precheck import PreCheck, storable_identity
from .profiler import match, profile
from .readers import parse_document
from .readers.base import ReaderError
from .repositories import LedgerRow, StagedRow, Store
from .trees import drift

log = logging.getLogger("importer")
READER_REJECT_CODES = frozenset({"field_count_mismatch", "invalid_json_line", "invalid_json_record"})


class _Stop(Exception):
    def __init__(self, result: FileResult):
        super().__init__(result.status.value)
        self.result = result


class _ConservationError(Exception):
    pass


def process(path, *, project: str, cls: str, file_type: str, source: str, context: dict | None = None,
            store: Store, batch_size: int = DEFAULT_BATCH_SIZE, plugins_dir: Path = PLUGINS_DIR) -> FileResult:
    declared = Declared.normalize(project, cls, file_type)
    locator = locators.normalize(path)
    data = locators.read_bytes(locator)
    key = hashlib.sha256(data).hexdigest()
    with store.transaction() as tx:
        row, created = tx.ledger.create_or_get(
            idempotency_key=key, source=source, original_filename=locators.name_of(locator),
            source_path=locator, declared=declared, request_context=context,
        )
    if not created:
        log.info("file_id=%s re-delivery of a known file (status=%s); nothing re-landed", row.file_id, row.status.value)
        return _result_from_ledger(row)
    log.info("file_id=%s discovered %s as %s", row.file_id, locators.name_of(locator), declared.prefix)
    return _Run(store, row.file_id, data, declared, context or {}, batch_size, plugins_dir).execute()


def retry(file_id: int, *, store: Store, batch_size: int = DEFAULT_BATCH_SIZE, plugins_dir: Path = PLUGINS_DIR) -> FileResult:
    with store.transaction() as tx:
        row = tx.ledger.get(file_id)
    data = locators.read_verified(row.source_path, row.idempotency_key)
    with store.transaction() as tx:
        row = tx.ledger.begin_reentry(file_id)
    log.info("file_id=%s re-entry attempt %s", file_id, row.attempts)
    return _Run(store, file_id, data, row.declared, row.request_context or {}, batch_size, plugins_dir).execute()


def _result_from_ledger(row: LedgerRow) -> FileResult:
    return FileResult(
        file_id=row.file_id, status=row.status, rows_total=row.rows_total or 0, rows_landed=row.rows_landed or 0,
        rows_rejected=row.rows_rejected or 0, percent_parsed=row.percent_parsed, percent_loaded=row.percent_loaded,
    )


class _Run:
    def __init__(self, store: Store, file_id: int, data: bytes, declared: Declared, context: dict,
                 batch_size: int, plugins_dir: Path):
        self.store = store
        self.file_id = file_id
        self.data = data
        self.declared = declared
        self.context = context
        self.batch_size = batch_size
        self.plugins_dir = plugins_dir
        self.status = FileStatus.DISCOVERED
        self.started = time.perf_counter()

    # ---- helpers ---------------------------------------------------------------

    def _step(self, dst: FileStatus, work=None):
        """Run ``work(tx)`` and the status change in one transaction."""
        with self.store.transaction() as tx:
            result = work(tx) if work else None
            tx.ledger.transition(self.file_id, self.status, dst)
        log.info("file_id=%s %s -> %s %dms", self.file_id, self.status.value, dst.value,
                 int((time.perf_counter() - self.started) * 1000))
        self.status = dst
        return result

    def _result(self) -> FileResult:
        with self.store.transaction() as tx:
            return _result_from_ledger(tx.ledger.get(self.file_id))

    def _fail(self, reason: str, exc: BaseException | None = None) -> FileResult:
        message = f"{reason}: {type(exc).__name__}: {exc}" if exc else reason
        log.error("file_id=%s failed: %s", self.file_id, message)
        self._step(FileStatus.FAILED, lambda tx: tx.ledger.set_error(self.file_id, message))
        return self._result()

    def _zero_metrics(self, tx) -> None:
        tx.ledger.record_metrics(self.file_id, rows_total=0, rows_landed=0, rows_rejected=0,
                                 percent_parsed=0.0, percent_loaded=0.0)

    def _quarantine(self, reason_code: str, detail: str) -> FileResult:
        def work(tx):
            tx.audit.quarantine(self.file_id, reason_code, detail, None)
            self._zero_metrics(tx)
        self._step(FileStatus.QUARANTINED, work)
        return replace(self._result(), quarantine_reason=reason_code)

    def _check_tree(self, tx, sig, hit) -> None:
        """XML only (§8.24): store the nesting tree the first time a signature is hit without one,
        otherwise record structural drift. Never fails or holds the file."""
        current = sig.observed.get("tree")
        if current is None:
            return
        observed = tx.registry.get(hit.signature_id).observed_schema or {}
        stored = observed.get("tree")
        if stored is None:
            tx.registry.set_observed(hit.signature_id, {**observed, **sig.observed})
            return
        changes = drift(stored, current)
        if changes:
            tx.audit.decision(self.file_id, "tree_drift", observed=changes, proposed=None, applied=False, approval=None)

    # ---- the state machine -------------------------------------------------------

    def execute(self) -> FileResult:
        try:
            doc = self._read()
            with self.store.transaction() as tx:
                sig, hit = detect(doc, self.declared, tx.registry)
            if hit is None:
                return self._unroutable(doc, sig)
            return self._land(doc, hit, sig)
        except _Stop as stop:
            return stop.result

    def _read(self):
        try:
            doc = parse_document(self.data, self.declared.file_type)
        except ReaderError as exc:
            raise _Stop(self._quarantine(exc.reason_code, exc.detail)) from exc
        with self.store.transaction() as tx:
            tx.ledger.set_encoding(self.file_id, doc.encoding)
        return doc

    def _unroutable(self, doc, sig) -> FileResult:
        prof = profile(doc)
        with self.store.transaction() as tx:
            candidates = match(sig, prof, tx.registry, tx.artifacts)
        summary = candidates[0].summary() if candidates else "no similar signature or schema found"
        proposal = [c.to_dict() for c in candidates]

        def work(tx):
            signature_id = tx.registry.propose(sig, proposal)
            tx.ledger.set_signature(self.file_id, signature_id)
            tx.audit.decision(self.file_id, "profile_match", observed=prof,
                              proposed={"signature": sig.fingerprint, "candidates": proposal},
                              applied=False, approval=None)
            tx.audit.quarantine(self.file_id, "unroutable", summary, signature_id)
            self._zero_metrics(tx)

        self._step(FileStatus.QUARANTINED, work)
        return replace(self._result(), quarantine_reason="unroutable", proposal_summary=summary)

    def _land(self, doc, hit, sig) -> FileResult:
        def detected(tx):
            tx.ledger.set_signature(self.file_id, hit.signature_id)
            self._check_tree(tx, sig, hit)

        self._step(FileStatus.DETECTED, detected)
        try:
            with self.store.transaction() as tx:
                artifact = tx.artifacts.get(hit.mapper_version_id)
                schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
            mapper = load_mapper(artifact, known_schemas=set(schemas), plugins_dir=self.plugins_dir)
        except MapperLoadError as exc:
            return self._fail(exc.reason_code, exc)
        targets = mapper.targets
        ctx = RunContext(self.file_id, hit.signature_id, artifact.version_id, self.context,
                         {t: schemas[t] for t in targets})
        try:
            emitted = list(mapper.extract(doc, ctx))
        except Exception as exc:  # spec/doc mismatch or a plugin bug: configuration problem
            return self._fail("mapping_failed", exc)

        precheck = PreCheck()
        staged: dict[str, list[StagedRow]] = {t: [] for t in targets}
        rejects: list[RejectRow] = []
        reader_rejects = 0
        for item in emitted:
            if isinstance(item, RejectRow):
                rejects.append(item)
                reader_rejects += item.reason_code in READER_REJECT_CODES
                continue
            if item.target not in staged:
                return self._fail("mapping_failed", ValueError(f"mapper emitted undeclared target {item.target!r}"))
            checked = precheck.check(item)
            if isinstance(checked, RejectRow):
                rejects.append(checked)
                continue
            staged[item.target].append(StagedRow(self.file_id, item.row_identity, hit.signature_id,
                                                 artifact.version_id, item.fields, checked.extras_json))
        total = len(emitted)

        def stage_work(tx):
            for target in targets:
                tx.raw.ensure_tables(target, schemas[target])
            return stage(tx.raw, self.file_id, staged, self.batch_size)

        try:
            rejects.extend(self._step(FileStatus.LANDED, stage_work))
        except Exception as exc:
            return self._fail("stage_failed", exc)

        reject_rows = self._unique_rejects(rejects)

        def promote_work(tx):
            promote(tx.raw, self.file_id, targets)
            for reject in reject_rows:
                tx.rejects.add(reject)

        try:
            self._step(FileStatus.PROMOTED, promote_work)
        except Exception as exc:
            return self._fail("promote_failed", exc)

        def complete_work(tx):
            landed = sum(tx.raw.count_landed(self.file_id, t) for t in targets)
            rejected = tx.rejects.count_open_for_file(self.file_id)
            if landed + rejected != total:
                raise _ConservationError(f"landed {landed} + rejected {rejected} != total {total}")
            tx.ledger.record_metrics(
                self.file_id, rows_total=total, rows_landed=landed, rows_rejected=rejected,
                percent_parsed=compute_percent(total - reader_rejects, total),
                percent_loaded=compute_percent(landed, total),
            )

        try:
            self._step(FileStatus.COMPLETED, complete_work)
        except _ConservationError as exc:
            return self._fail("conservation_violation", exc)
        except Exception as exc:
            return self._fail("complete_failed", exc)
        return self._result()

    def _unique_rejects(self, rejects: list[RejectRow]) -> list[Reject]:
        out: list[Reject] = []
        used: set[str] = set()
        for reject in rejects:
            identity = storable_identity(reject.row_identity)
            if identity in used:
                n = 2
                while storable_identity(f"{reject.row_identity}#{n}") in used:
                    n += 1
                identity = storable_identity(f"{reject.row_identity}#{n}")
            used.add(identity)
            out.append(Reject(file_id=self.file_id, row_identity=identity, raw_payload=reject.raw_payload,
                              reason_code=reject.reason_code, schema_id=reject.target, reason_detail=reject.detail))
        return out
