from __future__ import annotations

from importer.memory_store import MemoryStore
from importer.models import Declared, FileStatus, Signature, SignatureStatus
from importer.pipeline import process
from importer.proposals import new_review, normalize, with_candidates
from tests.support import fixture

SIG = Signature("VT|PC|CSV|h:abc", "h:abc", None, {"columns": ["a"]})


def test_normalize_accepts_none_list_and_object():
    assert normalize(None) == {"candidates": [], "review": new_review()}
    assert normalize([{"kind": "schema"}])["candidates"] == [{"kind": "schema"}]
    partial = normalize({"candidates": [], "review": {"state": "rejected"}})
    assert partial["review"]["state"] == "rejected" and partial["review"]["notes"] == []
    kept = with_candidates({"candidates": [1], "review": {"state": "ready_for_review"}}, [2])
    assert kept == {"candidates": [2], "review": {**new_review(), "state": "ready_for_review"}}


def test_repeated_miss_keeps_the_review_state():
    store = MemoryStore()
    with store.transaction() as tx:
        sid = tx.registry.propose(SIG, [{"kind": "schema", "score": 0.5}])
        proposal = normalize(tx.registry.get(sid).proposal)
        proposal["review"]["state"] = "ready_for_review"
        tx.registry.set_proposal(sid, proposal)
        assert tx.registry.propose(SIG, [{"kind": "schema", "score": 0.9}]) == sid
        row = tx.registry.get(sid)
    assert row.proposal["review"]["state"] == "ready_for_review"
    assert row.proposal["candidates"] == [{"kind": "schema", "score": 0.9}]


def test_list_by_status_activate_and_files_for_signature():
    store = MemoryStore()
    result = process(fixture("url_template_b.csv"), project="VT", cls="PC", file_type="CSV", source="t", store=store)
    with store.transaction() as tx:
        [proposed] = tx.registry.list_by_status(SignatureStatus.PROPOSED)
        assert proposed.proposal["review"]["state"] == "needs_mapping"
        files = tx.ledger.files_for_signature(proposed.signature_id)
        assert [f.file_id for f in files] == [result.file_id] and files[0].status is FileStatus.QUARANTINED
        tx.registry.activate(proposed.signature_id, 99, approved_by="tony")
        row = tx.registry.get(proposed.signature_id)
        assert (row.status, row.mapper_version_id, row.approved_by) == (SignatureStatus.ACTIVE, 99, "tony")
        assert tx.registry.list_by_status(SignatureStatus.PROPOSED) == []


def test_registry_rows_are_copies():
    store = MemoryStore()
    with store.transaction() as tx:
        sid = tx.registry.propose(SIG, [])
        tx.registry.get(sid).proposal["review"]["state"] = "accepted"
        assert tx.registry.get(sid).proposal["review"]["state"] == "needs_mapping"
