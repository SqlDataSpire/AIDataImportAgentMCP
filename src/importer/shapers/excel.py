"""Excel shapers, ported from the structural steps of the legacy SOPA parser."""

from __future__ import annotations

from ..readers.base import Record, Table, TabularDocument, dedupe_names, normalize_name
from . import shaper


def _is_empty(row: list) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)


@shaper("excel.anchor_header")
def anchor_header(doc: TabularDocument, *, sheet: str, anchor: str, search_rows: int = 4, search_cols: int = 14) -> TabularDocument:
    table = doc.table(sheet)
    if table.grid is None:
        raise ValueError(f"sheet {sheet!r} has no grid (not an Excel document)")
    grid = table.grid
    target = normalize_name(anchor)
    hit = None
    for r in range(min(search_rows, len(grid))):
        for c in range(min(search_cols, len(grid[r]))):
            if normalize_name(grid[r][c]) == target:
                hit = (r, c)
                break
        if hit:
            break
    if hit is None:
        raise ValueError(f"anchor {anchor!r} not found in sheet {sheet!r} within {search_rows}x{search_cols}")
    anchor_row = hit[0]
    width = max(len(row) for row in grid[: anchor_row + 1])
    header_rows: list[list] = []
    for r in range(anchor_row + 1):
        row = list(grid[r]) + [None] * (width - len(grid[r]))
        if r < anchor_row:  # merged category cells: forward-fill to the right
            last = None
            for c in range(width):
                if row[c] is None:
                    row[c] = last
                else:
                    last = row[c]
        header_rows.append(row)
    raw_header = [
        "_".join(p for p in (normalize_name(header_rows[r][c]) for r in range(anchor_row + 1)) if p)
        for c in range(width)
    ]
    columns = dedupe_names(raw_header)
    records: list[Record] = []
    for idx in range(anchor_row + 1, len(grid)):
        row = grid[idx]
        if _is_empty(row):
            continue
        values = {columns[i]: (row[i] if i < len(row) else None) for i in range(width)}
        for i in range(width, len(row)):
            if row[i] is not None:
                values[f"col{i + 1}"] = row[i]
        records.append(Record(f"{table.name}!R{idx + 1}", "\t".join("" if v is None else v for v in row), values=values))
    return doc.replace_table(Table(table.name, raw_header, columns, records, grid=grid))


@shaper("excel.crosstab_unpivot")
def crosstab_unpivot(doc: TabularDocument, *, sheet: str, id_columns: list[str], name_column: str = "benefit_name", value_column: str = "value") -> TabularDocument:
    table = doc.table(sheet)
    ids = list(id_columns)
    missing = [c for c in ids if c not in table.columns]
    if missing:
        raise ValueError(f"id columns {missing} are not in sheet {sheet!r}")
    records: list[Record] = []
    for record in table.records:
        if record.reject_reason:
            records.append(record)
            continue
        base = {c: record.values.get(c) for c in ids}
        for column, value in record.values.items():
            if column in ids or value is None:
                continue
            records.append(Record(f"{record.row_identity}#{column}", record.raw,
                                  values={**base, name_column: column, value_column: value}))
    columns = ids + [name_column, value_column]
    return doc.replace_table(Table(table.name, list(columns), columns, records, grid=table.grid))
