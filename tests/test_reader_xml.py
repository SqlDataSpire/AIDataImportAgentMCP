from __future__ import annotations

from pathlib import Path

import pytest

from importer.readers import parse_document
from importer.readers.base import ReaderError
from importer.readers.xml_reader import element_identity

NS = "http://vo.ffe.cms.hhs.gov"
FIXTURE = Path(__file__).parent / "fixtures" / "formulary_dc.xml"


def test_reads_root_and_namespace():
    doc = parse_document(FIXTURE.read_bytes(), "XML")
    assert doc.local_name == "prescriptionDrugTemplateVO"
    assert doc.namespace == NS
    assert doc.encoding == "UTF-8"
    assert doc.format == "XML"


def test_element_identity_is_namespace_free_with_indexes_only_when_repeated():
    doc = parse_document(FIXTURE.read_bytes(), "XML")
    el = doc.root.xpath(
        "/x:prescriptionDrugTemplateVO/x:formularyList/x:formularyVO[1]/x:formularyCostSharingTypeList"
        "/x:formularyCostSharingTypeVO[2]/x:costSharingTypeList/x:costSharingTypeVO[1]",
        namespaces={"x": NS},
    )[0]
    assert element_identity(el, doc.root) == (
        "/formularyList/formularyVO[1]/formularyCostSharingTypeList"
        "/formularyCostSharingTypeVO[2]/costSharingTypeList/costSharingTypeVO[1]"
    )
    assert element_identity(doc.root, doc.root) == "/"


def test_identity_falls_back_to_ordinal_when_too_long():
    tag = "averyveryverylongelementname"
    xml = "<r>" + "".join(f"<{tag}>" for _ in range(12)) + "".join(f"</{tag}>" for _ in range(12)) + "</r>"
    doc = parse_document(xml.encode(), "XML")
    leaf = doc.root
    for _ in range(12):
        leaf = leaf[0]
    assert element_identity(leaf, doc.root) == ".".join(["1"] * 12)


def test_corrupt_xml():
    with pytest.raises(ReaderError) as err:
        parse_document(b"<a><b></a>", "XML")
    assert err.value.reason_code == "corrupt"


def test_external_entities_are_not_resolved():
    data = b'<!DOCTYPE r [<!ENTITY e SYSTEM "file:///c:/windows/win.ini">]><r>&e;</r>'
    doc = parse_document(data, "XML")
    assert doc.root.text is None
