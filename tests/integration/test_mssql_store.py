from __future__ import annotations

import uuid

import pytest

from importer.loader import promote, stage
from importer.models import ArtifactType, Declared, FileStatus, Reject, Signature, SignatureStatus
from importer.repositories import ConcurrentTransition, StagedRow

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _create(tx, key):
    return tx.ledger.create_or_get(
        idempotency_key=key, source="it", original_filename="f.csv", source_path="C:/tmp/f.csv",
        declared=Declared.normalize("IT", "PC", "CSV"), request_context={"run_id": "1"},
    )


def test_ledger_roundtrip_floor_and_reentry(mssql_store):
    key = f"it-{_uid()}"
    with mssql_store.transaction() as tx:
        row, created = _create(tx, key)
        assert created and row.status is FileStatus.DISCOVERED and row.request_context == {"run_id": "1"}
        again, created_again = _create(tx, key)
        assert not created_again and again.file_id == row.file_id
        with pytest.raises(ConcurrentTransition):
            tx.ledger.transition(row.file_id, FileStatus.DETECTED, FileStatus.LANDED)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=8, rows_rejected=2, percent_parsed=100.0, percent_loaded=80.0)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=5, rows_rejected=5, percent_parsed=100.0, percent_loaded=50.0)
        assert tx.ledger.get(row.file_id).percent_loaded == 80.0
        tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.FAILED)
        tx.ledger.set_error(row.file_id, "boom")
        back = tx.ledger.begin_reentry(row.file_id)
        assert back.status is FileStatus.DISCOVERED and back.attempts == 2 and back.last_error is None


def test_transaction_rolls_back(mssql_store):
    key = f"it-{_uid()}"
    with pytest.raises(RuntimeError):
        with mssql_store.transaction() as tx:
            _create(tx, key)
            raise RuntimeError("boom")
    with mssql_store.transaction() as tx:
        _, created = _create(tx, key)
    assert created


def test_registry_artifacts_and_audit(mssql_store):
    uid = _uid()
    with mssql_store.transaction() as tx:
        v1 = tx.artifacts.put_version(ArtifactType.SCHEMA, f"s_{uid}", {"columns": ["a"]}, created_by="it")
        v2 = tx.artifacts.put_version(ArtifactType.SCHEMA, f"s_{uid}", {"columns": ["a", "b"]}, created_by="it")
        assert (v1.version_no, v2.version_no) == (1, 2)
        assert tx.artifacts.active_schema(f"s_{uid}").definition == {"columns": ["a", "b"]}
        draft = tx.artifacts.put_version(ArtifactType.MAPPER, f"sig.{uid}", {"kind": "tabular"}, created_by="it", activate=False)
        assert tx.artifacts.active(ArtifactType.MAPPER, f"sig.{uid}") is None
        assert tx.artifacts.activate(draft.version_id, approved_by="it").approved_by == "it"
        assert tx.artifacts.active(ArtifactType.MAPPER, f"sig.{uid}").version_id == draft.version_id
        mapper = tx.artifacts.put_version(ArtifactType.MAPPER, f"m_{uid}", {"kind": "tabular"}, created_by="it")
        sig = Signature(f"IT|PC|CSV|h:{uid}", f"h:{uid}", None, {"columns": ["a"]})
        proposed = tx.registry.propose(sig, [{"kind": "schema"}])
        assert tx.registry.get(proposed).status is SignatureStatus.PROPOSED
        assert tx.registry.get(proposed).proposal["review"]["state"] == "needs_mapping"
        tx.registry.set_proposal(proposed, {"candidates": [], "review": {"state": "ready_for_review"}})
        assert tx.registry.propose(sig, [{"kind": "x"}]) == proposed
        assert tx.registry.get(proposed).proposal["review"]["state"] == "ready_for_review"
        assert proposed in [r.signature_id for r in tx.registry.list_by_status(SignatureStatus.PROPOSED)]
        assert tx.registry.lookup(sig.fingerprint, None) is None
        active = tx.registry.register(sig, mapper.version_id, "it")
        assert active == proposed
        assert tx.registry.lookup(sig.fingerprint, None).mapper_version_id == mapper.version_id
        row, _ = _create(tx, f"it-{uid}")
        tx.ledger.set_signature(row.file_id, active)
        assert [f.file_id for f in tx.ledger.files_for_signature(active)] == [row.file_id]
        tx.registry.activate(active, mapper.version_id, approved_by="it")
        assert tx.registry.get(active).approved_by == "it"
        tx.audit.quarantine(row.file_id, "unroutable", "looks like x", active)
        tx.audit.decision(row.file_id, "profile_match", {"kind": "tabular"}, {"candidates": []}, False, None)


def test_rejects_are_idempotent(mssql_store):
    with mssql_store.transaction() as tx:
        row, _ = _create(tx, f"it-{_uid()}")
        reject = Reject(file_id=row.file_id, row_identity="L3", raw_payload="x", reason_code="field_count_mismatch", schema_id="s")
        assert tx.rejects.add(reject) is True
        assert tx.rejects.add(reject) is False
        assert tx.rejects.count_open_for_file(row.file_id) == 1
        assert tx.rejects.mark_reclaimed(row.file_id, "L3") is True
        assert tx.rejects.mark_reclaimed(row.file_id, "L3") is False
        assert tx.rejects.count_open_for_file(row.file_id) == 0


def test_raw_zone_stage_promote_and_bisection(mssql_store):
    target = f"import_agent_dev.it.t_{_uid()}"
    with mssql_store.transaction() as tx:
        row, _ = _create(tx, f"it-{_uid()}")
        tx.raw.ensure_tables(target, ["a", "b"])
        rows = [StagedRow(row.file_id, f"L{i}", 1, 1, {"a": str(i), "b": "x"}, None) for i in range(2, 22)]
        rows[5] = StagedRow(row.file_id, "L" + "9" * 300, 1, 1, {"a": "too long id"}, None)  # forces SQLSTATE 22001
        rejects = stage(tx.raw, row.file_id, {target: rows}, batch_size=8)
        assert [r.reason_code for r in rejects] == ["db_insert_error"]
        assert promote(tx.raw, row.file_id, [target]) == 19
        tx.raw.stage_batch(target, rows[:3])
        assert promote(tx.raw, row.file_id, [target]) == 0
        assert tx.raw.count_landed(row.file_id, target) == 19
        tx.raw.ensure_tables(target, ["a", "b", "c"])  # schema version adds a column
    with mssql_store.transaction() as tx:
        assert tx.raw.count_landed(row.file_id, target) == 19
