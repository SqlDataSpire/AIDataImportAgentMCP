"""NDJSON reader: one JSON object per line — design §8.6."""

from __future__ import annotations

import json

from .base import TabularDocument
from .json_reader import build_table
from .sniff import decode_text


def read_ndjson(data: bytes) -> TabularDocument:
    text, encoding = decode_text(data)
    items: list[tuple[str, str, object]] = []
    for line_no, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            obj: object = json.loads(line)
        except json.JSONDecodeError as exc:
            obj = exc
        items.append((f"L{line_no}", line, obj))
    return TabularDocument("NDJSON", [build_table(items, "invalid_json_line")], encoding)
