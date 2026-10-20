"""Property: ``percent_loaded`` is a monotonic floor (design §7, Blueprint §D).

Reclaim only ever moves rows out of reject into landed, never the reverse, so
the value handed to the caller only rises. These properties pin that invariant
on ``metrics.monotonic_update`` directly.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from importer.metrics import compute_percent, monotonic_update

# Percent-shaped values, including the clamp edges.
percents = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
# Arbitrary candidates, including out-of-range, to prove the clamp holds the floor.
any_candidates = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)


@given(current=percents, candidate=any_candidates)
def test_update_never_decreases(current: float, candidate: float) -> None:
    result = monotonic_update(current, candidate)
    assert result >= current - 1e-9  # the floor never drops


@given(candidate=any_candidates)
def test_first_observation_adopts_clamped_candidate(candidate: float) -> None:
    result = monotonic_update(None, candidate)
    assert 0.0 <= result <= 100.0
    assert result == min(100.0, max(0.0, candidate))  # clamp-only, no rounding


@given(start=percents, updates=st.lists(any_candidates, max_size=50))
def test_sequence_is_monotonic_nondecreasing(start: float, updates: list[float]) -> None:
    value = monotonic_update(None, start)
    prev = value
    for u in updates:
        value = monotonic_update(value, u)
        assert value >= prev - 1e-9  # monotonic across the whole sequence
        assert 0.0 <= value <= 100.0
        prev = value


@given(current=percents, candidate=percents)
def test_in_range_update_is_the_max(current: float, candidate: float) -> None:
    assert monotonic_update(current, candidate) == max(current, candidate)


@given(count=st.integers(min_value=-5, max_value=1000), total=st.integers(min_value=-5, max_value=1000))
def test_compute_percent_bounds(count: int, total: int) -> None:
    pct = compute_percent(count, total)
    assert 0.0 <= pct <= 100.0
