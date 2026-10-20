"""Reader output types (design §8.6): every format becomes a Document."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalize_name(name: object) -> str:
    """Legacy column normalization: lowercase, alphanumeric only."""
    if name is None:
        return ""
    return _NON_ALNUM.sub("", str(name).lower())


def dedupe_names(names: list[str]) -> list[str]:
    """Fill empty names with col<N> and suffix repeats with _<n>."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, name in enumerate(names, start=1):
        base = name or f"col{i}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        out.append(base if count == 1 else f"{base}_{count}")
    return out


def normalize_header(raw: list[object]) -> list[str]:
    return dedupe_names([normalize_name(n) for n in raw])


class ReaderError(Exception):
    """The file cannot start: ``corrupt`` or ``unparseable`` (→ quarantined)."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass
class Record:
    row_identity: str
    raw: str
    values: dict[str, str | None] = field(default_factory=dict)
    reject_reason: str | None = None
    detail: str | None = None


@dataclass
class Table:
    name: str
    raw_columns: list[str]
    columns: list[str]
    records: list[Record]
    grid: list[list[str | None]] | None = None  # Excel only: every row incl. header, for shapers


@dataclass
class TabularDocument:
    format: str  # DELIMITED | EXCEL | JSON | NDJSON
    tables: list[Table]
    encoding: str | None = None

    def table(self, name: str) -> Table:
        if name == "*":
            if not self.tables:
                raise KeyError("document has no tables")
            return self.tables[0]
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(f"no table named {name!r}; have {[t.name for t in self.tables]}")

    def replace_table(self, table: Table) -> "TabularDocument":
        return TabularDocument(
            self.format, [table if t.name == table.name else t for t in self.tables], self.encoding
        )


@dataclass
class XmlDocument:
    root: object  # lxml.etree._Element
    namespace: str | None
    local_name: str
    encoding: str | None = None
    format: str = "XML"


Document = Union[TabularDocument, XmlDocument]
