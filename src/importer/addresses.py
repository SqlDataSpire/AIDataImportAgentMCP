"""Destination addresses and the raw-zone names derived from them (design §8.15).

A schema is identified by the legacy destination it mirrors: database.schema.table,
lowercased. Raw and stage table names are derived mechanically, never hand-chosen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

PART = re.compile(r"^[A-Za-z0-9_]+$")
MAX_IDENTIFIER = 128
RESERVED_SCHEMAS = frozenset({"dbo", "stage", "sys", "guest", "information_schema", "meta", "raw"})


class AddressError(ValueError):
    """Not a usable destination address (design §8.15)."""


@dataclass(frozen=True)
class Address:
    database: str
    schema: str
    table: str

    @property
    def key(self) -> str:
        return f"{self.database}.{self.schema}.{self.table}"

    @property
    def raw_schema(self) -> str:
        return f"{self.database}_{self.schema}"

    @property
    def stage_table(self) -> str:
        return f"{self.raw_schema}__{self.table}"

    @property
    def pk_name(self) -> str:
        return f"pk_{self.stage_table}"


def parse(text: str) -> Address:
    parts = text.strip().split(".") if isinstance(text, str) else []
    if len(parts) != 3:
        raise AddressError(f"{text!r}: expected DATABASE.SCHEMA.TABLE")
    for part in parts:
        if not PART.match(part):
            raise AddressError(f"{text!r}: {part!r} must match [A-Za-z0-9_]+")
    address = Address(*(p.lower() for p in parts))
    if address.raw_schema in RESERVED_SCHEMAS or address.raw_schema.startswith("db_"):
        raise AddressError(f"{text!r}: raw schema {address.raw_schema!r} is reserved")
    for name in (address.raw_schema, address.table, address.stage_table, address.pk_name):
        if len(name) > MAX_IDENTIFIER:
            raise AddressError(f"{text!r}: generated name {name[:40]}... exceeds {MAX_IDENTIFIER} characters")
    return address


def collision(address: Address, existing: Iterable[str]) -> str | None:
    """The registered address that maps onto the same raw or stage table, if any."""
    for other_key in existing:
        if other_key == address.key:
            continue
        try:
            other = parse(other_key)
        except AddressError:
            continue
        same_raw = (other.raw_schema, other.table) == (address.raw_schema, address.table)
        if same_raw or other.stage_table == address.stage_table:
            return other_key
    return None
