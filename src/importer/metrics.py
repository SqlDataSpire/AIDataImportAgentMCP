"""Per-file metrics: ``percent_parsed`` / ``percent_loaded`` (design §7).

This module is **fully implemented** — it carries one of the two invariants the
scaffold guarantees green out of the box (the other is idempotent reclaim, in
``reclaim.py``):

* ``percent_loaded`` is a **monotonic floor** — reclaim only ever moves rows
  *out of* reject into landed, never the reverse, so the value only rises. The
  number handed to the caller is a guaranteed lower bound: never wrong,
  occasionally conservative.

We deliberately do not compute "percent valid" here; validity is a downstream
semantic judgment and reporting it would drag cleaning back into the importer.
"""

from __future__ import annotations

# percent_loaded is stored to DECIMAL(5,2). Quantization to that precision is a
# *persistence* concern and lives in compute_percent (the value producer), NOT
# in monotonic_update: rounding inside the floor comparison can round a reading
# down and dip below the established floor, breaking the invariant. So the floor
# clamps to [0, 100] but never re-rounds the current value.
_PRECISION = 2


def compute_percent(count: int, total: int) -> float:
    """Percent of ``count`` out of ``total``, clamped to [0, 100].

    A zero/negative total yields 0.0 (nothing parsed means nothing to load).
    """
    if total <= 0:
        return 0.0
    if count <= 0:
        return 0.0
    pct = 100.0 * count / total
    if pct > 100.0:
        pct = 100.0
    return round(pct, _PRECISION)


def monotonic_update(current: float | None, candidate: float) -> float:
    """Return the new ``percent_loaded``, enforcing the monotonic floor (§7).

    The stored value never decreases. First observation (``current is None``)
    adopts the candidate; thereafter the maximum wins. ``candidate`` is itself
    clamped to [0, 100] so a malformed input cannot breach the floor invariant.

    >>> monotonic_update(None, 40.0)
    40.0
    >>> monotonic_update(40.0, 55.0)
    55.0
    >>> monotonic_update(55.0, 40.0)   # a lower reading can never lower the floor
    55.0
    """
    candidate = _clamp(candidate)
    if current is None:
        return candidate
    current = _clamp(current)
    return current if current >= candidate else candidate


def _clamp(value: float) -> float:
    """Clamp to [0, 100] without rounding (see note on quantization above)."""
    if value < 0.0:
        return 0.0
    if value > 100.0:
        return 100.0
    return value
