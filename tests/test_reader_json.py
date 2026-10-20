from __future__ import annotations

import pytest

from importer.readers import parse_document
from importer.readers.base import ReaderError


def test_json_array_flattens_nested_keys():
    data = b'[{"id": 1, "plan": {"name": "Gold", "tier": 2}, "tags": ["a"], "ok": true, "x": null}]'
    table = parse_document(data, "JSON").table("*")
    assert table.columns == ["id", "planname", "plantier", "tags", "ok", "x"]
    record = table.records[0]
    assert record.row_identity == "[0]"
    assert record.values == {
        "id": "1", "planname": "Gold", "plantier": "2", "tags": '["a"]', "ok": "true", "x": None,
    }


def test_json_non_object_element_is_rejected():
    records = parse_document(b'[{"a": 1}, 5]', "JSON").table("*").records
    assert records[1].reject_reason == "invalid_json_record"
    assert records[1].row_identity == "[1]"


def test_json_single_object_is_one_record():
    records = parse_document(b'{"a": "x"}', "JSON").table("*").records
    assert len(records) == 1 and records[0].values == {"a": "x"}


def test_json_corrupt():
    with pytest.raises(ReaderError) as err:
        parse_document(b'[{"a":', "JSON")
    assert err.value.reason_code == "corrupt"


def test_ndjson_bad_line_is_a_row_reject():
    data = b'{"id": 1, "name": "a"}\n{"id": 2, "name": "b"}\nnot json\n\n{"id": 3}\n'
    doc = parse_document(data, "NDJSON")
    table = doc.table("*")
    assert doc.format == "NDJSON"
    assert [r.row_identity for r in table.records] == ["L1", "L2", "L3", "L5"]
    bad = table.records[2]
    assert bad.reject_reason == "invalid_json_line"
    assert bad.raw == "not json"
    assert table.columns == ["id", "name"]
    assert table.records[3].values == {"id": "3"}
