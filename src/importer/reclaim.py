"""Reclaim — re-run rejects for a schema against a new rule (design §5, §7).

**Fully implemented** (the scaffold's second green invariant). When a new repair
rule is added for a schema, every *open* reject for that schema is re-attempted
against it. Rows that newly pass move ``rejected -> reclaimed`` (landed); rows
that still fail stay put for the next rule.

Idempotency (§7): a row lands exactly once. ``open_rejects_for_schema`` only
returns rows still in ``rejected`` status, and :meth:`RejectStore.mark_reclaimed`
refuses to move an already-reclaimed row — so re-running the whole pile with the
same rule lands nothing new on the second pass. Reclaim only ever moves rows
*out of* reject, never back, which is exactly what keeps ``percent_loaded``
monotonic (see ``metrics.monotonic_update``).

The ``land`` hook is where a reclaimed row is written to the raw zone. It is
injected (not imported) so these mechanics stay DB-free and testable; the
default is a no-op suitable for the property tests. Reclaim produces
late-arriving data in the raw zone — the downstream transform stage must
tolerate re-runs and out-of-order arrival.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import Reject
from .reject_store import RejectStore
from .rules.base import RepairRule

# Called with each (reject, repaired_payload) that newly lands. No-op default.
LandHook = Callable[[Reject, str], None]


def _noop_land(_reject: Reject, _payload: str) -> None:
    return None


def select_newly_landable(
    store: RejectStore, schema_id: str, rule: RepairRule
) -> list[tuple[Reject, str]]:
    """Dry run: which open rejects would ``rule`` make landable, and to what.

    Pure read — does not mutate the store. Useful for agent evals that assert a
    proposed rule verifies against captured payloads before it is activated.
    """
    out: list[tuple[Reject, str]] = []
    for reject in store.open_rejects_for_schema(schema_id):
        repaired = rule.try_repair(reject.raw_payload)
        if repaired is not None:
            out.append((reject, repaired))
    return out


def reclaim_schema(
    store: RejectStore,
    schema_id: str,
    rule: RepairRule,
    land: LandHook = _noop_land,
) -> int:
    """Re-run all open rejects for ``schema_id`` against ``rule``.

    Lands each newly-repairable row exactly once and returns the count landed on
    *this* pass. Idempotent: calling again with the same rule returns 0, because
    every row it could fix is now ``reclaimed`` and no longer open.
    """
    newly_landed = 0
    for reject, repaired in select_newly_landable(store, schema_id, rule):
        # mark_reclaimed is the idempotency guard: it only moves a row that is
        # still 'rejected'. Land into the raw zone only if we actually moved it.
        if store.mark_reclaimed(reject.file_id, reject.row_identity):
            land(reject, repaired)
            newly_landed += 1
    return newly_landed
