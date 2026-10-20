"""Reject repository — one row per rejected row (design §2.4a, §7).

A rejected *row* never fails the *file*; it gets a terminal home here and can be
reclaimed later when a new repair rule arrives. The grain, the uniqueness key
``(file_id, row_identity)``, and the ``rejected -> reclaimed`` status mirror the
``reject_repository`` table in sql/001_schema.sql.

Two implementations:

* :class:`RejectStore` — the ``Protocol`` the rest of the system codes against.
* :class:`InMemoryRejectStore` — a fully working reference backing used by the
  property tests, so the idempotent-reclaim invariant is green without a
  database. The MSSQL-backed store (``pyodbc``) is milestone M3.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Reject, RejectStatus, _utcnow


@runtime_checkable
class RejectStore(Protocol):
    """Narrow contract the loader, reclaim, and agent harness depend on."""

    def add(self, reject: Reject) -> bool:
        """Insert a reject. Idempotent on ``(file_id, row_identity)``.

        Returns True if a new row was stored, False if it already existed
        (the unique constraint makes a re-delivery a no-op, not a duplicate).
        """
        ...

    def open_rejects_for_schema(self, schema_id: str) -> list[Reject]:
        """All rows for ``schema_id`` still in ``rejected`` status."""
        ...

    def mark_reclaimed(self, file_id: int, row_identity: str) -> bool:
        """Flip one row ``rejected -> reclaimed``. Returns True if it moved.

        A row already reclaimed returns False — this is what makes a second
        reclaim pass land nothing new (idempotency; design §7).
        """
        ...

    def all_rejects(self) -> list[Reject]:
        """Every row regardless of status (diagnostics/tests)."""
        ...

    def count_open_for_file(self, file_id: int) -> int:
        """Rows for ``file_id`` still in ``rejected`` status (feeds rows_rejected, §8.12)."""
        ...


class InMemoryRejectStore:
    """Reference :class:`RejectStore` backed by a dict keyed by the reclaim key."""

    def __init__(self) -> None:
        self._rows: dict[tuple[int, str], Reject] = {}

    def add(self, reject: Reject) -> bool:
        key = reject.key
        if key in self._rows:
            return False  # uq_reject_row (file_id, row_identity) — re-delivery no-op
        self._rows[key] = reject
        return True

    def open_rejects_for_schema(self, schema_id: str) -> list[Reject]:
        return [
            r
            for r in self._rows.values()
            if r.schema_id == schema_id and r.status is RejectStatus.REJECTED
        ]

    def mark_reclaimed(self, file_id: int, row_identity: str) -> bool:
        row = self._rows.get((file_id, row_identity))
        if row is None or row.status is RejectStatus.RECLAIMED:
            return False
        row.status = RejectStatus.RECLAIMED
        row.reclaimed_at = _utcnow()
        return True

    def all_rejects(self) -> list[Reject]:
        return list(self._rows.values())

    def count_open_for_file(self, file_id: int) -> int:
        return sum(
            1 for r in self._rows.values() if r.file_id == file_id and r.status is RejectStatus.REJECTED
        )
