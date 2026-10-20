"""Property: reclaim is idempotent (design §5, §7, Blueprint §D).

Re-running the whole reject pile for a schema with the same rule keeps
re-attempting still-failing rows, but a row lands exactly once: the second pass
lands nothing new. We also assert reclaim never moves rows backward (monotonic),
which is what keeps ``percent_loaded`` a floor.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from importer.models import Reject, RejectStatus
from importer.reclaim import reclaim_schema, select_newly_landable
from importer.reject_store import InMemoryRejectStore
from importer.rules.base import RepairRule

SCHEMA = "schema_fixture"


class EvenLengthRule(RepairRule):
    """Toy deterministic rule: a payload is 'landable' iff its length is even.

    Stands in for a real rule-as-data (e.g. 'sniff mixed delimiters per line').
    Deterministic and side-effect-free, as the contract requires.
    """

    def __init__(self) -> None:
        self.schema_id = SCHEMA
        self.version_no = 1

    def try_repair(self, raw_payload: str) -> str | None:
        return raw_payload if len(raw_payload) % 2 == 0 else None


def _store_with(payloads: list[str]) -> InMemoryRejectStore:
    store = InMemoryRejectStore()
    for i, p in enumerate(payloads):
        store.add(
            Reject(
                file_id=1,
                row_identity=f"r{i}",
                raw_payload=p,
                reason_code="unlandable",
                schema_id=SCHEMA,
            )
        )
    return store


payload_lists = st.lists(st.text(max_size=12), max_size=40)


@given(payloads=payload_lists)
def test_second_pass_lands_nothing_new(payloads: list[str]) -> None:
    store = _store_with(payloads)
    rule = EvenLengthRule()

    _ = reclaim_schema(store, SCHEMA, rule)
    second = reclaim_schema(store, SCHEMA, rule)

    assert second == 0  # idempotent: nothing new on the re-run


@given(payloads=payload_lists)
def test_first_pass_lands_exactly_the_repairable(payloads: list[str]) -> None:
    store = _store_with(payloads)
    rule = EvenLengthRule()

    expected = len(select_newly_landable(store, SCHEMA, rule))
    landed = reclaim_schema(store, SCHEMA, rule)

    assert landed == expected
    reclaimed = [r for r in store.all_rejects() if r.status is RejectStatus.RECLAIMED]
    assert len(reclaimed) == expected


@given(payloads=payload_lists)
def test_total_landed_matches_first_pass_across_reruns(payloads: list[str]) -> None:
    store = _store_with(payloads)
    rule = EvenLengthRule()

    first = reclaim_schema(store, SCHEMA, rule)
    # Any number of further passes add nothing.
    extra = sum(reclaim_schema(store, SCHEMA, rule) for _ in range(3))

    assert extra == 0
    total_reclaimed = sum(
        1 for r in store.all_rejects() if r.status is RejectStatus.RECLAIMED
    )
    assert total_reclaimed == first


@given(payloads=payload_lists)
def test_reclaim_never_moves_rows_backward(payloads: list[str]) -> None:
    store = _store_with(payloads)
    rule = EvenLengthRule()

    reclaim_schema(store, SCHEMA, rule)
    reclaimed_after_first = {
        r.key for r in store.all_rejects() if r.status is RejectStatus.RECLAIMED
    }
    reclaim_schema(store, SCHEMA, rule)
    reclaimed_after_second = {
        r.key for r in store.all_rejects() if r.status is RejectStatus.RECLAIMED
    }
    # monotonic: the reclaimed set only grows (here: stays identical)
    assert reclaimed_after_first <= reclaimed_after_second


def test_add_is_idempotent_on_reclaim_key() -> None:
    store = InMemoryRejectStore()
    r = Reject(file_id=1, row_identity="r0", raw_payload="xx", reason_code="x", schema_id=SCHEMA)
    assert store.add(r) is True
    assert store.add(r) is False  # uq (file_id, row_identity) — re-delivery no-op
    assert len(store.all_rejects()) == 1
