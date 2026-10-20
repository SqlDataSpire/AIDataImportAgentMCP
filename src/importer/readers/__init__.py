"""Format readers (design §8.6). parse_document is the single entry point."""

from __future__ import annotations

from pathlib import Path

from ..models import DELIMITED_TYPES
from .base import Document, ReaderError
from .sniff import check_declared


def parse_document(data: bytes, file_type: str) -> Document:
    check_declared(data, file_type)
    if file_type in DELIMITED_TYPES:
        from .delimited import read_delimited
        return read_delimited(data, file_type)
    if file_type == "EXCEL":
        from .excel import read_excel
        return read_excel(data)
    if file_type == "JSON":
        from .json_reader import read_json
        return read_json(data)
    if file_type == "NDJSON":
        from .ndjson import read_ndjson
        return read_ndjson(data)
    if file_type == "XML":
        from .xml_reader import read_xml
        return read_xml(data)
    raise ReaderError("unparseable", f"no reader for file type {file_type!r}")


def open_document(path: str | Path, file_type: str) -> Document:
    return parse_document(Path(path).read_bytes(), file_type)
