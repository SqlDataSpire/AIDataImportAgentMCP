from __future__ import annotations

from importer.admin import register_schema
from importer.fingerprint import fingerprint
from importer.memory_store import MemoryStore
from importer.models import Declared
from importer.profiler import match, profile
from importer.readers import parse_document
from tests.support import URL_SCHEMA, configure, fixture, load_json, setup_signature


def test_xml_profile_is_the_nesting_tree():
    prof = profile(parse_document(fixture("formulary_dc.xml").read_bytes(), "XML"))
    assert prof["kind"] == "xml" and prof["root"] == "prescriptionDrugTemplateVO"
    assert prof["tree"]["tree"]["path"] == "/prescriptionDrugTemplateVO"
    assert "paths" not in prof


def test_tabular_profile_lists_columns():
    prof = profile(parse_document(fixture("url_template_b.csv").read_bytes(), "CSV"))
    assert prof == {"kind": "tabular", "format": "DELIMITED", "tables": {"*": ["urlid", "url", "urltype"]}}


def test_same_template_under_another_project_proposes_mapper_reuse():
    store = MemoryStore()
    mapper_id = setup_signature(
        store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
        load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary",
    )
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    sig = fingerprint(doc, Declared.normalize("ME", "PC", "XML"))
    with store.transaction() as tx:
        candidates = match(sig, profile(doc), tx.registry, tx.artifacts)
    assert candidates[0].kind == "reuse_mapper"
    assert candidates[0].mapper_version_id == mapper_id
    assert candidates[0].summary() == f"looks like DC|PC|XML|prescriptionDrugTemplateVO -> reuse mapper {mapper_id}"


def test_tabular_overlap_names_extra_and_missing_columns():
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        register_schema(tx, "app_db.relational.url_template", URL_SCHEMA["app_db.relational.url_template"], created_by="t")
    doc = parse_document(fixture("url_template_b.csv").read_bytes(), "CSV")
    sig = fingerprint(doc, Declared.normalize("VT", "PC", "CSV"))
    with store.transaction() as tx:
        candidates = match(sig, profile(doc), tx.registry, tx.artifacts)
    best = candidates[0]
    assert best.kind == "schema" and best.schema_id == "app_db.relational.url_template"
    assert best.score == 0.67
    assert best.extra == ["url"]
    assert best.missing == ["fileid", "issuerid", "newurl", "runid"]
    assert best.summary() == "looks like app_db.relational.url_template (0.67; extra: url; missing: fileid, issuerid, newurl, runid)"
    assert best.to_dict()["schema_id"] == "app_db.relational.url_template"


def test_low_overlap_is_not_proposed():
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        register_schema(tx, "app_db.relational.url_template", URL_SCHEMA["app_db.relational.url_template"], created_by="t")
    doc = parse_document(b"alpha,beta,gamma\n1,2,3\n", "CSV")
    sig = fingerprint(doc, Declared.normalize("VT", "PC", "CSV"))
    with store.transaction() as tx:
        assert match(sig, profile(doc), tx.registry, tx.artifacts) == []


def test_xml_record_nodes_are_matched_against_schemas():
    store = MemoryStore()
    schemas = load_json("formulary_schemas.json")
    configure(store, "app_db")
    with store.transaction() as tx:
        for address, columns in schemas.items():
            register_schema(tx, address, columns, created_by="t")
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    sig = fingerprint(doc, Declared.normalize("ME", "PC", "XML"))
    with store.transaction() as tx:
        candidates = match(sig, profile(doc), tx.registry, tx.artifacts)
    records = {c.table: c for c in candidates if c.kind == "record"}
    drug = records["/prescriptionDrugTemplateVO/drugList/drugListVO/drugVOList/drugVO"]
    assert drug.schema_id == "app_db.xml.formulary_drug_list" and drug.score == 0.6
    assert drug.matched == ["authorizationrequired", "rxcui", "steptherapyrequired"]
    assert records["/prescriptionDrugTemplateVO/formularyList/formularyVO"].schema_id == "app_db.xml.formulary_benefit_definition"
    assert drug.summary().startswith("looks like app_db.xml.formulary_drug_list at /prescriptionDrugTemplateVO/drugList")
