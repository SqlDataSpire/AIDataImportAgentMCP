from __future__ import annotations

import pytest

from importer.mappers.spec import DeclarativeMapper, SpecError, parse_spec
from importer.models import LandRow, RejectRow
from importer.readers import parse_document
from tests.support import ctx, fixture, load_json

NS = "http://vo.ffe.cms.hhs.gov"
REQUEST = {"binder_name": "ABCD-DC25-1", "run_id": "42"}


def _formulary_rows():
    mapper = DeclarativeMapper(parse_spec(load_json("formulary_spec.json")))
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    return list(mapper.extract(doc, ctx(load_json("formulary_schemas.json"), REQUEST)))


def _inline(spec_extractions, xml: str, schemas, value_child="x:cellValue"):
    spec = {"kind": "xml", "namespaces": {"x": "urn:t"}, "extractions": spec_extractions}
    if value_child:
        spec["value_child"] = value_child
    mapper = DeclarativeMapper(parse_spec(spec))
    return list(mapper.extract(parse_document(xml.encode(), "XML"), ctx(schemas)))


def test_formulary_counts_per_target():
    rows = _formulary_rows()
    counts = {}
    for row in rows:
        counts[row.target] = counts.get(row.target, 0) + 1
    assert counts == {"app_db.xml.formulary_benefit_definition": 5, "app_db.xml.formulary_tier_definition": 2, "app_db.xml.formulary_drug_list": 3}


def test_formulary_first_benefit_row():
    first = _formulary_rows()[0]
    assert first.row_identity == (
        "/formularyList/formularyVO[1]/formularyCostSharingTypeList"
        "/formularyCostSharingTypeVO[1]/costSharingTypeList/costSharingTypeVO[1]"
    )
    assert first.fields == {
        "fileid": "7", "binder_name": "ABCD-DC25-1", "run_id": "42", "templateVersion": "v13.0",
        "issuer_id": "12345", "state_code": "DC", "formulary_id": "DCF001", "drug_list_id": "1",
        "num_of_tiers": "2", "drug_tier_id": "1", "one_month": "Yes", "three_month_in": "Yes",
        "three_month_out": "No", "cost_coverage_type": "In Network", "prescription_period": "1 Month",
        "copay": "$10.00", "coin": "0%",
    }
    assert first.extras is None


def test_unmapped_child_lands_in_extras():
    drugs = [r for r in _formulary_rows() if r.target == "app_db.xml.formulary_drug_list"]
    assert drugs[2].extras == {"quantityLimit": "Yes"}
    assert drugs[0].extras is None


def test_children_as_fields():
    xml = '<root xmlns="urn:t"><pkg><a><cellValue>1</cellValue></a><b><cellValue>2</cellValue></b><zz><cellValue>9</cellValue></zz></pkg></root>'
    rows = _inline([{"target": "pkg", "records": "/x:root/x:pkg", "fields": {"*": "x:*"}}], xml, {"pkg": ["a", "b"]})
    assert rows == [LandRow("pkg", "/pkg", {"a": "1", "b": "2"}, {"zz": "9"})]


def test_unpivot_children():
    xml = '<root xmlns="urn:t"><items><age><cellValue>21</cellValue></age><rate><cellValue>300</cellValue></rate></items></root>'
    rows = _inline(
        [{"target": "rate", "records": "/x:root/x:items", "fields": {"item": "$row_identity"},
          "unpivot": {"over": "x:*", "name_column": "component_name", "value_column": "component_value"}}],
        xml, {"rate": ["item", "component_name", "component_value"]},
    )
    assert [r.row_identity for r in rows] == ["/items#age", "/items#rate"]
    assert rows[1].fields == {"item": "/items", "component_name": "rate", "component_value": "300"}


def test_multi_match_lands_first_and_keeps_all_in_extras():
    xml = '<root xmlns="urn:t"><d><tier><cellValue>1</cellValue></tier><tier><cellValue>2</cellValue></tier></d></root>'
    rows = _inline([{"target": "d", "records": "/x:root/x:d", "fields": {"tier": "x:tier"}}], xml, {"d": ["tier"]})
    assert rows[0].fields == {"tier": "1"}
    assert rows[0].extras == {"tier": ["1", "2"]}


def test_missing_path_is_null_and_empty_element_is_empty_string():
    xml = '<root xmlns="urn:t"><d><a><cellValue/></a></d></root>'
    rows = _inline([{"target": "d", "records": "/x:root/x:d", "fields": {"a": "x:a", "b": "x:b"}}], xml, {"d": ["a", "b"]})
    assert rows[0].fields == {"a": "", "b": None}


def test_parent_reference():
    xml = '<root xmlns="urn:t"><pkg><id>P1</id><ben><n>x</n></ben></pkg><pkg><id>P2</id><ben><n>y</n></ben></pkg></root>'
    rows = _inline(
        [
            {"target": "package", "records": "/x:root/x:pkg", "fields": {"id": "x:id"}},
            {"target": "benefit", "records": "/x:root/x:pkg/x:ben", "fields": {"n": "x:n", "pkg": "$parent(package)"}},
        ],
        xml, {"package": ["id"], "benefit": ["n", "pkg"]}, value_child=None,
    )
    benefits = [r for r in rows if r.target == "benefit"]
    assert [b.fields["pkg"] for b in benefits] == ["/pkg[1]", "/pkg[2]"]


def test_record_level_exception_becomes_mapping_error(monkeypatch):
    original = DeclarativeMapper._value

    def flaky(self, el):
        value = original(self, el)
        if value == "boom":
            raise ValueError("bad value")
        return value

    monkeypatch.setattr(DeclarativeMapper, "_value", flaky)
    xml = '<root xmlns="urn:t"><d><a>ok</a></d><d><a>boom</a></d></root>'
    rows = _inline([{"target": "d", "records": "/x:root/x:d", "fields": {"a": "x:a"}}], xml, {"d": ["a"]}, value_child=None)
    assert isinstance(rows[0], LandRow)
    assert isinstance(rows[1], RejectRow) and rows[1].reason_code == "mapping_error"
    assert rows[1].row_identity == "/d[2]"


def test_invalid_xpath_is_a_spec_error():
    with pytest.raises(SpecError, match="invalid XPath"):
        parse_spec({"kind": "xml", "extractions": [{"target": "d", "records": "/x:[", "fields": {"a": "x:a"}}]})


def test_xml_spec_rejects_tabular_document():
    mapper = DeclarativeMapper(parse_spec(load_json("formulary_spec.json")))
    with pytest.raises(SpecError, match="xml spec"):
        list(mapper.extract(parse_document(b"a,b\n1,2\n", "CSV"), ctx({})))
