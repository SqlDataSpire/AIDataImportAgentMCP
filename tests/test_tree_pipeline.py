from __future__ import annotations

from importer.memory_store import MemoryStore
from importer.models import Declared, FileStatus
from importer.pipeline import process
from tests.support import fixture, load_json, setup_signature

ROOT = "/prescriptionDrugTemplateVO"


def _bound_store() -> tuple[MemoryStore, int]:
    store = MemoryStore()
    setup_signature(store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
                    load_json("formulary_schemas.json"), load_json("formulary_tree_spec.json"), "formulary_tree")
    [sid] = list(store.state.registry)
    return store, sid


def _copy(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _drift_rows(store):
    return [d for d in store.state.decisions if d["action_type"] == "tree_drift"]


def test_a_miss_stores_the_tree_with_the_proposed_signature(tmp_path):
    store = MemoryStore()
    process(_copy(tmp_path, "odd.xml", b"<root><item><code>A</code></item></root>"),
            project="VT", cls="PC", file_type="XML", source="t", store=store)
    [row] = store.state.registry.values()
    assert row.observed_schema["tree"]["tree"]["children"][0]["path"] == "/root/item"


def test_a_hit_with_the_same_structure_writes_no_drift(tmp_path):
    store, _ = _bound_store()
    data = fixture("formulary_dc.xml").read_bytes() + b"\n<!-- same -->\n"
    result = process(_copy(tmp_path, "f.xml", data), project="DC", cls="PC", file_type="XML", source="t", store=store)
    assert result.status is FileStatus.COMPLETED and _drift_rows(store) == []


def test_a_hit_with_a_new_branch_reports_drift_and_still_lands(tmp_path):
    store, _ = _bound_store()
    data = fixture("formulary_dc.xml").read_bytes().replace(
        b"</prescriptionDrugTemplateVO>", b"<extraBranch><x>1</x></extraBranch></prescriptionDrugTemplateVO>")
    result = process(_copy(tmp_path, "f.xml", data), project="DC", cls="PC", file_type="XML", source="t", store=store)
    assert result.status is FileStatus.COMPLETED and result.rows_landed == 10
    [row] = _drift_rows(store)
    assert row["observed"]["new"] == [f"{ROOT}/extraBranch", f"{ROOT}/extraBranch/x"]
    assert row["file_id"] == result.file_id and row["applied"] is False


def test_a_signature_without_a_tree_is_backfilled_on_its_next_hit(tmp_path):
    store, sid = _bound_store()
    store.state.registry[sid].observed_schema.pop("tree")
    data = fixture("formulary_dc.xml").read_bytes() + b"\n<!-- backfill -->\n"
    result = process(_copy(tmp_path, "f.xml", data), project="DC", cls="PC", file_type="XML", source="t", store=store)
    assert result.status is FileStatus.COMPLETED
    assert store.state.registry[sid].observed_schema["tree"]["root"] == "prescriptionDrugTemplateVO"
    assert _drift_rows(store) == []
