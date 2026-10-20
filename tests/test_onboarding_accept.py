from __future__ import annotations

import pytest

from importer import onboarding
from importer.admin import register_schema
from importer.memory_store import MemoryStore
from importer.models import ArtifactType, Declared, FileStatus, SignatureStatus
from importer.pipeline import process
from tests.support import URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, URL_TARGET, configure, fixture, setup_signature

RATIONALE = [{"column": "url", "target": "newurl", "how": "model", "evidence": "values are URLs", "confidence": 0.9}]


def _url_store() -> MemoryStore:
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="t")
    return store


def _park(store, path, project="VT"):
    return process(path, project=project, cls="PC", file_type="CSV", source="t", store=store)


def _b_copy(tmp_path, name, extra_line):
    path = tmp_path / name
    path.write_text(fixture("url_template_b.csv").read_text() + extra_line, encoding="utf-8")
    return path


def test_submit_then_accept_lands_every_waiting_file(tmp_path):
    store = _url_store()
    first = _park(store, fixture("url_template_b.csv"))
    second = _park(store, _b_copy(tmp_path, "b2.csv", "B9,https://example.net/x,SBC\n"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert onboarding.pending(store)[0]["waiting_files"] == 2
    draft = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    assert "| url | newurl | model | values are URLs | 0.9 |" in draft["review_table"]
    assert "Bind" in onboarding.bind_summary(store, sid, draft["draft_version_id"])
    result = onboarding.accept(store, sid, draft["draft_version_id"], by="tony")
    assert [f["status"] for f in result["files"]] == ["completed", "completed"]
    assert [f["file_id"] for f in result["files"]] == [first.file_id, second.file_id]
    assert result["reused_existing"] is False and result["mapper_version_id"] == draft["draft_version_id"]
    assert len(store.state.raw[URL_TARGET]) == 5
    sig = store.state.registry[sid]
    assert (sig.status, sig.approved_by, sig.proposal["review"]["state"]) == (SignatureStatus.ACTIVE, "tony", "accepted")
    mapper = store.state.artifacts[draft["draft_version_id"]]
    assert mapper.is_active and mapper.approved_by == "tony" and mapper.artifact_key == f"sig.{sid}"
    actions = [d["action_type"] for d in store.state.decisions]
    assert actions.index("draft_mapping") < actions.index("bind_signature")


def test_accept_requires_the_latest_draft():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    with pytest.raises(onboarding.OnboardingError, match="no draft ready"):
        onboarding.accept(store, sid, 1, by="tony")
    old = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")["draft_version_id"]
    new = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony", note="same again")["draft_version_id"]
    with pytest.raises(onboarding.OnboardingError, match="not the latest"):
        onboarding.accept(store, sid, old, by="tony")
    review = store.state.registry[sid].proposal["review"]
    assert review["notes"][0]["text"] == "same again" and review["draft_version_id"] == new


def test_invalid_submissions_store_nothing():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert "errors" in onboarding.submit(store, sid, {"kind": "plugin", "ref": "x:Y"}, [], by="tony")
    with pytest.raises(onboarding.OnboardingError, match="path"):
        onboarding.submit(store, sid, URL_SPEC_B, [], by="tony", path="guess")
    assert not [a for a in store.state.artifacts.values() if a.artifact_type is ArtifactType.MAPPER]
    assert store.state.registry[sid].proposal["review"]["state"] == "needs_mapping"


def test_reject_and_reopen():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert onboarding.reject(store, sid, "not a URL template", by="tony") == {"signature_id": sid, "state": "rejected"}
    assert onboarding.pending(store)[0]["state"] == "rejected"
    onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    assert onboarding.pending(store)[0]["state"] == "ready_for_review"
    assert "reject_mapping" in [d["action_type"] for d in store.state.decisions]


def test_identical_spec_binds_to_the_existing_mapper():
    store = MemoryStore()
    mapper_id = setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"),
                                URL_SCHEMA, URL_SPEC_A, "urls_a")
    _park(store, fixture("url_template_a.csv"), project="ME")
    item = onboarding.pending(store)[0]
    suggestion = onboarding.file_profile(store, item["signature_id"])["suggested_draft"]
    assert suggestion["path"] == "reuse"
    draft = onboarding.submit(store, item["signature_id"], suggestion["spec"], [], by="tony", path="reuse")
    result = onboarding.accept(store, item["signature_id"], draft["draft_version_id"], by="tony")
    assert result["mapper_version_id"] == mapper_id and result["reused_existing"] is True
    assert store.state.artifacts[draft["draft_version_id"]].is_active is False
    assert result["files"][0]["status"] == "completed"


def test_repeated_miss_keeps_the_draft(tmp_path):
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    _park(store, _b_copy(tmp_path, "b3.csv", "B7,https://example.net/y,SBC\n"))
    [item] = onboarding.pending(store)
    assert item["state"] == "ready_for_review" and item["waiting_files"] == 2


def test_a_retry_failure_is_reported_and_does_not_undo_the_bind():
    store = _url_store()
    parked = _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    draft = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")

    def broken(file_id, *, store):
        raise RuntimeError("share offline")

    result = onboarding.accept(store, sid, draft["draft_version_id"], by="tony", retry_fn=broken)
    assert result["files"] == [{"file_id": parked.file_id, "error": "RuntimeError: share offline"}]
    assert store.state.registry[sid].status is SignatureStatus.ACTIVE
    assert store.state.ledger[parked.file_id].status is FileStatus.QUARANTINED


def test_a_file_quarantined_during_accept_is_retried_too(tmp_path):
    from importer.pipeline import retry

    store = _url_store()
    first = _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    draft = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    late = {}

    def retry_while_another_file_arrives(file_id, *, store):
        if not late:  # the unattended VM quarantined a file before it saw the bind commit
            store.state.registry[sid].status = SignatureStatus.PROPOSED
            late["file"] = _park(store, _b_copy(tmp_path, "late.csv", "B8,https://example.net/z,SBC\n"))
            store.state.registry[sid].status = SignatureStatus.ACTIVE
        return retry(file_id, store=store)

    result = onboarding.accept(store, sid, draft["draft_version_id"], by="tony", retry_fn=retry_while_another_file_arrives)
    assert late["file"].status is FileStatus.QUARANTINED
    assert [f["file_id"] for f in result["files"]] == [first.file_id, late["file"].file_id]
    assert [f["status"] for f in result["files"]] == ["completed", "completed"]


def test_files_left_quarantined_under_an_active_signature_are_listed_as_stranded():
    store = _url_store()
    parked = _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    draft = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")

    def broken(file_id, *, store):
        raise RuntimeError("share offline")

    onboarding.accept(store, sid, draft["draft_version_id"], by="tony", retry_fn=broken)
    assert onboarding.pending(store) == []
    [item] = onboarding.stranded(store)
    assert (item["file_id"], item["signature_id"]) == (parked.file_id, sid)
    assert item["retry"] == f"importer retry {parked.file_id}"
