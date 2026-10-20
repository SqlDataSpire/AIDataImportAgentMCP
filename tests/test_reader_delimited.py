from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from importer.readers import parse_document
from importer.readers.base import ReaderError, normalize_header


def test_normalize_header_dedupes_and_fills():
    assert normalize_header(["URL ID", "url-id", None, ""]) == ["urlid", "urlid_2", "col3", "col4"]


def test_csv_header_records_and_identity():
    doc = parse_document(b"URL ID,New URL,urlType\r\na1,http://x,Payment\r\n\r\na2,http://y,SBC\r\n", "CSV")
    table = doc.table("*")
    assert table.columns == ["urlid", "newurl", "urltype"]
    assert table.raw_columns == ["URL ID", "New URL", "urlType"]
    assert [r.row_identity for r in table.records] == ["L2", "L4"]
    assert table.records[0].values == {"urlid": "a1", "newurl": "http://x", "urltype": "Payment"}
    assert doc.encoding == "utf-8"
    assert doc.format == "DELIMITED"


def test_field_count_mismatch_is_a_reader_reject():
    records = parse_document(b"a,b\n1,2\n3\n", "CSV").table("*").records
    bad = records[1]
    assert bad.reject_reason == "field_count_mismatch"
    assert bad.raw == "3"
    assert bad.row_identity == "L3"
    assert bad.values == {}


def test_quoted_multiline_field_keeps_physical_start_line():
    records = parse_document(b'a,b\n"x\ny",2\n3,4\n', "CSV").table("*").records
    assert [r.row_identity for r in records] == ["L2", "L4"]
    assert records[0].values["a"] == "x\ny"


def test_pipe_declared_fixes_delimiter():
    rec = parse_document(b"a|b\n1|2\n", "PIPE").table("*").records[0]
    assert rec.values == {"a": "1", "b": "2"}


def test_semicolon_is_sniffed():
    rec = parse_document(b"a;b\n1;2\n", "CSV").table("*").records[0]
    assert rec.values == {"a": "1", "b": "2"}


def test_cp1252_fallback():
    doc = parse_document("name\ncaf\xe9\n".encode("cp1252"), "CSV")
    assert doc.encoding == "cp1252"
    assert doc.table("*").records[0].values == {"name": "café"}


@pytest.mark.parametrize(
    "data,file_type",
    [(b"PK\x03\x04junk", "CSV"), (b"a,b\n", "XML"), (b"<x/>", "JSON"), (b"[1]", "NDJSON"), (b"a,b", "EXCEL")],
)
def test_declared_mismatch_is_unparseable(data, file_type):
    with pytest.raises(ReaderError) as err:
        parse_document(data, file_type)
    assert err.value.reason_code == "unparseable"


def test_empty_file_is_corrupt():
    with pytest.raises(ReaderError) as err:
        parse_document(b"", "CSV")
    assert err.value.reason_code == "corrupt"


def test_header_only_file_has_no_records():
    assert parse_document(b"a,b\n", "CSV").table("*").records == []


cell = st.text(alphabet="abcxyz019 ", max_size=5)


@given(st.lists(st.lists(cell, min_size=2, max_size=2), max_size=30))
def test_row_identities_are_unique(rows):
    body = "h1,h2\n" + "".join(",".join(r) + "\n" for r in rows)
    ids = [r.row_identity for r in parse_document(body.encode(), "CSV").table("*").records]
    assert len(ids) == len(set(ids))
