"""Per-row landability pre-check before staging (design §8.10, layer 1).

Turns anything SQL Server would reject into a RejectRow up front, so batched
inserts never fail on data in the normal case.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import LandRow, RejectRow

MAX_ROW_IDENTITY = 256


def _encodable(value: object) -> bool:
    if value is None:
        return True
    try:
        str(value).encode("utf-16-le")
        return True
    except UnicodeEncodeError:
        return False


def storable_identity(rid: str) -> str:
    """rid itself if it fits NVARCHAR(256) and is encodable, else a stable hash."""
    if len(rid) <= MAX_ROW_IDENTITY and _encodable(rid):
        return rid
    return "h:" + hashlib.sha256(rid.encode("utf-8", "surrogatepass")).hexdigest()


def _payload(row: LandRow) -> str:
    return json.dumps(
        {"row_identity": row.row_identity, "fields": row.fields, "extras": row.extras},
        ensure_ascii=True, default=repr,
    )


@dataclass(frozen=True)
class CheckedRow:
    row: LandRow
    extras_json: str | None


class PreCheck:
    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def check(self, row: LandRow) -> CheckedRow | RejectRow:
        rid = row.row_identity
        if len(rid) > MAX_ROW_IDENTITY:
            return RejectRow(row.target, storable_identity(rid), _payload(row), "row_identity_too_long",
                             f"{len(rid)} characters: {rid[:200]}")
        key = (row.target, rid)
        occurrence = self._seen.get(key, 0)
        self._seen[key] = occurrence + 1
        if occurrence:
            return RejectRow(row.target, storable_identity(f"{rid}#dup{occurrence}"), _payload(row),
                             "duplicate_row_identity", f"occurrence {occurrence + 1} of {rid}")
        if not all(_encodable(v) for v in (rid, *row.fields.keys(), *row.fields.values())):
            return RejectRow(row.target, storable_identity(rid), _payload(row), "unencodable_value",
                             "a value contains characters SQL Server cannot store")
        extras_json = None
        if row.extras:
            try:
                extras_json = json.dumps(row.extras, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                return RejectRow(row.target, storable_identity(rid), _payload(row), "extras_not_serializable", str(exc))
            if not _encodable(extras_json):
                return RejectRow(row.target, storable_identity(rid), _payload(row), "unencodable_value",
                                 "extras contain characters SQL Server cannot store")
        return CheckedRow(row, extras_json)
