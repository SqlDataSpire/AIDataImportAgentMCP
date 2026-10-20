"""Declared-vs-actual check and text decoding (design §8.6)."""

from __future__ import annotations

from .base import ReaderError

ZIP = b"PK"
OLE2 = b"\xd0\xcf\x11\xe0"
UTF8_BOM = b"\xef\xbb\xbf"


def decode_text(data: bytes) -> tuple[str, str]:
    """UTF-8 (BOM-aware), falling back to cp1252; returns (text, encoding)."""
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError as exc:
        raise ReaderError("corrupt", f"text is neither UTF-8 nor cp1252: {exc}") from exc


def _lead(data: bytes) -> bytes:
    if data.startswith(UTF8_BOM):
        data = data[len(UTF8_BOM):]
    return data.lstrip()


def check_declared(data: bytes, file_type: str) -> None:
    if not data:
        raise ReaderError("corrupt", "file is empty")
    lead = _lead(data)
    if file_type == "XML":
        if not lead.startswith(b"<"):
            raise ReaderError("unparseable", "declared XML but content does not start with '<'")
    elif file_type == "EXCEL":
        if not (data.startswith(ZIP) or data.startswith(OLE2)):
            raise ReaderError("unparseable", "declared EXCEL but content is neither .xlsx (ZIP) nor .xls (OLE2)")
    elif file_type == "JSON":
        if lead[:1] not in (b"[", b"{"):
            raise ReaderError("unparseable", "declared JSON but content does not start with '[' or '{'")
    elif file_type == "NDJSON":
        first = next((line.strip() for line in lead.splitlines() if line.strip()), b"")
        if not first.startswith(b"{"):
            raise ReaderError("unparseable", "declared NDJSON but the first line is not a JSON object")
    else:  # delimited family
        if data.startswith(ZIP) or data.startswith(OLE2) or b"\x00" in data[:4096]:
            raise ReaderError("unparseable", f"declared {file_type} but content looks binary")
