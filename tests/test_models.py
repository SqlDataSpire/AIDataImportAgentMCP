from __future__ import annotations

import json

import pytest

from importer.models import (
    Declared,
    FileResult,
    FileStatus,
    InvalidDeclaration,
    can_transition,
)


def test_reentry_transitions():
    assert can_transition(FileStatus.QUARANTINED, FileStatus.DISCOVERED)
    assert can_transition(FileStatus.FAILED, FileStatus.DISCOVERED)
    assert not can_transition(FileStatus.COMPLETED, FileStatus.DISCOVERED)
    assert not can_transition(FileStatus.QUARANTINED, FileStatus.DETECTED)


def test_declared_normalize_uppercases_and_strips():
    d = Declared.normalize(" dc ", "pc", "xml")
    assert d == Declared("DC", "PC", "XML")
    assert d.prefix == "DC|PC|XML"


@pytest.mark.parametrize(
    "project,cls,file_type",
    [("D|C", "PC", "XML"), ("DC", "", "XML"), ("DC", "PC", "PDF"), ("DC", "P C", "CSV")],
)
def test_declared_rejects_bad_parts(project, cls, file_type):
    with pytest.raises(InvalidDeclaration):
        Declared.normalize(project, cls, file_type)


def test_file_result_to_dict_is_json_ready():
    result = FileResult(file_id=7, status=FileStatus.COMPLETED, rows_total=3, rows_landed=3, percent_loaded=100.0)
    data = result.to_dict()
    assert data["status"] == "completed"
    assert data["file_id"] == 7
    json.dumps(data)
