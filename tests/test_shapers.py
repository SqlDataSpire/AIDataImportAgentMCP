from __future__ import annotations

import io

import openpyxl
import pytest

from importer.mappers.spec import DeclarativeMapper, parse_spec
from importer.readers import parse_document
from importer.shapers import apply_shapers, registered_shapers
from tests.support import ctx


def sopa_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOPA"
    ws.append([None, None, "Primary Care Visit", None, "Specialist Visit", None])
    ws.append(["Plan ID", "2nd Tier Utilization", "Copay", "Coinsurance", "Copay", "Coinsurance"])
    ws.append(["P1", "0%", "$20", "0%", "$40", "10%"])
    ws.append(["P2", "10%", "$30", None, "$50", "20%"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_builtin_shapers_are_registered():
    assert {"excel.anchor_header", "excel.crosstab_unpivot"} <= registered_shapers()


def test_anchor_header_flattens_multi_row_headers():
    doc = apply_shapers(parse_document(sopa_workbook(), "EXCEL"),
                        [{"name": "excel.anchor_header", "options": {"sheet": "SOPA", "anchor": "2ndTierUtilization"}}])
    table = doc.table("SOPA")
    assert table.columns == [
        "planid", "2ndtierutilization", "primarycarevisit_copay", "primarycarevisit_coinsurance",
        "specialistvisit_copay", "specialistvisit_coinsurance",
    ]
    assert [r.row_identity for r in table.records] == ["SOPA!R3", "SOPA!R4"]
    assert table.records[1].values["primarycarevisit_coinsurance"] is None


def test_anchor_not_found_raises():
    with pytest.raises(ValueError, match="anchor"):
        apply_shapers(parse_document(sopa_workbook(), "EXCEL"),
                      [{"name": "excel.anchor_header", "options": {"sheet": "SOPA", "anchor": "nothere"}}])


def test_crosstab_unpivot_through_a_spec():
    spec = {
        "kind": "tabular",
        "shapers": [
            {"name": "excel.anchor_header", "options": {"sheet": "SOPA", "anchor": "2ndTierUtilization"}},
            {"name": "excel.crosstab_unpivot", "options": {"sheet": "SOPA", "id_columns": ["planid", "2ndtierutilization"]}},
        ],
        "extractions": [{"target": "plan_variant_benefit", "records": "SOPA", "auto": True}],
    }
    schema = {"plan_variant_benefit": ["planid", "2ndtierutilization", "benefit_name", "value"]}
    rows = list(DeclarativeMapper(parse_spec(spec, known_shapers=registered_shapers()))
                .extract(parse_document(sopa_workbook(), "EXCEL"), ctx(schema)))
    assert len(rows) == 7
    assert rows[0].row_identity == "SOPA!R3#primarycarevisit_copay"
    assert rows[0].fields == {"planid": "P1", "2ndtierutilization": "0%", "benefit_name": "primarycarevisit_copay", "value": "$20"}
