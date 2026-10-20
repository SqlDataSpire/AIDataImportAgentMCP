"""Delimited text reader (CSV / TSV / pipe / semicolon) — design §8.6."""

from __future__ import annotations

import csv

from .base import Record, ReaderError, Table, TabularDocument, normalize_header
from .sniff import decode_text

csv.field_size_limit(2**31 - 1)

FIXED = {"TSV": "\t", "PIPE": "|"}
CANDIDATES = ",\t|;"


def _sniff(sample_line: str) -> str:
    try:
        return csv.Sniffer().sniff(sample_line, delimiters=CANDIDATES).delimiter
    except csv.Error:
        counts = {d: sample_line.count(d) for d in CANDIDATES}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_delimited(data: bytes, file_type: str) -> TabularDocument:
    text, encoding = decode_text(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    header_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if header_idx is None:
        raise ReaderError("corrupt", "no header row")
    delimiter = FIXED.get(file_type) or _sniff(lines[header_idx])
    reader = csv.reader((line + "\n" for line in lines), delimiter=delimiter)

    raw_header: list[str] | None = None
    columns: list[str] = []
    records: list[Record] = []
    prev_end = 0
    try:
        for fields in reader:
            start = prev_end + 1
            prev_end = reader.line_num
            if not fields or not any(f.strip() for f in fields):
                continue
            if raw_header is None:
                raw_header = fields
                columns = normalize_header(fields)
                continue
            raw = "\n".join(lines[start - 1:prev_end])
            row_identity = f"L{start}"
            if len(fields) != len(columns):
                records.append(Record(
                    row_identity, raw,
                    reject_reason="field_count_mismatch",
                    detail=f"expected {len(columns)} fields, got {len(fields)}",
                ))
            else:
                records.append(Record(row_identity, raw, values=dict(zip(columns, fields))))
    except csv.Error as exc:
        raise ReaderError("corrupt", f"malformed delimited text: {exc}") from exc
    if raw_header is None:
        raise ReaderError("corrupt", "no header row")
    return TabularDocument("DELIMITED", [Table("*", list(raw_header), columns, records)], encoding)
