from __future__ import annotations

import io
from pathlib import Path

import openpyxl
from hypothesis import assume, given
from hypothesis import strategies as st

from importer.detector import detect
from importer.fingerprint import fingerprint
from importer.models import Declared, SignatureHit
from importer.readers import parse_document

FIXTURE = Path(__file__).parent / "fixtures" / "formulary_dc.xml"
CSV_A = b"URL ID,New URL,URL Type\nx,y,z\n"


def _sig(data: bytes, file_type: str, project: str = "ME"):
    return fingerprint(parse_document(data, file_type), Declared.normalize(project, "PC", file_type))


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_csv_signature_shape():
    sig = _sig(CSV_A, "CSV")
    assert sig.fingerprint.startswith("ME|PC|CSV|h:")
    assert len(sig.physical_part) == 18
    assert sig.xml_namespace is None
    assert sig.observed == {"columns": ["urlid", "newurl", "urltype"]}


def test_xml_signature_is_readable():
    sig = _sig(FIXTURE.read_bytes(), "XML", project="DC")
    assert sig.fingerprint == "DC|PC|XML|prescriptionDrugTemplateVO"
    assert sig.xml_namespace == "http://vo.ffe.cms.hhs.gov"


def test_header_case_and_punctuation_do_not_matter():
    assert _sig(b"url_id,NEW-URL,url type\n", "CSV").physical_part == _sig(CSV_A, "CSV").physical_part


def test_different_header_changes_signature():
    assert _sig(b"urlid,url,urltype\n", "CSV").fingerprint != _sig(CSV_A, "CSV").fingerprint


def test_json_key_order_does_not_matter():
    assert _sig(b'[{"a": 1, "b": 2}]', "JSON") == _sig(b'[{"b": 1, "a": 2}]', "JSON")


def test_excel_sheet_order_does_not_matter():
    one = _xlsx({"Issuer": [["id"]], "Plan": [["plan id"]]})
    two = _xlsx({"Plan": [["plan id"]], "Issuer": [["id"]]})
    assert _sig(one, "EXCEL").physical_part == _sig(two, "EXCEL").physical_part


class FakeRegistry:
    def __init__(self, namespace: str | None):
        self.namespace = namespace

    def lookup(self, fp: str, ns: str | None):
        return SignatureHit(1, fp, 10, ns) if ns == self.namespace else None


def test_detect_hit_and_namespace_mismatch():
    doc = parse_document(FIXTURE.read_bytes(), "XML")
    declared = Declared.normalize("DC", "PC", "XML")
    sig, hit = detect(doc, declared, FakeRegistry("http://vo.ffe.cms.hhs.gov"))
    assert hit is not None and hit.mapper_version_id == 10
    _, miss = detect(doc, declared, FakeRegistry("urn:other"))
    assert miss is None


names = st.lists(st.text(alphabet="abcdefgh", min_size=1, max_size=6), min_size=1, max_size=8, unique=True)


@given(names)
def test_signature_is_deterministic_and_normalized(cols):
    plain = (",".join(cols) + "\n").encode()
    noisy = (",".join(c.upper() + "_" for c in cols) + "\n").encode()
    assert _sig(plain, "CSV") == _sig(noisy, "CSV")


@given(names, names)
def test_different_ordered_headers_differ(a, b):
    assume(a != b)
    assert _sig((",".join(a) + "\n").encode(), "CSV").fingerprint != _sig((",".join(b) + "\n").encode(), "CSV").fingerprint


def test_xml_observed_carries_the_nesting_tree():
    sig = _sig(FIXTURE.read_bytes(), "XML", project="DC")
    assert sig.observed["root"] == "prescriptionDrugTemplateVO"
    assert sig.observed["tree"]["tree"]["path"] == "/prescriptionDrugTemplateVO"
    assert sig.fingerprint == "DC|PC|XML|prescriptionDrugTemplateVO"  # the signature itself is unchanged
