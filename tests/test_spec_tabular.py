from __future__ import annotations

import pytest

from importer.mappers.spec import DeclarativeMapper, SpecError, parse_spec
from importer.models import LandRow, RejectRow
from importer.readers import parse_document
from tests.support import URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, ctx, fixture


def _rows(spec, data, file_type, schemas, request_context=None):
    mapper = DeclarativeMapper(parse_spec(spec))
    return list(mapper.extract(parse_document(data, file_type), ctx(schemas, request_context)))


def test_variant_a_auto_maps_and_fills_special_sources():
    rows = _rows(URL_SPEC_A, fixture("url_template_a.csv").read_bytes(), "CSV", URL_SCHEMA, {"run_id": "42"})
    assert rows[0] == LandRow(
        "app_db.relational.url_template", "L2",
        {"urlid": "12345VT0010001", "newurl": "https://example.org/sbc/1", "urltype": "SBC", "file_id": "7", "run_id": "42"},
        None,
    )
    assert len(rows) == 2


def test_variant_b_maps_url_into_newurl():
    rows = _rows(URL_SPEC_B, fixture("url_template_b.csv").read_bytes(), "CSV", URL_SCHEMA, {"run_id": "42"})
    assert rows[0].fields["newurl"] == "https://example.net/sbc/9"
    assert "url" not in rows[0].fields
    assert rows[0].extras is None


def test_unmapped_columns_go_to_extras_or_are_ignored():
    data = b"urlid,newurl,urltype,notes\n1,u,SBC,hello\n"
    assert _rows(URL_SPEC_A, data, "CSV", URL_SCHEMA)[0].extras == {"notes": "hello"}
    spec = {"kind": "tabular", "extractions": [{"target": "app_db.relational.url_template", "records": "*", "auto": True, "unmapped": "ignore"}]}
    assert _rows(spec, data, "CSV", URL_SCHEMA)[0].extras is None


def test_reader_rejects_are_forwarded_with_the_target():
    rows = _rows(URL_SPEC_A, b"urlid,newurl,urltype\n1,u,SBC\nshort\n", "CSV", URL_SCHEMA)
    assert rows[1] == RejectRow("app_db.relational.url_template", "L3", "short", "field_count_mismatch", "expected 3 fields, got 1")


def test_auto_matches_mixed_case_schema_columns():
    spec = {"kind": "tabular", "extractions": [{"target": "plans", "records": "*", "auto": True}]}
    rows = _rows(spec, b"Plan ID,Metal\nP1,Gold\n", "CSV", {"plans": ["PlanID", "Metal"]})
    assert rows[0].fields == {"PlanID": "P1", "Metal": "Gold"}


def test_unpivot_all_unused_columns():
    spec = {"kind": "tabular", "extractions": [{
        "target": "plan_cost", "records": "*", "fields": {"planid": "planid"},
        "unpivot": {"over": "*", "name_column": "component_name", "value_column": "component_value"},
    }]}
    rows = _rows(spec, b"planid,deductible,moop\nP1,500,8000\n", "CSV", {"plan_cost": ["planid", "component_name", "component_value"]})
    assert [r.row_identity for r in rows] == ["L2#deductible", "L2#moop"]
    assert rows[1].fields == {"planid": "P1", "component_name": "moop", "component_value": "8000"}


def test_excel_sheet_records_by_name():
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws.append(["Plan ID", "Metal"])
    ws.append(["P1", "Gold"])
    buf = io.BytesIO()
    wb.save(buf)
    spec = {"kind": "tabular", "extractions": [{"target": "plans", "records": "Plan", "auto": True}]}
    rows = _rows(spec, buf.getvalue(), "EXCEL", {"plans": ["planid", "metal"]})
    assert rows == [LandRow("plans", "Plan!R2", {"planid": "P1", "metal": "Gold"}, None)]


@pytest.mark.parametrize(
    "spec,message",
    [
        ({"extractions": []}, "kind"),
        ({"kind": "tabular", "extractions": []}, "non-empty"),
        ({"kind": "tabular", "extractions": [{"target": "bad-name", "records": "*", "auto": True}]}, "target"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*"}]}, "maps nothing"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*", "fields": {"a": "$parent(t)"}}]}, "only valid in xml"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*", "auto": True, "unmapped": "drop"}]}, "unmapped"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*", "fields": {"a": "$nope"}}]}, "unknown special"),
    ],
)
def test_invalid_specs(spec, message):
    with pytest.raises(SpecError, match=message):
        parse_spec(spec)


def test_target_must_have_an_active_schema():
    with pytest.raises(SpecError, match="no active schema"):
        parse_spec(URL_SPEC_A, known_schemas={"other"})


def test_tabular_spec_rejects_xml_document():
    mapper = DeclarativeMapper(parse_spec(URL_SPEC_A))
    with pytest.raises(SpecError, match="tabular spec"):
        list(mapper.extract(parse_document(b"<r/>", "XML"), ctx(URL_SCHEMA)))
