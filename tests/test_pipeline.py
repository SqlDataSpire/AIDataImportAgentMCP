from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from importer.admin import register_signature
from importer.memory_store import MemoryStore, RawFault
from importer.models import Declared, FileStatus
from importer.pipeline import process, retry
from tests.support import (
    EVENT_SCHEMA, EVENT_SPEC, URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, URL_TARGET, fixture, load_json, setup_signature,
)

REQUEST = {"binder_name": "ABCD-DC25-1", "run_id": "42"}


def _formulary_store() -> tuple[MemoryStore, int]:
    store = MemoryStore()
    mapper_id = setup_signature(
        store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
        load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary",
    )
    return store, mapper_id


def _run(store, path, project="DC", cls="PC", file_type="XML", context=None):
    return process(path, project=project, cls=cls, file_type=file_type, source="test", context=context, store=store)


def _me_copy(tmp_path: Path) -> Path:
    path = tmp_path / "formulary_me.xml"
    path.write_bytes(fixture("formulary_dc.xml").read_bytes() + b"\n<!-- ME delivery -->\n")
    return path


def test_known_formulary_lands_into_three_raw_tables():
    store, _ = _formulary_store()
    result = _run(store, fixture("formulary_dc.xml"), context=REQUEST)
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.rows_landed, result.rows_rejected) == (10, 10, 0)
    assert result.percent_loaded == 100.0 and result.percent_parsed == 100.0
    raw = store.state.raw
    assert {t: len(rows) for t, rows in raw.items()} == {
        "app_db.xml.formulary_benefit_definition": 5, "app_db.xml.formulary_tier_definition": 2, "app_db.xml.formulary_drug_list": 3,
    }
    first = next(iter(raw["app_db.xml.formulary_benefit_definition"].values()))
    assert first.fields["binder_name"] == "ABCD-DC25-1"
    ledger = store.state.ledger[result.file_id]
    assert ledger.signature_id is not None and ledger.encoding == "UTF-8"
    assert result.to_dict()["status"] == "completed"


def test_unknown_project_is_quarantined_with_a_reuse_proposal(tmp_path):
    store, mapper_id = _formulary_store()
    result = _run(store, _me_copy(tmp_path), project="ME")
    assert result.status is FileStatus.QUARANTINED
    assert result.quarantine_reason == "unroutable"
    assert result.proposal_summary == f"looks like DC|PC|XML|prescriptionDrugTemplateVO -> reuse mapper {mapper_id}"
    assert store.state.quarantine[-1]["reason_code"] == "unroutable"
    assert store.state.decisions[-1]["action_type"] == "profile_match"
    assert result.percent_loaded == 0.0


def test_binding_then_retry_completes_the_parked_file(tmp_path):
    store, mapper_id = _formulary_store()
    path = _me_copy(tmp_path)
    parked = _run(store, path, project="ME")
    with store.transaction() as tx:
        register_signature(tx, path.read_bytes(), Declared.normalize("ME", "PC", "XML"), mapper_id, approved_by="test")
    result = retry(parked.file_id, store=store)
    assert result.status is FileStatus.COMPLETED
    assert result.percent_loaded == 100.0
    assert store.state.ledger[parked.file_id].attempts == 2


def test_two_csv_variants_land_in_one_schema():
    store = MemoryStore()
    setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    setup_signature(store, fixture("url_template_b.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_B, "urls_b")
    a = _run(store, fixture("url_template_a.csv"), project="VT", file_type="CSV", context={"run_id": "7"})
    b = _run(store, fixture("url_template_b.csv"), project="VT", file_type="CSV", context={"run_id": "7"})
    assert a.status is b.status is FileStatus.COMPLETED
    rows = store.state.raw[URL_TARGET]
    assert len(rows) == 4
    assert {r.fields["newurl"] for r in rows.values()} == {
        "https://example.org/sbc/1", "https://example.org/pay", "https://example.net/sbc/9", "https://example.net/pay",
    }
    assert store.state.ledger[a.file_id].signature_id != store.state.ledger[b.file_id].signature_id


def test_bad_ndjson_line_completes_with_failures():
    store = MemoryStore()
    setup_signature(store, fixture("events.ndjson"), Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    result = _run(store, fixture("events.ndjson"), cls="PD", file_type="NDJSON")
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.rows_landed, result.rows_rejected) == (4, 3, 1)
    assert result.percent_loaded == 75.0 and result.percent_parsed == 75.0
    [reject] = store.state.rejects.all_rejects()
    assert (reject.reason_code, reject.row_identity, reject.schema_id) == ("invalid_json_line", "L3", "app_db.dbo.event")


def test_byte_identical_redelivery_returns_the_same_file():
    store, _ = _formulary_store()
    first = _run(store, fixture("formulary_dc.xml"))
    again = _run(store, fixture("formulary_dc.xml"))
    assert again.file_id == first.file_id
    assert again.status is FileStatus.COMPLETED
    assert len(store.state.raw["app_db.xml.formulary_drug_list"]) == 3


def test_corrupt_and_mislabelled_files_are_quarantined(tmp_path):
    store = MemoryStore()
    corrupt = tmp_path / "bad.xml"
    corrupt.write_bytes(b"<a><b></a>")
    assert _run(store, corrupt).quarantine_reason == "corrupt"
    mislabelled = tmp_path / "data.csv"
    mislabelled.write_bytes(b"a,b\n1,2\n")
    assert _run(store, mislabelled, file_type="XML").quarantine_reason == "unparseable"


def test_broken_mapper_artifact_fails_the_file():
    store, mapper_id = _formulary_store()
    store.state.artifacts[mapper_id].definition = {"kind": "bogus"}
    result = _run(store, fixture("formulary_dc.xml"))
    assert result.status is FileStatus.FAILED
    assert store.state.ledger[result.file_id].last_error.startswith("unknown_mapper_kind")


def test_stage_infrastructure_failure_then_retry():
    store, _ = _formulary_store()
    store.fault = RawFault(infra_on_stage=True)
    failed = _run(store, fixture("formulary_dc.xml"))
    assert failed.status is FileStatus.FAILED
    assert store.state.ledger[failed.file_id].last_error.startswith("stage_failed")
    assert store.state.raw == {}
    store.fault = None
    result = retry(failed.file_id, store=store)
    assert result.status is FileStatus.COMPLETED and result.rows_landed == 10


def test_promote_failure_rolls_back_and_fails():
    store, _ = _formulary_store()
    store.fault = RawFault(infra_on_promote=True)
    result = _run(store, fixture("formulary_dc.xml"))
    assert result.status is FileStatus.FAILED
    assert store.state.ledger[result.file_id].last_error.startswith("promote_failed")
    assert store.state.rejects.all_rejects() == []


def test_database_data_error_becomes_a_reject():
    store = MemoryStore()
    setup_signature(store, fixture("events.ndjson"), Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    store.fault = RawFault(bad_identities={"L2"})
    result = _run(store, fixture("events.ndjson"), cls="PD", file_type="NDJSON")
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_landed, result.rows_rejected) == (2, 2)
    assert sorted(r.reason_code for r in store.state.rejects.all_rejects()) == ["db_insert_error", "invalid_json_line"]


def test_header_only_csv_completes_with_zero_rows(tmp_path):
    store = MemoryStore()
    setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"URL ID,New URL,URL Type\n")
    result = _run(store, empty, project="VT", file_type="CSV")
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.percent_loaded) == (0, 0.0)


line = st.one_of(
    st.builds(lambda i: '{"id": "%d", "name": "n", "amount": "1"}' % i, st.integers(0, 10**6)),
    st.sampled_from(["oops", "{not json", "[1, 2]"]),
)


@settings(max_examples=40, deadline=None)
@given(st.lists(line, min_size=1, max_size=30))
def test_conservation_landed_plus_rejected_equals_total(lines):
    store = MemoryStore()
    setup_signature(store, fixture("events.ndjson"), Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    body = '{"id": "seed", "name": "n", "amount": "1"}\n' + "\n".join(lines) + "\n"
    fd, name = tempfile.mkstemp(suffix=".ndjson")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode())
        result = _run(store, name, cls="PD", file_type="NDJSON")
    finally:
        os.unlink(name)
    assert result.status is FileStatus.COMPLETED
    assert result.rows_landed + result.rows_rejected == result.rows_total
