"""Loader — stage with bisection, then promote (design §8.10).

Staging tables are all-NVARCHAR(MAX) heaps, so a data failure there is
unexpected; if one happens, the batch is halved recursively until the bad
row(s) are isolated as rejects. Infrastructure errors propagate (file failed).
"""

from __future__ import annotations

import json

from .models import RejectRow
from .repositories import RawZone, RowDataError, StagedRow

DEFAULT_BATCH_SIZE = 5000  # Task 2 spike decision


def _payload(row: StagedRow) -> str:
    return json.dumps(
        {"row_identity": row.row_identity, "fields": row.fields, "extras": row.extras_json},
        ensure_ascii=True, default=repr,
    )


def stage(raw: RawZone, file_id: int, rows_by_target: dict[str, list[StagedRow]], batch_size: int = DEFAULT_BATCH_SIZE) -> list[RejectRow]:
    rejects: list[RejectRow] = []
    for target, rows in rows_by_target.items():
        raw.clear_stage(file_id, target)
        for start in range(0, len(rows), batch_size):
            _stage_bisect(raw, target, rows[start:start + batch_size], rejects)
    return rejects


def _stage_bisect(raw: RawZone, target: str, batch: list[StagedRow], rejects: list[RejectRow]) -> None:
    if not batch:
        return
    try:
        raw.stage_batch(target, batch)
        return
    except RowDataError as exc:
        if len(batch) == 1:
            rejects.append(RejectRow(target, batch[0].row_identity, _payload(batch[0]), "db_insert_error", str(exc)))
            return
    middle = len(batch) // 2
    _stage_bisect(raw, target, batch[:middle], rejects)
    _stage_bisect(raw, target, batch[middle:], rejects)


def promote(raw: RawZone, file_id: int, targets) -> int:
    return sum(raw.promote(file_id, target) for target in targets)
