from __future__ import annotations

from importer.masking import SAMPLE_CHARS, SAMPLE_COLUMNS, mask, mask_row


def test_mask_keeps_shape_and_never_value():
    assert mask("78079DC0320001-01") == "99999aa9999999-99"
    assert mask("BlueDental Preferred High") == "aaaaaaaaaa aaaaaaaaa aaaa"
    assert mask("20% Coinsurance, after Deductible") == "99% aaaaaaaaaaa, aaaaa aaaaaaaaaa"
    assert mask("Ñandú 3.5") == "aaaaa 9.9"
    assert mask(1234.5) == "9999.9" and mask(None) is None


def test_mask_is_clipped_to_a_fixed_length():
    assert mask("x" * 500) == "a" * SAMPLE_CHARS


def test_mask_row_drops_identity_masks_extras_and_caps_columns():
    row = {"row_identity": "Sheet1!R5#ACME-123", "plan": "Gold 2027",
           "_extras": {f"c{i}": f"Value {i}" for i in range(40)}}
    out = mask_row(row)
    assert "row_identity" not in out and out["plan"] == "aaaa 9999"
    shown = len(out) - 2 + len(out["_extras"])  # minus _extras and _more_columns
    assert shown == SAMPLE_COLUMNS and out["_more_columns"] == 41 - SAMPLE_COLUMNS
    assert all(set(v) <= set("a9 ") for v in out["_extras"].values())
