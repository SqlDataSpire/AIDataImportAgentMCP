"""Value masking at the model boundary (design §8.19, the data boundary).

Row data never crosses MCP. Anything derived from a file's rows (preview samples,
_extras, get_samples) is reduced to its shape first: every letter becomes "a", every
digit "9", other characters are kept, so "78079DC0320001-01" -> "99999aa9999999-99".
The caps are fixed constants, the same for every file; they never depend on the
file's size, width or how much of it a spec maps. Counts, column names, _extras keys
and reject codes are metadata and are not masked.
"""

from __future__ import annotations

SAMPLE_ROWS = 3        # sample rows per target in a preview
SAMPLE_COLUMNS = 20    # fields per sample row (mapped fields first, then _extras entries)
SAMPLE_CHARS = 40      # characters per masked value


def mask(value, max_chars: int = SAMPLE_CHARS):
    """The shape of one value: letters -> 'a', digits -> '9', everything else kept."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: mask(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, max_chars) for v in value]
    text = str(value)[:max_chars]
    return "".join("a" if ch.isalpha() else "9" if ch.isdigit() else ch for ch in text)


def mask_row(row: dict) -> dict:
    """A preview sample row: no row identity, masked values, at most SAMPLE_COLUMNS fields."""
    out: dict = {}
    shown = 0
    hidden = 0
    for key, value in row.items():
        if key in ("row_identity", "_extras"):
            continue
        if shown < SAMPLE_COLUMNS:
            out[key] = mask(value)
            shown += 1
        else:
            hidden += 1
    extras = row.get("_extras")
    if isinstance(extras, dict):
        kept = {}
        for key, value in extras.items():
            if shown < SAMPLE_COLUMNS:
                kept[key] = mask(value)
                shown += 1
            else:
                hidden += 1
        out["_extras"] = kept
    elif extras is not None:
        out["_extras"] = mask(extras)
    if hidden:
        out["_more_columns"] = hidden
    return out
