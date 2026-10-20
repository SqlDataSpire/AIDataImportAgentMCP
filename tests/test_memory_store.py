from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from importer.memory_store import MemoryStore
from importer.models import ArtifactType, Declared, FileStatus, Reject, Signature, SignatureStatus
from importer.repositories import ConcurrentTransition, IllegalTransition, StagedRow

DECLARED = Declared.normalize("DC", "PC", "CSV")


def _create(tx, key="k1"):
    return tx.ledger.create_or_get(
        idempotency_key=key, source="test", original_filename="f.csv",
        source_path="/tmp/f.csv", declared=DECLARED, request_context={"run_id": "1"},
    )


def _sig(fp="DC|PC|CSV|h:1", part="h:1", ns=None):
    return Signature(fp, part, ns, {"columns": ["a"]})


def test_create_or_get_is_idempotent():
    store = MemoryStore()
    with store.transaction() as tx:
        first, created = _create(tx)
        again, created_again = _create(tx)
    assert created is True and created_again is False
    assert first.file_id == again.file_id
    assert first.status is FileStatus.DISCOVERED
    assert first.declared == DECLARED


def test_transition_guards():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        with pytest.raises(IllegalTransition):
            tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.COMPLETED)
        with pytest.raises(ConcurrentTransition):
            tx.ledger.transition(row.file_id, FileStatus.DETECTED, FileStatus.LANDED)
        tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.DETECTED)
        assert tx.ledger.get(row.file_id).status is FileStatus.DETECTED


def test_exception_rolls_back_the_transaction():
    store = MemoryStore()
    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            _create(tx)
            raise RuntimeError("boom")
    with store.transaction() as tx:
        _, created = _create(tx)
    assert created is True


def test_percent_loaded_floor():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=8, rows_rejected=2, percent_parsed=100.0, percent_loaded=80.0)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=5, rows_rejected=5, percent_parsed=100.0, percent_loaded=50.0)
        assert tx.ledger.get(row.file_id).percent_loaded == 80.0


def test_begin_reentry_increments_attempts():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.FAILED)
        tx.ledger.set_error(row.file_id, "boom")
        again = tx.ledger.begin_reentry(row.file_id)
    assert again.status is FileStatus.DISCOVERED
    assert again.attempts == 2
    assert again.last_error is None


def test_begin_reentry_requires_quarantined_or_failed():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        with pytest.raises(IllegalTransition):
            tx.ledger.begin_reentry(row.file_id)


def test_registry_register_lookup_and_namespace():
    store = MemoryStore()
    with store.transaction() as tx:
        art = tx.artifacts.put_version(ArtifactType.MAPPER, "m", {"kind": "tabular"}, created_by="t")
        sid = tx.registry.register(_sig(ns="urn:a"), art.version_id, "tester")
        hit = tx.registry.lookup("DC|PC|CSV|h:1", "urn:a")
        assert hit is not None and hit.signature_id == sid and hit.mapper_version_id == art.version_id
        assert tx.registry.lookup("DC|PC|CSV|h:1", "urn:b") is None
        assert [r.signature_id for r in tx.registry.find_active_by_physical_part("h:1", "urn:a")] == [sid]


def test_propose_never_overrides_an_active_signature():
    store = MemoryStore()
    with store.transaction() as tx:
        art = tx.artifacts.put_version(ArtifactType.MAPPER, "m", {"kind": "tabular"}, created_by="t")
        sid = tx.registry.register(_sig(), art.version_id, "tester")
        assert tx.registry.propose(_sig(), [{"kind": "schema"}]) == sid
        assert tx.registry.get(sid).status is SignatureStatus.ACTIVE
        new_id = tx.registry.propose(_sig("ME|PC|CSV|h:1"), [{"kind": "schema"}])
        assert tx.registry.get(new_id).status is SignatureStatus.PROPOSED
        assert tx.registry.lookup("ME|PC|CSV|h:1", None) is None


def test_artifact_versions_increment_and_single_active():
    store = MemoryStore()
    with store.transaction() as tx:
        v1 = tx.artifacts.put_version(ArtifactType.SCHEMA, "s", {"columns": ["a"]}, created_by="t")
        v2 = tx.artifacts.put_version(ArtifactType.SCHEMA, "s", {"columns": ["a", "b"]}, created_by="t")
        assert (v1.version_no, v2.version_no) == (1, 2)
        assert tx.artifacts.active_schema("s").version_id == v2.version_id
        assert not tx.artifacts.get(v1.version_id).is_active
        assert [a.artifact_key for a in tx.artifacts.active_schemas()] == ["s"]


def test_reject_count_open_for_file():
    store = MemoryStore()
    with store.transaction() as tx:
        tx.rejects.add(Reject(file_id=1, row_identity="L2", raw_payload="x", reason_code="r", schema_id="s"))
        tx.rejects.add(Reject(file_id=1, row_identity="L3", raw_payload="x", reason_code="r", schema_id="s"))
        tx.rejects.add(Reject(file_id=2, row_identity="L2", raw_payload="x", reason_code="r", schema_id="s"))
        tx.rejects.mark_reclaimed(1, "L3")
        assert tx.rejects.count_open_for_file(1) == 1


@given(st.lists(st.text(alphabet="abc123", min_size=1, max_size=4), unique=True, max_size=25))
def test_promote_is_idempotent(identities):
    store = MemoryStore()
    rows = [StagedRow(1, rid, 1, 1, {"a": rid}, None) for rid in identities]
    with store.transaction() as tx:
        tx.raw.ensure_tables("t", ["a"])
        tx.raw.clear_stage(1, "t")
        tx.raw.stage_batch("t", rows)
        first = tx.raw.promote(1, "t")
        tx.raw.stage_batch("t", rows)
        second = tx.raw.promote(1, "t")
        assert first == len(identities)
        assert second == 0
        assert tx.raw.count_landed(1, "t") == len(identities)
