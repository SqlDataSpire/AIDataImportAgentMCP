"""Dry run of a mapper against a document (design §8.9); no database writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import RejectRow
from .base import Mapper, RunContext


@dataclass
class PreviewResult:
    counts: dict[str, int]
    sample: dict[str, list[dict]]
    extras_keys: dict[str, list[str]]
    rejects: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


def preview(mapper: Mapper, doc, ctx: RunContext, n: int = 20) -> PreviewResult:
    counts: dict[str, int] = {}
    sample: dict[str, list[dict]] = {}
    extras: dict[str, set[str]] = {}
    rejects: dict[str, int] = {}
    for row in mapper.extract(doc, ctx):
        if isinstance(row, RejectRow):
            rejects[row.reason_code] = rejects.get(row.reason_code, 0) + 1
            continue
        counts[row.target] = counts.get(row.target, 0) + 1
        bucket = sample.setdefault(row.target, [])
        if len(bucket) < n:
            entry = {"row_identity": row.row_identity, **row.fields}
            if row.extras:
                entry["_extras"] = row.extras
            bucket.append(entry)
        if row.extras:
            extras.setdefault(row.target, set()).update(row.extras)
    return PreviewResult(counts, sample, {t: sorted(k) for t, k in extras.items()}, rejects)
