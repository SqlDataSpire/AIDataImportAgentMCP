from __future__ import annotations

import json

import pytest

from importer.loader import promote, stage
from importer.memory_store import MemoryStore, RawFault
from importer.models import LandRow, RejectRow
from importer.precheck import CheckedRow, PreCheck, storable_identity
from importer.repositories import StagedRow


def test_clean_row_passes_with_serialized_extras():
    result = PreCheck().check(LandRow("t", "L2", {"a": "1"}, {"x": "y"}))
    assert isinstance(result, CheckedRow)
    assert json.loads(result.extras_json) == {"x": "y"}


def test_duplicate_identity_is_rejected_with_a_unique_reject_identity():
    check = PreCheck()
    assert isinstance(check.check(LandRow("t", "L2", {"a": "1"})), CheckedRow)
    second = check.check(LandRow("t", "L2", {"a": "2"}))
    third = check.check(LandRow("t", "L2", {"a": "3"}))
    assert isinstance(second, RejectRow) and second.reason_code == "duplicate_row_identity"
    assert (second.row_identity, third.row_identity) == ("L2#dup1", "L2#dup2")
    assert isinstance(check.check(LandRow("other", "L2", {"a": "1"})), CheckedRow)


def test_too_long_identity_is_hashed_for_the_reject():
    rid = "x" * 300
    result = PreCheck().check(LandRow("t", rid, {"a": "1"}))
    assert isinstance(result, RejectRow) and result.reason_code == "row_identity_too_long"
    assert result.row_identity == storable_identity(rid)
    assert result.row_identity.startswith("h:") and len(result.row_identity) <= 256


def test_lone_surrogate_is_unencodable_and_payload_is_ascii_safe():
    result = PreCheck().check(LandRow("t", "L2", {"a": "bad\ud800"}))
    assert isinstance(result, RejectRow) and result.reason_code == "unencodable_value"
    result.raw_payload.encode("utf-16-le")


def test_non_serializable_extras():
    result = PreCheck().check(LandRow("t", "L2", {"a": "1"}, {"x": object()}))
    assert isinstance(result, RejectRow) and result.reason_code == "extras_not_serializable"


def _staged(n: int) -> list[StagedRow]:
    return [StagedRow(1, f"L{i}", 1, 1, {"a": str(i)}, None) for i in range(2, n + 2)]


def test_bisection_isolates_only_the_bad_rows():
    store = MemoryStore(fault=RawFault(bad_identities={"L7", "L42"}))
    with store.transaction() as tx:
        tx.raw.ensure_tables("t", ["a"])
        rejects = stage(tx.raw, 1, {"t": _staged(60)}, batch_size=16)
        promoted = promote(tx.raw, 1, ["t"])
    assert sorted(r.row_identity for r in rejects) == ["L42", "L7"]
    assert all(r.reason_code == "db_insert_error" and r.target == "t" for r in rejects)
    assert promoted == 58


def test_infrastructure_errors_are_not_bisected():
    store = MemoryStore(fault=RawFault(infra_on_stage=True))
    with pytest.raises(RuntimeError, match="connection"):
        with store.transaction() as tx:
            tx.raw.ensure_tables("t", ["a"])
            stage(tx.raw, 1, {"t": _staged(5)})


def test_restaging_clears_a_previous_attempt():
    store = MemoryStore()
    with store.transaction() as tx:
        tx.raw.ensure_tables("t", ["a"])
        stage(tx.raw, 1, {"t": _staged(5)})
        stage(tx.raw, 1, {"t": _staged(5)})
        assert promote(tx.raw, 1, ["t"]) == 5
