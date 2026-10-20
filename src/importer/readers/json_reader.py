"""JSON reader: a flat array of objects (or one object) — design §8.6."""

from __future__ import annotations

import json

from .base import Record, ReaderError, Table, TabularDocument, normalize_name
from .sniff import decode_text


def scalar_to_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def flatten(obj: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in obj.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            out.update(flatten(value, name))
        else:
            out[name] = value
    return out


def build_table(items: list[tuple[str, str, object]], bad_reason: str) -> Table:
    """items are (row_identity, raw text, parsed value or the parse exception)."""
    columns: list[str] = []
    seen: set[str] = set()
    records: list[Record] = []
    for row_identity, raw, obj in items:
        if not isinstance(obj, dict):
            detail = str(obj) if isinstance(obj, Exception) else f"expected a JSON object, got {type(obj).__name__}"
            records.append(Record(row_identity, raw, reject_reason=bad_reason, detail=detail))
            continue
        values: dict[str, str | None] = {}
        for key, value in flatten(obj).items():
            column = normalize_name(key) or "col"
            values[column] = scalar_to_str(value)
            if column not in seen:
                seen.add(column)
                columns.append(column)
        records.append(Record(row_identity, raw, values=values))
    return Table("*", list(columns), columns, records)


def read_json(data: bytes) -> TabularDocument:
    text, encoding = decode_text(data)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReaderError("corrupt", f"invalid JSON: {exc}") from exc
    elements = parsed if isinstance(parsed, list) else [parsed]
    items = [(f"[{i}]", json.dumps(obj, ensure_ascii=False), obj) for i, obj in enumerate(elements)]
    return TabularDocument("JSON", [build_table(items, "invalid_json_record")], encoding)
