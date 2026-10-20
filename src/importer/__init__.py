"""Data Agent: Importer — ELT import agent driven by a ledger state machine.

See the Confluence pages for the design of record:
  * Develop Data Agent: Importer (project)
  * Data Import Agent — Design Decisions (the §-numbered record referenced here)
  * Implementation Blueprint & Build Plan
  * Schema DDL (MSSQL)

Fully implemented in this scaffold (the two guaranteed-green invariants):
  * importer.metrics   — monotonic-floor percent_loaded (§7)
  * importer.reclaim   — idempotent reclaim (§5, §7)
  * importer.reject_store.InMemoryRejectStore — reference backing for the above
  * importer.models    — status lifecycle + core dataclasses

Everything else is an honest stub mapped to a milestone (see README).
"""

__all__ = ["models", "metrics", "reclaim", "reject_store"]
__version__ = "0.1.0"
