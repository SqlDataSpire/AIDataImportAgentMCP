from __future__ import annotations

import pytest

from importer import onboarding, settings
from importer.admin import register_schema
from importer.memory_store import MemoryStore
from importer.models import Declared
from importer.pipeline import process
from tests.support import FORMULARY_TARGETS, URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, URL_TARGET, configure, fixture, load_json, setup_signature


def _url_store() -> MemoryStore:
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="t")
    return store


def _park(store, path, project="VT", file_type="CSV"):
    return process(path, project=project, cls="PC", file_type=file_type, source="t", store=store)


def test_pending_lists_a_fallback_proposal():
    store = _url_store()
    parked = _park(store, fixture("url_template_b.csv"))
    [item] = onboarding.pending(store)
    assert item["state"] == "needs_mapping" and item["suggested_path"] == "fallback"
    assert item["candidates"][0].startswith(f"looks like {URL_TARGET}")
    assert item["waiting_files"] == 1 and item["trigger_file"]["file_id"] == parked.file_id


def test_exact_path_drafts_an_auto_spec():
    store = _url_store()
    _park(store, fixture("url_template_a.csv"))
    [item] = onboarding.pending(store)
    assert item["suggested_path"] == "exact"
    draft = onboarding.file_profile(store, item["signature_id"])["suggested_draft"]
    assert draft == {"path": "exact", "spec": {"kind": "tabular", "extractions": [
        {"target": URL_TARGET, "records": "*", "auto": True, "unmapped": "extras"}]}}


def test_reuse_path_and_xml_profile(tmp_path):
    store = MemoryStore()
    mapper_id = setup_signature(store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
                                load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary")
    me = tmp_path / "me.xml"
    me.write_bytes(fixture("formulary_dc.xml").read_bytes() + b"\n<!-- ME -->\n")
    _park(store, me, project="ME", file_type="XML")
    [item] = onboarding.pending(store)
    assert item["suggested_path"] == "reuse"
    prof = onboarding.file_profile(store, item["signature_id"])
    assert prof["suggested_draft"]["mapper_version_id"] == mapper_id
    assert prof["profile"]["kind"] == "xml" and prof["profile"]["root"] == "prescriptionDrugTemplateVO"


def test_unknown_xml_needs_the_model(tmp_path):
    store = MemoryStore()
    path = tmp_path / "odd.xml"
    path.write_bytes(b"<root><item><code>A</code></item><item><code>B</code></item></root>")
    _park(store, path, file_type="XML")
    [item] = onboarding.pending(store)
    assert item["suggested_path"] == "model"
    assert onboarding.file_profile(store, item["signature_id"])["suggested_draft"] is None
    assert onboarding.samples(store, item["signature_id"], ["/root/item/code"]) == {"/root/item/code": ["a"]}  # masked shapes, deduplicated


def test_samples_are_capped_and_truncated():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert len(onboarding.samples(store, sid, ["url"])["url"]) == 2
    with store.transaction() as tx:
        settings.set_value(tx, "samples.max_values", 1, by="t")
        settings.set_value(tx, "samples.max_chars", 10, by="t")
    assert onboarding.samples(store, sid, ["url"]) == {"url": ["aaaaa://aa"]}


def test_search_get_schema_and_prior_mappings():
    store = _url_store()
    setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    best = onboarding.search_schemas(store, columns=["URL ID", "url", "url type"])[0]
    assert (best["address"], best["score"]) == (URL_TARGET, 0.67)
    assert onboarding.search_schemas(store, text="url")[0]["address"] == URL_TARGET
    assert onboarding.get_schema(store, "APP_DB.relational.url_template")["columns"] == URL_SCHEMA[URL_TARGET]
    with pytest.raises(onboarding.OnboardingError):
        onboarding.get_schema(store, "app_db.relational.nope")
    [prior] = onboarding.prior_mappings(store, URL_TARGET)
    assert prior["spec"] == URL_SPEC_A and prior["fingerprint"].startswith("VT|PC|CSV|h:")


def test_preview_spec_counts_rows_and_reports_errors():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    result = onboarding.preview_spec(store, sid, URL_SPEC_B)
    assert result["counts"] == {URL_TARGET: 2} and result["rejects"] == {}
    assert len(result["sample"][URL_TARGET]) == 2 and result["sample"][URL_TARGET][0]["newurl"].startswith("aaaaa://")
    assert "plugin" in onboarding.preview_spec(store, sid, {"kind": "plugin", "ref": "x:Y"})["errors"][0]
    bad = {"kind": "tabular", "extractions": [{"target": "app_db.relational.nope", "records": "*", "auto": True}]}
    assert "no active schema" in onboarding.preview_spec(store, sid, bad)["errors"][0]


REAL_VALUES = ("54321VT0020001", "54321VT0020002", "example.net", "SBC", "Payment")


def test_no_real_value_is_returned_or_stored(tmp_path):
    import json

    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    everything_unmapped = {"kind": "tabular", "extractions": [{"target": URL_TARGET, "records": "*", "fields": {"newurl": "url"}}]}
    outputs = [onboarding.preview_spec(store, sid, everything_unmapped), onboarding.preview_spec(store, sid, URL_SPEC_B),
               onboarding.samples(store, sid, ["urlid", "url", "urltype"]), onboarding.file_profile(store, sid)]
    outputs.append(onboarding.submit(store, sid, URL_SPEC_B, [], by="t"))
    outputs.append(store.state.registry[sid].proposal)
    text = json.dumps(outputs, default=str)
    assert not [v for v in REAL_VALUES if v in text]
    sample = outputs[0]["sample"][URL_TARGET][0]
    assert "row_identity" not in sample and sample["_extras"]["urlid"] == "99999aa9999999"


FORMULARY_SCHEMAS = "formulary_schemas.json"


def _formulary_waiting(tmp_path):
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        for address, columns in load_json(FORMULARY_SCHEMAS).items():
            register_schema(tx, address, columns, created_by="t")
    path = tmp_path / "f.xml"
    path.write_bytes(fixture("formulary_dc.xml").read_bytes())
    _park(store, path, project="ME", file_type="XML")
    [item] = onboarding.pending(store)
    return store, item["signature_id"]


def test_xml_profile_shows_a_depth_limited_tree_and_record_candidates(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    prof = onboarding.file_profile(store, sid)
    top = prof["profile"]["tree"]["tree"]
    assert top["path"] == "/prescriptionDrugTemplateVO"
    formulary_vo = top["children"][1]["children"][0]  # depth 3: root > formularyList > formularyVO
    assert formulary_vo["children"][3]["name"] == "formularyCostSharingTypeList"
    assert formulary_vo["children"][3]["children_omitted"] == 1  # depth 4 is the last level shown
    assert any(c["kind"] == "record" for c in prof["candidates"])


def test_tree_pages_a_branch(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    branch = onboarding.tree(store, sid, path="/prescriptionDrugTemplateVO/drugList", depth=2)
    assert branch["tree"]["children"][0]["name"] == "drugListVO"
    with pytest.raises(onboarding.OnboardingError, match="no element path"):
        onboarding.tree(store, sid, path="/nope")


def test_tree_preview_counts_every_target_and_warns(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    spec = load_json("formulary_tree_spec.json")
    spec["select"][1]["select"][1]["forEach"] = "x:drugVOList/x:drug"
    result = onboarding.preview_spec(store, sid, spec)
    assert result["counts"]["app_db.xml.formulary_drug_list"] == 0
    assert result["counts"]["app_db.xml.formulary_benefit_definition"] == 5
    assert any("x:drugVOList/x:drug" in w for w in result["tree_warnings"])
    good = onboarding.preview_spec(store, sid, load_json("formulary_tree_spec.json"))
    assert good["tree_warnings"] == [] and sum(good["counts"].values()) == 10


def test_tree_submit_shows_the_outline_and_prior_mappings_find_tree_targets(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    draft = onboarding.submit(store, sid, load_json("formulary_tree_spec.json"), [], by="t")
    assert "app_db.xml.formulary_drug_list <- x:drugList/x:drugListVO / x:drugVOList/x:drugVO" in draft["review_table"]
    onboarding.accept(store, sid, draft["draft_version_id"], by="t")
    assert onboarding.prior_mappings(store, FORMULARY_TARGETS[2])[0]["spec"]["kind"] == "tree"
