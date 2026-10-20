# M2a — Deterministic Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the model-free core of the importer. A file with a registered signature lands every landable record in `raw.<schema>` tables and reports `percent_loaded`. A file with an unknown signature is profiled, matched against what's already registered, and quarantined with a "looks like…" proposal.

**Architecture:**
- **Ledger state machine:** `pipeline.process()` drives each file through a state machine over a ledger. Every status change is its own short transaction on a `Store` (an in-memory store for unit tests, an MSSQL store via DataEngine for integration).
- **Readers:** they turn every format into a `Document`. Signatures come from the requestor's declared parameters plus the file's structure.
- **Mappers:** they turn documents into `LandRow` / `RejectRow` streams.
- **Loader:** stages rows in batches (with bisection when a batch fails on data), then promotes them with `INSERT … WHERE NOT EXISTS`.

**Tech Stack:** Python 3.11+ (venv is 3.14), SQLAlchemy 2.x Core, pyodbc, the user's `Python-DataEngine` library, lxml, openpyxl, xlrd, python-dotenv, pytest, Hypothesis.

**Spec:** `docs/superpowers/specs/2026-10-02-m2a-fast-path-design.md` (decision record: Confluence *Design Decisions* §8.1–8.13).

## Global Constraints

- The two existing property suites (`tests/test_metrics_monotonic.py`, `tests/test_reclaim_idempotent.py`) must stay green and **unchanged**. `metrics.py`, `reclaim.py` and `rules/base.py` are not modified. Quantization stays in `compute_percent`.
- The model is never in the row-data path. M2a has no `anthropic` / `claude-agent-sdk` imports.
- **Agent-ready outputs:** every result type M2b will expose as a Claude Agent SDK tool (`FileResult`, `PreviewResult`, `Candidate`) has `to_dict()` returning JSON-serializable data.
- Only `src/importer/db.py` imports `DataEngine`, and only inside `get_engine()`. Unit tests never import DataEngine.
- SQL values are always bound parameters. Identifiers are validated against `^[A-Za-z0-9_]+$` and bracket-quoted. DataEngine's string helpers (`query`, `execute`, `interop`) are never used with values.
- No stored secrets. `database.env` (gitignored) holds the `importer` connection with `"trusted": "yes"`, or `UN` without `PW`. A `PW` is rejected.
- Values land exactly as written: no trimming, coercion or cleaning.
- Declared parameters: `project`, `cls`, `file_type` are uppercased and must match `^[A-Za-z0-9_-]+$`. `file_type` ∈ `CSV, TSV, PIPE, DELIMITED, EXCEL, JSON, NDJSON, XML`.
- Signatures are `PROJECT|CLASS|FILE_TYPE|<physical part>`, matched exactly. The XML namespace must also match.
- `row_identity` is at most 256 characters. Raw columns are `NVARCHAR(MAX) NULL`. Lineage columns are `_file_id, _row_identity, _signature_id, _artifact_version_id, _extras, _landed_at`.
- DDL policy (prototype): edit `sql/001_schema.sql` in place, with git as the history. Every change to it is mirrored on Confluence page 3775135746 in the same task.
- Commands run from the repo root in Git Bash: `.venv/Scripts/python -m pytest …`. Plain `pytest` excludes `integration`-marked tests.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Refinements to the spec made while planning

These are small, deliberate, and recorded on Confluence in Task 20:
1. **Spec `kind` key.** Mapper definitions carry `"kind": "xml" | "tabular" | "plugin"`. This makes validation and dispatch explicit.
2. **Matcher score.** The score is the *share of the file's columns found in the schema* (`|observed ∩ schema| / |observed|`), with Jaccard as the tie-break. Plain Jaccard under-scores mirrored tables, because their lineage and context columns (`file_id`, `run_id`, …) never appear in files.
3. **`excel.crosstab_unpivot`** takes `(sheet, id_columns, name_column, value_column)`. Header-row flattening is done by `excel.anchor_header`.
4. **New `file_ledger.last_error NVARCHAR(MAX)`** records why a file `failed` (the spec says failures go to "ledger + log").
5. **Two extra CLI commands, `mapper add` and `signature register`.** Hand registration needs them, and the M2a acceptance cases rely on hand registration.
6. **`detect()` returns `(Signature, SignatureHit | None)`.** The pipeline needs the signature on a miss to profile the file.
7. **New reader reject code `invalid_json_record`**, for JSON array elements that aren't objects.

## File map

| Path | Status | Responsibility |
|---|---|---|
| `pyproject.toml` | modify | deps, `importer` script, pytest markers |
| `src/importer/config.py` | create | `home()`, `importer_env()` |
| `src/importer/db.py` | create | `load_connection_document()`, `get_engine()` (lazy DataEngine) |
| `scripts/spike_fast_executemany.py` | create | DEV spike |
| `src/importer/models.py` | modify | statuses (+ re-entry), `Declared`, `Signature`, `SignatureHit`, `LandRow`, `RejectRow`, `FileResult` |
| `src/importer/mappers/base.py` | rewrite | `RunContext`, `Mapper` ABC |
| `src/importer/readers/{__init__,base,sniff,delimited,excel,json_reader,ndjson,xml_reader}.py` | create | readers |
| `src/importer/fingerprint.py`, `detector.py` | rewrite | signature, lookup |
| `src/importer/repositories.py` | create | row dataclasses, errors, Protocols |
| `src/importer/registry.py` | delete | superseded by `repositories.py` |
| `src/importer/reject_store.py` | modify | `count_open_for_file` |
| `src/importer/memory_store.py` | create | in-memory `Store` with real rollback |
| `src/importer/mappers/spec.py` | create | spec parsing + `DeclarativeMapper` |
| `src/importer/shapers/{__init__,excel}.py` | create | shaper registry + Excel shapers |
| `src/importer/mappers/loading.py`, `mappers/preview.py`, `mappers/plugins/__init__.py` | create | artifact → Mapper, preview, plugin folder |
| `src/importer/admin.py` | create | `register_schema/mapper/signature` |
| `src/importer/profiler.py` | create | profile + matcher |
| `src/importer/precheck.py` | create | per-row landability check |
| `src/importer/loader.py` | rewrite | stage (bisection) + promote |
| `src/importer/pipeline.py` | rewrite | `process()`, `retry()` |
| `sql/001_schema.sql` | rewrite | control-plane DDL (M2a) |
| `src/importer/schema_tables.py` | create | raw/stage DDL generation, rebuild drop SQL |
| `src/importer/mssql_store.py` | create | SQL Server repositories |
| `src/importer/cli.py` | create | `importer` CLI |
| `tests/support.py`, `tests/fixtures/*` | create | shared test helpers and synthetic fixtures |
| `CLAUDE.md`, `README.md` | modify | layout, run instructions, status |

---

### Task 1: Dependencies, configuration, and the DataEngine connection

**Files:**
- Modify: `pyproject.toml`
- Create: `src/importer/config.py`, `src/importer/db.py`
- Test: `tests/test_db_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `importer.config.home() -> Path`, `importer.config.importer_env() -> str | None`, `importer.db.ConfigError`, `importer.db.CONNECTION_NAME = "importer"`, `importer.db.database_env_path() -> Path`, `importer.db.load_connection_document(path: Path | None = None, name: str = "importer") -> dict`, `importer.db.get_engine(path: Path | None = None, name: str = "importer") -> sqlalchemy.engine.Engine`.

- [ ] **Step 1: Update `pyproject.toml`**

Replace the `[project]` dependencies, the dev extras and the pytest section with:

```toml
[project]
name = "data-import-agent"
version = "0.1.0"
description = "ELT import agent driven by a ledger state machine (deterministic fast path + agent slow path)."
requires-python = ">=3.11"
dependencies = [
    "Python-DataEngine>=2.4",
    "sqlalchemy>=2.0",
    "pyodbc>=5",
    "lxml>=5",
    "openpyxl>=3.1",
    "xlrd>=2.0",
    "python-dotenv>=1.0",
    # "claude-agent-sdk",   # agent slow path (M2b / M5)
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "hypothesis>=6",
    "xlwt>=1.3",
]

[project.scripts]
importer = "importer.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
addopts = "-q -m \"not integration\""
markers = ["integration: needs the DEV SQL Server (database.env 'importer' entry)"]
```

Keep the existing `[build-system]` and `[tool.ruff]` sections unchanged.

- [ ] **Step 2: Install and confirm wheels on the venv's Python**

Run:
```bash
.venv/Scripts/python --version
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -c "import lxml, openpyxl, xlrd, pyodbc, sqlalchemy, dotenv; print('ok')"
```
Expected: `Python 3.14.x`, a successful install, then `ok`. If any wheel fails to install on 3.14, stop. Create a 3.12 venv instead (`py -3.12 -m venv .venv`), re-run this step, and note the change in the Task 1 commit message.

- [ ] **Step 3: Write the failing tests**

`tests/test_db_config.py`:

```python
"""database.env parsing and the lazy-DataEngine rule (spec §11)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from importer.db import ConfigError, load_connection_document


def _env(tmp_path, body: str):
    path = tmp_path / "database.env"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_importer_entry(tmp_path):
    path = _env(
        tmp_path,
        """databases = '{"importer": {"type": "mssql", "server": "dev01", "database": "imp_dev", "trusted": "yes"}}'\n""",
    )
    doc = load_connection_document(path)
    assert doc["server"] == "dev01"
    assert doc["database"] == "imp_dev"
    assert doc["trusted"] == "yes"


def test_rejects_stored_password(tmp_path):
    path = _env(
        tmp_path,
        """databases = '{"importer": {"type": "mssql", "server": "s", "database": "d", "UN": "u", "PW": "secret"}}'\n""",
    )
    with pytest.raises(ConfigError, match="password"):
        load_connection_document(path)


def test_missing_entry(tmp_path):
    path = _env(tmp_path, """databases = '{"main": {"type": "mssql", "server": "s", "database": "d"}}'\n""")
    with pytest.raises(ConfigError, match="no 'importer'"):
        load_connection_document(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_connection_document(tmp_path / "nope.env")


def test_requires_mssql_type(tmp_path):
    path = _env(tmp_path, """databases = '{"importer": {"type": "postgres", "server": "s", "database": "d"}}'\n""")
    with pytest.raises(ConfigError, match="mssql"):
        load_connection_document(path)


def test_importing_db_does_not_import_dataengine():
    code = "import sys, importer.db; print('DataEngine' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
```

- [ ] **Step 4: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_db_config.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.db'`.

- [ ] **Step 5: Implement `config.py` and `db.py`**

`src/importer/config.py`:

```python
"""Importer settings: where database.env / .env live, and importer-only flags."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


def home() -> Path:
    """Directory holding database.env and .env.

    IMPORTER_HOME wins; otherwise the repo root (editable install); otherwise the
    current directory (the importer used as a library from another project).
    """
    override = os.environ.get("IMPORTER_HOME")
    if override:
        return Path(override)
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo
    return Path.cwd()


def importer_env() -> str | None:
    """IMPORTER_ENV from the environment or .env (``DEV`` enables db rebuild)."""
    value = os.environ.get("IMPORTER_ENV") or dotenv_values(home() / ".env").get("IMPORTER_ENV")
    return value.strip().upper() if value else None
```

`src/importer/db.py`:

```python
"""SQL Server connectivity through the user's DataEngine library (design §8.8).

DataEngine is imported lazily, inside get_engine(): importing it runs its
initialize(), which reads database.env from the current directory and builds an
engine for every entry. Nothing else in the importer imports DataEngine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from .config import home

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

CONNECTION_NAME = "importer"


class ConfigError(RuntimeError):
    """database.env is missing, malformed, or violates the no-stored-secrets rule."""


def database_env_path() -> Path:
    return home() / "database.env"


def load_connection_document(path: Path | None = None, name: str = CONNECTION_NAME) -> dict:
    path = path or database_env_path()
    if not path.exists():
        raise ConfigError(f"{path} not found")
    raw = dotenv_values(path).get("databases")
    if not raw:
        raise ConfigError(f"{path} has no 'databases' entry")
    try:
        docs = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: 'databases' is not valid JSON: {exc}") from exc
    doc = docs.get(name)
    if doc is None:
        raise ConfigError(f"{path}: no '{name}' connection")
    if doc.get("type") != "mssql":
        raise ConfigError(f"connection '{name}' must be type 'mssql'")
    if doc.get("PW"):
        raise ConfigError(
            f"connection '{name}' stores a password; use Windows ('trusted': 'yes') or Entra (UN only) auth"
        )
    for key in ("server", "database"):
        if not doc.get(key):
            raise ConfigError(f"connection '{name}' is missing '{key}'")
    return doc


def get_engine(path: Path | None = None, name: str = CONNECTION_NAME) -> "Engine":
    doc = load_connection_document(path, name)
    from DataEngine.mssql import SqlConnectionObject  # lazy on purpose (module docstring)

    connection = SqlConnectionObject(
        name=name,
        server=doc["server"],
        database=doc["database"],
        UN=doc.get("UN") or None,
        PW=None,
        trusted=doc.get("trusted", "no"),
    )
    return connection.engine
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_db_config.py -v`
Expected: 6 passed.

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all green (the 10 original property cases plus the 6 new ones).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/importer/config.py src/importer/db.py tests/test_db_config.py
git commit -m "$(cat <<'EOF'
Add runtime deps and DataEngine-backed DB configuration

db.py is the only DataEngine importer and imports it lazily, so unit tests
and library users never trigger DataEngine's auto-initialize.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: DEV spike — `fast_executemany` with `NVARCHAR(MAX)`

This task needs the user. It needs the DEV server name, the database name, the auth mode, and a database where the user may create tables.

**Files:**
- Create: `scripts/spike_fast_executemany.py`, `database.env` (gitignored; never committed), `.env` (gitignored)

**Interfaces:**
- Consumes: `importer.db.get_engine()`.
- Produces: two decisions used later. `loader.DEFAULT_BATCH_SIZE` (Task 15) and `mssql_store.USE_SETINPUTSIZES` (Task 18).

- [ ] **Step 1: Ask the user for the connection details, then create the two local config files**

Ask for: DEV server, database (it must allow `CREATE TABLE` / `CREATE SCHEMA`), and auth mode. The auth mode is Windows (`"trusted": "yes"`) or Entra MFA (`"UN": "<user@domain>"`, no `PW`).

Create `database.env` at the repo root:
```
databases = '{"importer": {"type": "mssql", "server": "<DEV server>", "database": "<db>", "trusted": "yes"}}'
```
Create `.env` at the repo root:
```
IMPORTER_ENV=DEV
```
Run: `git status --short`
Expected: neither file is listed (both are gitignored).

- [ ] **Step 2: Write the spike script**

`scripts/spike_fast_executemany.py`:

```python
"""Spike (design §8.10): pyodbc fast_executemany into NVARCHAR(MAX) on DEV.

Measures batch insert time and Python-side peak memory with and without
setinputsizes. Driver (C-side) memory is not visible to tracemalloc; watch the
python process in Task Manager during the 20,000-row runs.
"""

from __future__ import annotations

import sys
import time
import tracemalloc

import pyodbc

from importer.db import get_engine

COLS = 12


def _rows(n: int) -> list[tuple[str, ...]]:
    return [tuple(f"r{i}c{j}-" + "x" * 40 for j in range(COLS)) for i in range(n)]


def _run(engine, n: int, use_sizes: bool) -> tuple[float, int, int]:
    create = "CREATE TABLE #spike (" + ", ".join(f"c{j} NVARCHAR(MAX) NULL" for j in range(COLS)) + ")"
    insert = "INSERT INTO #spike VALUES (" + ", ".join(["?"] * COLS) + ")"
    data = _rows(n)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS #spike")
        conn.exec_driver_sql(create)
        cursor = conn.connection.cursor()
        try:
            cursor.fast_executemany = True
            if use_sizes:
                cursor.setinputsizes([(pyodbc.SQL_WVARCHAR, 0, 0)] * COLS)
            tracemalloc.start()
            started = time.perf_counter()
            cursor.executemany(insert, data)
            elapsed = time.perf_counter() - started
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        finally:
            cursor.close()
        landed = conn.exec_driver_sql("SELECT COUNT(*) FROM #spike").scalar()
        conn.exec_driver_sql("DROP TABLE #spike")
    return elapsed, peak, landed


def main() -> int:
    engine = get_engine()
    for n in (1_000, 5_000, 20_000):
        for use_sizes in (False, True):
            try:
                elapsed, peak, landed = _run(engine, n, use_sizes)
                print(f"rows={n:>6} setinputsizes={use_sizes!s:<5} secs={elapsed:7.2f} "
                      f"py_peak_MB={peak / 1e6:7.1f} landed={landed}")
            except Exception as exc:  # report every variant, keep going
                print(f"rows={n:>6} setinputsizes={use_sizes!s:<5} FAILED: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run the spike with the user present**

The first connection may open an Entra MFA prompt.

Run: `.venv/Scripts/python scripts/spike_fast_executemany.py`
Expected: six lines, each `landed` equal to `rows`.

- [ ] **Step 4: Decide and record**

Rules:
- `DEFAULT_BATCH_SIZE` is the largest of 1,000 / 5,000 / 20,000 that succeeded in under 30 seconds without the python process exceeding ~1 GB in Task Manager. Default 5,000.
- `USE_SETINPUTSIZES = True` only if the runs without `setinputsizes` failed or were more than 2× slower.

Paste the six result lines and the two decisions into the commit message. Then add one line to Confluence Design Decisions §8.10 under "Spike first", using the Atlassian MCP (fetch HTML → append → update, keeping all existing content):

> Spike <date>: `DEFAULT_BATCH_SIZE=<n>`, `USE_SETINPUTSIZES=<bool>` (<one-line evidence>).

- [ ] **Step 5: Commit**

```bash
git add scripts/spike_fast_executemany.py
git commit -m "$(cat <<'EOF'
Add fast_executemany NVARCHAR(MAX) spike

<paste the six result lines>
Decision: DEFAULT_BATCH_SIZE=<n>, USE_SETINPUTSIZES=<bool>.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Domain models and the Mapper contract

**Files:**
- Modify: `src/importer/models.py` (full rewrite below)
- Rewrite: `src/importer/mappers/base.py`, `src/importer/loader.py` (temporary stub), `src/importer/detector.py` (temporary stub)
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `FileStatus`, with `LEGAL_TRANSITIONS` gaining `QUARANTINED → DISCOVERED` and `FAILED → DISCOVERED`; `can_transition(src, dst) -> bool`.
  - Enums: `ArtifactType.{SCHEMA,MAPPER,RULE}`, `SignatureStatus.{PROPOSED,ACTIVE,RETIRED}`, `RejectStatus`.
  - Constants: `FILE_TYPES`, `DELIMITED_TYPES`.
  - `InvalidDeclaration(ValueError)`; `Declared(project, cls, file_type)` with `Declared.normalize(project, cls, file_type) -> Declared` and the `.prefix` property.
  - `Signature(fingerprint, physical_part, xml_namespace, observed: dict)`; `SignatureHit(signature_id, fingerprint, mapper_version_id, xml_namespace)`.
  - `LandRow(target, row_identity, fields, extras=None)`; `RejectRow(target, row_identity, raw_payload, reason_code, detail=None)`.
  - `Reject` (unchanged); `FileMetrics` (unchanged).
  - `FileResult(file_id, status, rows_total=0, rows_landed=0, rows_rejected=0, percent_parsed=None, percent_loaded=None, quarantine_reason=None, proposal_summary=None)` with `.to_dict()`.
  - `importer.mappers.base.RunContext(file_id, signature_id, artifact_version_id, request_context={}, schemas={})`; the abstract `Mapper` with `version: int`, the abstract property `targets -> tuple[str, ...]`, and the abstract `extract(doc, ctx) -> Iterator[LandRow | RejectRow]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:

```python
from __future__ import annotations

import json

import pytest

from importer.models import (
    Declared,
    FileResult,
    FileStatus,
    InvalidDeclaration,
    can_transition,
)


def test_reentry_transitions():
    assert can_transition(FileStatus.QUARANTINED, FileStatus.DISCOVERED)
    assert can_transition(FileStatus.FAILED, FileStatus.DISCOVERED)
    assert not can_transition(FileStatus.COMPLETED, FileStatus.DISCOVERED)
    assert not can_transition(FileStatus.QUARANTINED, FileStatus.DETECTED)


def test_declared_normalize_uppercases_and_strips():
    d = Declared.normalize(" dc ", "pc", "xml")
    assert d == Declared("DC", "PC", "XML")
    assert d.prefix == "DC|PC|XML"


@pytest.mark.parametrize(
    "project,cls,file_type",
    [("D|C", "PC", "XML"), ("DC", "", "XML"), ("DC", "PC", "PDF"), ("DC", "P C", "CSV")],
)
def test_declared_rejects_bad_parts(project, cls, file_type):
    with pytest.raises(InvalidDeclaration):
        Declared.normalize(project, cls, file_type)


def test_file_result_to_dict_is_json_ready():
    result = FileResult(file_id=7, status=FileStatus.COMPLETED, rows_total=3, rows_landed=3, percent_loaded=100.0)
    data = result.to_dict()
    assert data["status"] == "completed"
    assert data["file_id"] == 7
    json.dumps(data)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: FAIL, with `ImportError: cannot import name 'Declared'`.

- [ ] **Step 3: Rewrite `src/importer/models.py`**

```python
"""Core domain models for the import agent.

Shared vocabulary across the deterministic fast path and the agent slow path.
Mirrors sql/001_schema.sql and the design record (Design Decisions §2, §3, §8).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


class FileStatus(str, Enum):
    """Ledger status lifecycle for a file (design §3, §8.12).

        discovered -> detected -> landed -> promoted -> completed

    with branches to ``quarantined`` (cannot start) or ``failed`` (control-plane
    error). Both may re-enter at ``discovered`` via retry / confirm-and-bind.
    """

    DISCOVERED = "discovered"
    DETECTED = "detected"
    LANDED = "landed"
    PROMOTED = "promoted"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


LEGAL_TRANSITIONS: dict[FileStatus, frozenset[FileStatus]] = {
    FileStatus.DISCOVERED: frozenset({FileStatus.DETECTED, FileStatus.QUARANTINED, FileStatus.FAILED}),
    FileStatus.DETECTED: frozenset({FileStatus.LANDED, FileStatus.QUARANTINED, FileStatus.FAILED}),
    FileStatus.LANDED: frozenset({FileStatus.PROMOTED, FileStatus.FAILED}),
    FileStatus.PROMOTED: frozenset({FileStatus.COMPLETED, FileStatus.FAILED}),
    FileStatus.COMPLETED: frozenset(),
    FileStatus.QUARANTINED: frozenset({FileStatus.DISCOVERED}),
    FileStatus.FAILED: frozenset({FileStatus.DISCOVERED}),
}


def can_transition(src: FileStatus, dst: FileStatus) -> bool:
    """Whether ``src -> dst`` is a legal status transition (§3, §8.12)."""
    return dst in LEGAL_TRANSITIONS[src]


class ArtifactType(str, Enum):
    """Versioned artifact kinds in ``artifact_versions`` (§2, §8.10)."""

    SCHEMA = "schema"
    MAPPER = "mapper"
    RULE = "rule"


class RejectStatus(str, Enum):
    REJECTED = "rejected"
    RECLAIMED = "reclaimed"


class SignatureStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    RETIRED = "retired"


FILE_TYPES = frozenset({"CSV", "TSV", "PIPE", "DELIMITED", "EXCEL", "JSON", "NDJSON", "XML"})
DELIMITED_TYPES = frozenset({"CSV", "TSV", "PIPE", "DELIMITED"})
_PART = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidDeclaration(ValueError):
    """The requestor's declared project / class / file type is malformed (§8.2)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Declared:
    """Requestor-declared parameters (§8.2); the signature's namespace."""

    project: str
    cls: str
    file_type: str

    @classmethod
    def normalize(klass, project: str, cls: str, file_type: str) -> "Declared":
        parts: list[str] = []
        for label, value in (("project", project), ("class", cls), ("file_type", file_type)):
            value = (value or "").strip().upper()
            if not _PART.match(value):
                raise InvalidDeclaration(f"{label} must match [A-Za-z0-9_-]+, got {value!r}")
            parts.append(value)
        if parts[2] not in FILE_TYPES:
            raise InvalidDeclaration(f"file_type must be one of {sorted(FILE_TYPES)}, got {parts[2]!r}")
        return klass(*parts)

    @property
    def prefix(self) -> str:
        return f"{self.project}|{self.cls}|{self.file_type}"


@dataclass(frozen=True)
class Signature:
    """A file's computed signature (§8.6)."""

    fingerprint: str
    physical_part: str
    xml_namespace: str | None
    observed: dict


@dataclass(frozen=True)
class SignatureHit:
    """Result of a successful registry lookup (§4, §8.11)."""

    signature_id: int
    fingerprint: str
    mapper_version_id: int
    xml_namespace: str | None


@dataclass(frozen=True)
class LandRow:
    """One landable row for ``raw.<target>`` (§8.9)."""

    target: str
    row_identity: str
    fields: dict[str, str | None]
    extras: dict | None = None


@dataclass(frozen=True)
class RejectRow:
    """One row that cannot land; becomes a ``reject_repository`` row (§8.9)."""

    target: str | None
    row_identity: str
    raw_payload: str
    reason_code: str
    detail: str | None = None


@dataclass
class Reject:
    """One rejected row (grain of ``reject_repository``; design §2.4a, §7)."""

    file_id: int
    row_identity: str
    raw_payload: str
    reason_code: str
    schema_id: str | None = None
    reason_detail: str | None = None
    status: RejectStatus = RejectStatus.REJECTED
    created_at: datetime = field(default_factory=_utcnow)
    reclaimed_at: datetime | None = None

    @property
    def key(self) -> tuple[int, str]:
        """The uniqueness key ``(file_id, row_identity)`` — the reclaim key."""
        return (self.file_id, self.row_identity)


@dataclass
class FileMetrics:
    """Per-file metrics reported to the caller (design §7)."""

    rows_total: int = 0
    rows_landed: int = 0
    rows_rejected: int = 0
    percent_parsed: float | None = None
    percent_loaded: float | None = None


@dataclass(frozen=True)
class FileResult:
    """What ``process()`` returns (§8.12). ``to_dict()`` is the agent-tool payload."""

    file_id: int
    status: FileStatus
    rows_total: int = 0
    rows_landed: int = 0
    rows_rejected: int = 0
    percent_parsed: float | None = None
    percent_loaded: float | None = None
    quarantine_reason: str | None = None
    proposal_summary: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data
```

- [ ] **Step 4: Rewrite `src/importer/mappers/base.py`**

```python
"""Mapper contract — one interface for all three tiers (design §8.5, §8.9).

A mapper turns a whole Document into LandRow / RejectRow items, possibly for
several target schemas. Declarative specs, spec + shapers, and plugin classes
all implement this interface; the pipeline cannot tell them apart.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import LandRow, RejectRow

if TYPE_CHECKING:
    from ..readers.base import Document


@dataclass(frozen=True)
class RunContext:
    file_id: int
    signature_id: int
    artifact_version_id: int
    request_context: dict = field(default_factory=dict)
    schemas: dict[str, list[str]] = field(default_factory=dict)  # target -> canonical columns


class Mapper(ABC):
    version: int = 1

    @property
    @abstractmethod
    def targets(self) -> tuple[str, ...]:
        """Target schema ids this mapper writes to."""

    @abstractmethod
    def extract(self, doc: "Document", ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        """Yield one item per extracted row; never raise for a single bad row."""
```

- [ ] **Step 5: Replace the stubs that referenced removed names**

`src/importer/loader.py` (temporary; Task 15 replaces it):

```python
"""Loader — stage with bisection, promote (design §8.10). STATUS: Task 15."""

from __future__ import annotations


def stage(*_args, **_kwargs):
    raise NotImplementedError("loader.stage lands in plan Task 15")
```

`src/importer/detector.py` (temporary; Task 8 replaces it):

```python
"""Detect & route (design §4, §8.6). STATUS: Task 8."""

from __future__ import annotations


def detect(*_args, **_kwargs):
    raise NotImplementedError("detector lands in plan Task 8")
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all green. `tests/test_models.py` gives 7 passed (the parametrized test counts as 4), and both original property suites still pass.

- [ ] **Step 7: Commit**

```bash
git add src/importer/models.py src/importer/mappers/base.py src/importer/loader.py src/importer/detector.py tests/test_models.py
git commit -m "$(cat <<'EOF'
Rework domain models and the Mapper contract for M2a

Adds declared parameters, signatures, land/reject rows and agent-ready
FileResult; re-entry transitions let quarantined/failed files be retried.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---
### Task 4: Reader foundations and the delimited reader

**Files:**
- Create: `src/importer/readers/__init__.py`, `src/importer/readers/base.py`, `src/importer/readers/sniff.py`, `src/importer/readers/delimited.py`
- Test: `tests/test_reader_delimited.py`

**Interfaces:**
- Consumes: `importer.models.DELIMITED_TYPES`.
- Produces:
  - Name helpers: `normalize_name(name) -> str`, `dedupe_names(names: list[str]) -> list[str]`, `normalize_header(raw: list) -> list[str]`.
  - `ReaderError(reason_code, detail)`, where `reason_code` is `corrupt` or `unparseable`.
  - `Record(row_identity, raw, values={}, reject_reason=None, detail=None)`.
  - `Table(name, raw_columns, columns, records, grid=None)`.
  - `TabularDocument(format, tables, encoding=None)`, with `.table(name)` (`"*"` = first table) and `.replace_table(table)`.
  - `XmlDocument(root, namespace, local_name, encoding=None, format="XML")`; `Document = TabularDocument | XmlDocument`.
  - `sniff.decode_text(data) -> (text, encoding)`, `sniff.check_declared(data, file_type)`.
  - `readers.parse_document(data: bytes, file_type: str) -> Document`, `readers.open_document(path, file_type) -> Document`.
  - `delimited.read_delimited(data, file_type) -> TabularDocument`.

- [ ] **Step 1: Write the failing tests**

`tests/test_reader_delimited.py`:

```python
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from importer.readers import parse_document
from importer.readers.base import ReaderError, normalize_header


def test_normalize_header_dedupes_and_fills():
    assert normalize_header(["URL ID", "url-id", None, ""]) == ["urlid", "urlid_2", "col3", "col4"]


def test_csv_header_records_and_identity():
    doc = parse_document(b"URL ID,New URL,urlType\r\na1,http://x,Payment\r\n\r\na2,http://y,SBC\r\n", "CSV")
    table = doc.table("*")
    assert table.columns == ["urlid", "newurl", "urltype"]
    assert table.raw_columns == ["URL ID", "New URL", "urlType"]
    assert [r.row_identity for r in table.records] == ["L2", "L4"]
    assert table.records[0].values == {"urlid": "a1", "newurl": "http://x", "urltype": "Payment"}
    assert doc.encoding == "utf-8"
    assert doc.format == "DELIMITED"


def test_field_count_mismatch_is_a_reader_reject():
    records = parse_document(b"a,b\n1,2\n3\n", "CSV").table("*").records
    bad = records[1]
    assert bad.reject_reason == "field_count_mismatch"
    assert bad.raw == "3"
    assert bad.row_identity == "L3"
    assert bad.values == {}


def test_quoted_multiline_field_keeps_physical_start_line():
    records = parse_document(b'a,b\n"x\ny",2\n3,4\n', "CSV").table("*").records
    assert [r.row_identity for r in records] == ["L2", "L4"]
    assert records[0].values["a"] == "x\ny"


def test_pipe_declared_fixes_delimiter():
    rec = parse_document(b"a|b\n1|2\n", "PIPE").table("*").records[0]
    assert rec.values == {"a": "1", "b": "2"}


def test_semicolon_is_sniffed():
    rec = parse_document(b"a;b\n1;2\n", "CSV").table("*").records[0]
    assert rec.values == {"a": "1", "b": "2"}


def test_cp1252_fallback():
    doc = parse_document("name\ncaf\xe9\n".encode("cp1252"), "CSV")
    assert doc.encoding == "cp1252"
    assert doc.table("*").records[0].values == {"name": "café"}


@pytest.mark.parametrize(
    "data,file_type",
    [(b"PK\x03\x04junk", "CSV"), (b"a,b\n", "XML"), (b"<x/>", "JSON"), (b"[1]", "NDJSON"), (b"a,b", "EXCEL")],
)
def test_declared_mismatch_is_unparseable(data, file_type):
    with pytest.raises(ReaderError) as err:
        parse_document(data, file_type)
    assert err.value.reason_code == "unparseable"


def test_empty_file_is_corrupt():
    with pytest.raises(ReaderError) as err:
        parse_document(b"", "CSV")
    assert err.value.reason_code == "corrupt"


def test_header_only_file_has_no_records():
    assert parse_document(b"a,b\n", "CSV").table("*").records == []


cell = st.text(alphabet="abcxyz019 ", max_size=5)


@given(st.lists(st.lists(cell, min_size=2, max_size=2), max_size=30))
def test_row_identities_are_unique(rows):
    body = "h1,h2\n" + "".join(",".join(r) + "\n" for r in rows)
    ids = [r.row_identity for r in parse_document(body.encode(), "CSV").table("*").records]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_reader_delimited.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.readers'`.

- [ ] **Step 3: Implement `readers/base.py`**

```python
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
```

- [ ] **Step 4: Implement `readers/sniff.py`**

```python
"""Declared-vs-actual check and text decoding (design §8.6)."""

from __future__ import annotations

from .base import ReaderError

ZIP = b"PK"
OLE2 = b"\xd0\xcf\x11\xe0"
UTF8_BOM = b"\xef\xbb\xbf"


def decode_text(data: bytes) -> tuple[str, str]:
    """UTF-8 (BOM-aware), falling back to cp1252; returns (text, encoding)."""
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError as exc:
        raise ReaderError("corrupt", f"text is neither UTF-8 nor cp1252: {exc}") from exc


def _lead(data: bytes) -> bytes:
    if data.startswith(UTF8_BOM):
        data = data[len(UTF8_BOM):]
    return data.lstrip()


def check_declared(data: bytes, file_type: str) -> None:
    if not data:
        raise ReaderError("corrupt", "file is empty")
    lead = _lead(data)
    if file_type == "XML":
        if not lead.startswith(b"<"):
            raise ReaderError("unparseable", "declared XML but content does not start with '<'")
    elif file_type == "EXCEL":
        if not (data.startswith(ZIP) or data.startswith(OLE2)):
            raise ReaderError("unparseable", "declared EXCEL but content is neither .xlsx (ZIP) nor .xls (OLE2)")
    elif file_type == "JSON":
        if lead[:1] not in (b"[", b"{"):
            raise ReaderError("unparseable", "declared JSON but content does not start with '[' or '{'")
    elif file_type == "NDJSON":
        first = next((line.strip() for line in lead.splitlines() if line.strip()), b"")
        if not first.startswith(b"{"):
            raise ReaderError("unparseable", "declared NDJSON but the first line is not a JSON object")
    else:  # delimited family
        if data.startswith(ZIP) or data.startswith(OLE2) or b"\x00" in data[:4096]:
            raise ReaderError("unparseable", f"declared {file_type} but content looks binary")
```

- [ ] **Step 5: Implement `readers/delimited.py`**

```python
"""Delimited text reader (CSV / TSV / pipe / semicolon) — design §8.6."""

from __future__ import annotations

import csv

from .base import Record, ReaderError, Table, TabularDocument, normalize_header
from .sniff import decode_text

csv.field_size_limit(2**31 - 1)

FIXED = {"TSV": "\t", "PIPE": "|"}
CANDIDATES = ",\t|;"


def _sniff(sample_line: str) -> str:
    try:
        return csv.Sniffer().sniff(sample_line, delimiters=CANDIDATES).delimiter
    except csv.Error:
        counts = {d: sample_line.count(d) for d in CANDIDATES}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_delimited(data: bytes, file_type: str) -> TabularDocument:
    text, encoding = decode_text(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    header_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if header_idx is None:
        raise ReaderError("corrupt", "no header row")
    delimiter = FIXED.get(file_type) or _sniff(lines[header_idx])
    reader = csv.reader((line + "\n" for line in lines), delimiter=delimiter)

    raw_header: list[str] | None = None
    columns: list[str] = []
    records: list[Record] = []
    prev_end = 0
    try:
        for fields in reader:
            start = prev_end + 1
            prev_end = reader.line_num
            if not fields or not any(f.strip() for f in fields):
                continue
            if raw_header is None:
                raw_header = fields
                columns = normalize_header(fields)
                continue
            raw = "\n".join(lines[start - 1:prev_end])
            row_identity = f"L{start}"
            if len(fields) != len(columns):
                records.append(Record(
                    row_identity, raw,
                    reject_reason="field_count_mismatch",
                    detail=f"expected {len(columns)} fields, got {len(fields)}",
                ))
            else:
                records.append(Record(row_identity, raw, values=dict(zip(columns, fields))))
    except csv.Error as exc:
        raise ReaderError("corrupt", f"malformed delimited text: {exc}") from exc
    if raw_header is None:
        raise ReaderError("corrupt", "no header row")
    return TabularDocument("DELIMITED", [Table("*", list(raw_header), columns, records)], encoding)
```

- [ ] **Step 6: Implement `readers/__init__.py`**

```python
"""Format readers (design §8.6). parse_document is the single entry point."""

from __future__ import annotations

from pathlib import Path

from ..models import DELIMITED_TYPES
from .base import Document, ReaderError
from .sniff import check_declared


def parse_document(data: bytes, file_type: str) -> Document:
    check_declared(data, file_type)
    if file_type in DELIMITED_TYPES:
        from .delimited import read_delimited
        return read_delimited(data, file_type)
    if file_type == "EXCEL":
        from .excel import read_excel
        return read_excel(data)
    if file_type == "JSON":
        from .json_reader import read_json
        return read_json(data)
    if file_type == "NDJSON":
        from .ndjson import read_ndjson
        return read_ndjson(data)
    if file_type == "XML":
        from .xml_reader import read_xml
        return read_xml(data)
    raise ReaderError("unparseable", f"no reader for file type {file_type!r}")


def open_document(path: str | Path, file_type: str) -> Document:
    return parse_document(Path(path).read_bytes(), file_type)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_reader_delimited.py -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add src/importer/readers tests/test_reader_delimited.py
git commit -m "$(cat <<'EOF'
Add reader foundations and the delimited reader

Declared-vs-actual checks quarantine mislabelled files; field-count
mismatches become row-level rejects with physical-line identities.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Excel reader (`.xlsx` and `.xls`)

**Files:**
- Create: `src/importer/readers/excel.py`
- Test: `tests/test_reader_excel.py`

**Interfaces:**
- Consumes: `readers.base` (Task 4).
- Produces: `excel.read_excel(data: bytes) -> TabularDocument` (format `EXCEL`, one `Table` per non-empty sheet, `name` = original sheet name, `grid` populated, `row_identity = "<sheet>!R<excel row>"`); `excel.cell_to_str(value) -> str | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_reader_excel.py`:

```python
from __future__ import annotations

import io

import openpyxl
import pytest

from importer.readers import parse_document
from importer.readers.base import ReaderError


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_multi_sheet_workbook():
    data = xlsx({
        "Issuer": [["Issuer ID", "State"], ["12345", "VT"]],
        "Plan": [["Plan ID", "Metal"], ["P1", "Gold"], [None, None], ["P2", 7.0]],
        "Empty": [[None]],
    })
    doc = parse_document(data, "EXCEL")
    assert doc.format == "EXCEL"
    assert [t.name for t in doc.tables] == ["Issuer", "Plan"]
    plan = doc.table("Plan")
    assert plan.columns == ["planid", "metal"]
    assert [r.row_identity for r in plan.records] == ["Plan!R2", "Plan!R4"]
    assert plan.records[1].values == {"planid": "P2", "metal": "7"}


def test_cells_beyond_header_are_kept_as_col_n():
    record = parse_document(xlsx({"S": [["a"], ["1", "extra"]]}), "EXCEL").table("S").records[0]
    assert record.values == {"a": "1", "col2": "extra"}


def test_grid_is_kept_for_shapers():
    table = parse_document(xlsx({"S": [["a", "b"], ["1", "2"]]}), "EXCEL").table("S")
    assert table.grid[0] == ["a", "b"]
    assert table.grid[1] == ["1", "2"]


def test_xls_legacy_format():
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Rates")
    ws.write(0, 0, "Plan ID")
    ws.write(0, 1, "Rate")
    ws.write(1, 0, "P1")
    ws.write(1, 1, 12.5)
    buf = io.BytesIO()
    wb.save(buf)
    table = parse_document(buf.getvalue(), "EXCEL").table("Rates")
    assert table.columns == ["planid", "rate"]
    assert table.records[0].values == {"planid": "P1", "rate": "12.5"}


def test_corrupt_xlsx():
    with pytest.raises(ReaderError) as err:
        parse_document(b"PK\x03\x04not a workbook", "EXCEL")
    assert err.value.reason_code == "corrupt"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_reader_excel.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.readers.excel'`.

- [ ] **Step 3: Implement `readers/excel.py`**

```python
"""Excel reader (.xlsx via openpyxl, .xls via xlrd) — design §8.6.

Row 1 is the header. Each non-empty sheet is one table; the full grid is kept
so shapers (anchor header, cross-tab unpivot) can reshape awkward layouts.
"""

from __future__ import annotations

import io
from datetime import date, datetime, time

from .base import Record, ReaderError, Table, TabularDocument, normalize_header


def cell_to_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _xlsx_sheets(data: bytes) -> list[tuple[str, list[list[str | None]]]]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ReaderError("corrupt", f"cannot open .xlsx: {exc}") from exc
    try:
        return [
            (ws.title, [[cell_to_str(v) for v in row] for row in ws.iter_rows(values_only=True)])
            for ws in wb.worksheets
        ]
    except Exception as exc:
        raise ReaderError("corrupt", f"cannot read .xlsx: {exc}") from exc
    finally:
        wb.close()


def _xls_sheets(data: bytes) -> list[tuple[str, list[list[str | None]]]]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise ReaderError("corrupt", f"cannot open .xls: {exc}") from exc
    sheets = []
    for sheet in book.sheets():
        rows = []
        for r in range(sheet.nrows):
            row: list[str | None] = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    row.append(None)
                elif cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).isoformat())
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    row.append("TRUE" if cell.value else "FALSE")
                else:
                    row.append(cell_to_str(cell.value))
            rows.append(row)
        sheets.append((sheet.name, rows))
    return sheets


def _is_empty(row: list[str | None]) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)


def read_excel(data: bytes) -> TabularDocument:
    sheets = _xlsx_sheets(data) if data[:2] == b"PK" else _xls_sheets(data)
    tables: list[Table] = []
    for name, grid in sheets:
        if all(_is_empty(row) for row in grid):
            continue
        header = list(grid[0]) if grid else []
        while header and header[-1] is None:
            header.pop()
        columns = normalize_header(header)
        width = len(columns)
        records: list[Record] = []
        for excel_row, row in enumerate(grid[1:], start=2):
            if _is_empty(row):
                continue
            values = {columns[i]: (row[i] if i < len(row) else None) for i in range(width)}
            for i in range(width, len(row)):
                if row[i] is not None:
                    values[f"col{i + 1}"] = row[i]
            raw = "\t".join("" if v is None else v for v in row)
            records.append(Record(f"{name}!R{excel_row}", raw, values=values))
        tables.append(Table(name, ["" if h is None else h for h in header], columns, records, grid=grid))
    if not tables:
        raise ReaderError("corrupt", "workbook has no non-empty sheets")
    return TabularDocument("EXCEL", tables, None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_reader_excel.py -v`
Expected: 5 passed. If `xlwt` couldn't be installed on this Python, `test_xls_legacy_format` is skipped; that's acceptable.

- [ ] **Step 5: Commit**

```bash
git add src/importer/readers/excel.py tests/test_reader_excel.py
git commit -m "$(cat <<'EOF'
Add the Excel reader for .xlsx and .xls workbooks

Each non-empty sheet becomes a table with sheet!Rn identities; the raw grid
is kept so shapers can handle report-style layouts.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: JSON and NDJSON readers

**Files:**
- Create: `src/importer/readers/json_reader.py`, `src/importer/readers/ndjson.py`
- Test: `tests/test_reader_json.py`

**Interfaces:**
- Consumes: `readers.base`, `readers.sniff.decode_text` (Task 4).
- Produces:
  - `json_reader.read_json(data) -> TabularDocument` (format `JSON`, one table `"*"`, `row_identity = "[i]"`, reject code `invalid_json_record`).
  - `ndjson.read_ndjson(data) -> TabularDocument` (format `NDJSON`, `row_identity = "L<line>"`, reject code `invalid_json_line`).
  - Helpers: `json_reader.flatten(obj) -> dict`, `json_reader.scalar_to_str(v) -> str | None`, `json_reader.build_table(items, bad_reason) -> Table`.

- [ ] **Step 1: Write the failing tests**

`tests/test_reader_json.py`:

```python
from __future__ import annotations

import pytest

from importer.readers import parse_document
from importer.readers.base import ReaderError


def test_json_array_flattens_nested_keys():
    data = b'[{"id": 1, "plan": {"name": "Gold", "tier": 2}, "tags": ["a"], "ok": true, "x": null}]'
    table = parse_document(data, "JSON").table("*")
    assert table.columns == ["id", "planname", "plantier", "tags", "ok", "x"]
    record = table.records[0]
    assert record.row_identity == "[0]"
    assert record.values == {
        "id": "1", "planname": "Gold", "plantier": "2", "tags": '["a"]', "ok": "true", "x": None,
    }


def test_json_non_object_element_is_rejected():
    records = parse_document(b'[{"a": 1}, 5]', "JSON").table("*").records
    assert records[1].reject_reason == "invalid_json_record"
    assert records[1].row_identity == "[1]"


def test_json_single_object_is_one_record():
    records = parse_document(b'{"a": "x"}', "JSON").table("*").records
    assert len(records) == 1 and records[0].values == {"a": "x"}


def test_json_corrupt():
    with pytest.raises(ReaderError) as err:
        parse_document(b'[{"a":', "JSON")
    assert err.value.reason_code == "corrupt"


def test_ndjson_bad_line_is_a_row_reject():
    data = b'{"id": 1, "name": "a"}\n{"id": 2, "name": "b"}\nnot json\n\n{"id": 3}\n'
    doc = parse_document(data, "NDJSON")
    table = doc.table("*")
    assert doc.format == "NDJSON"
    assert [r.row_identity for r in table.records] == ["L1", "L2", "L3", "L5"]
    bad = table.records[2]
    assert bad.reject_reason == "invalid_json_line"
    assert bad.raw == "not json"
    assert table.columns == ["id", "name"]
    assert table.records[3].values == {"id": "3"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_reader_json.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.readers.json_reader'`.

- [ ] **Step 3: Implement `readers/json_reader.py`**

```python
"""JSON reader: a flat array of objects (or one object) — design §8.6."""

from __future__ import annotations

import json

from .base import Record, ReaderError, Table, TabularDocument, normalize_name
from .sniff import decode_text


def scalar_to_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def flatten(obj: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in obj.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            out.update(flatten(value, name))
        else:
            out[name] = value
    return out


def build_table(items: list[tuple[str, str, object]], bad_reason: str) -> Table:
    """items are (row_identity, raw text, parsed value or the parse exception)."""
    columns: list[str] = []
    seen: set[str] = set()
    records: list[Record] = []
    for row_identity, raw, obj in items:
        if not isinstance(obj, dict):
            detail = str(obj) if isinstance(obj, Exception) else f"expected a JSON object, got {type(obj).__name__}"
            records.append(Record(row_identity, raw, reject_reason=bad_reason, detail=detail))
            continue
        values: dict[str, str | None] = {}
        for key, value in flatten(obj).items():
            column = normalize_name(key) or "col"
            values[column] = scalar_to_str(value)
            if column not in seen:
                seen.add(column)
                columns.append(column)
        records.append(Record(row_identity, raw, values=values))
    return Table("*", list(columns), columns, records)


def read_json(data: bytes) -> TabularDocument:
    text, encoding = decode_text(data)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReaderError("corrupt", f"invalid JSON: {exc}") from exc
    elements = parsed if isinstance(parsed, list) else [parsed]
    items = [(f"[{i}]", json.dumps(obj, ensure_ascii=False), obj) for i, obj in enumerate(elements)]
    return TabularDocument("JSON", [build_table(items, "invalid_json_record")], encoding)
```

- [ ] **Step 4: Implement `readers/ndjson.py`**

```python
"""NDJSON reader: one JSON object per line — design §8.6."""

from __future__ import annotations

import json

from .base import TabularDocument
from .json_reader import build_table
from .sniff import decode_text


def read_ndjson(data: bytes) -> TabularDocument:
    text, encoding = decode_text(data)
    items: list[tuple[str, str, object]] = []
    for line_no, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            obj: object = json.loads(line)
        except json.JSONDecodeError as exc:
            obj = exc
        items.append((f"L{line_no}", line, obj))
    return TabularDocument("NDJSON", [build_table(items, "invalid_json_line")], encoding)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_reader_json.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/readers/json_reader.py src/importer/readers/ndjson.py tests/test_reader_json.py
git commit -m "$(cat <<'EOF'
Add JSON and NDJSON readers

Nested keys are dot-flattened and normalized; a bad NDJSON line is a row
reject, never a file failure.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: XML reader and the formulary fixture

**Files:**
- Create: `src/importer/readers/xml_reader.py`, `tests/fixtures/formulary_dc.xml`
- Test: `tests/test_reader_xml.py`

**Interfaces:**
- Consumes: `readers.base` (Task 4).
- Produces: `xml_reader.read_xml(data) -> XmlDocument`; `xml_reader.local(el) -> str`; `xml_reader.element_identity(el, root) -> str` (namespace-free path; `"/"` for the root; ordinal fallback when longer than 256); `xml_reader.ordinal_identity(el, root) -> str`; `xml_reader.MAX_IDENTITY = 256`.

- [ ] **Step 1: Create the fixture `tests/fixtures/formulary_dc.xml`**

This is invented data shaped like the CMS prescription drug template. It produces 5 cost-share rows, 2 tier rows and 3 drug rows.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<prescriptionDrugTemplateVO xmlns="http://vo.ffe.cms.hhs.gov">
  <header>
    <templateVersion>v13.0</templateVersion>
    <issuerId cellLocation="B2"><cellValue>12345</cellValue></issuerId>
    <statePostalCode cellLocation="B3"><cellValue>DC</cellValue></statePostalCode>
  </header>
  <formularyList>
    <formularyVO>
      <formularyID><cellValue>DCF001</cellValue></formularyID>
      <drugListID><cellValue>1</cellValue></drugListID>
      <numberTiers><cellValue>2</cellValue></numberTiers>
      <formularyCostSharingTypeList>
        <formularyCostSharingTypeVO>
          <drugTierLevel><cellValue>1</cellValue></drugTierLevel>
          <oneMonthOutNetworkRetailOfferedIndicator><cellValue>Yes</cellValue></oneMonthOutNetworkRetailOfferedIndicator>
          <threeMonthInNetworkMailOfferedIndicator><cellValue>Yes</cellValue></threeMonthInNetworkMailOfferedIndicator>
          <threeMonthOutNetworkMailOfferedIndicator><cellValue>No</cellValue></threeMonthOutNetworkMailOfferedIndicator>
          <costSharingTypeList>
            <costSharingTypeVO>
              <networkCostType>In Network</networkCostType>
              <drugPrescriptionPeriodType>1 Month</drugPrescriptionPeriodType>
              <coPayment><cellValue>$10.00</cellValue></coPayment>
              <coInsurance><cellValue>0%</cellValue></coInsurance>
            </costSharingTypeVO>
            <costSharingTypeVO>
              <networkCostType>Out of Network</networkCostType>
              <drugPrescriptionPeriodType>1 Month</drugPrescriptionPeriodType>
              <coPayment><cellValue>$25.00</cellValue></coPayment>
              <coInsurance><cellValue>0%</cellValue></coInsurance>
            </costSharingTypeVO>
          </costSharingTypeList>
        </formularyCostSharingTypeVO>
        <formularyCostSharingTypeVO>
          <drugTierLevel><cellValue>2</cellValue></drugTierLevel>
          <oneMonthOutNetworkRetailOfferedIndicator><cellValue>Yes</cellValue></oneMonthOutNetworkRetailOfferedIndicator>
          <threeMonthInNetworkMailOfferedIndicator><cellValue>No</cellValue></threeMonthInNetworkMailOfferedIndicator>
          <threeMonthOutNetworkMailOfferedIndicator><cellValue>No</cellValue></threeMonthOutNetworkMailOfferedIndicator>
          <costSharingTypeList>
            <costSharingTypeVO>
              <networkCostType>In Network</networkCostType>
              <drugPrescriptionPeriodType>1 Month</drugPrescriptionPeriodType>
              <coPayment><cellValue>$40.00</cellValue></coPayment>
              <coInsurance><cellValue>0%</cellValue></coInsurance>
            </costSharingTypeVO>
            <costSharingTypeVO>
              <networkCostType>Out of Network</networkCostType>
              <drugPrescriptionPeriodType>1 Month</drugPrescriptionPeriodType>
              <coPayment><cellValue>No Charge</cellValue></coPayment>
              <coInsurance><cellValue>40%</cellValue></coInsurance>
            </costSharingTypeVO>
          </costSharingTypeList>
        </formularyCostSharingTypeVO>
      </formularyCostSharingTypeList>
    </formularyVO>
    <formularyVO>
      <formularyID><cellValue>DCF002</cellValue></formularyID>
      <drugListID><cellValue>1</cellValue></drugListID>
      <numberTiers><cellValue>1</cellValue></numberTiers>
      <formularyCostSharingTypeList>
        <formularyCostSharingTypeVO>
          <drugTierLevel><cellValue>1</cellValue></drugTierLevel>
          <oneMonthOutNetworkRetailOfferedIndicator><cellValue>No</cellValue></oneMonthOutNetworkRetailOfferedIndicator>
          <threeMonthInNetworkMailOfferedIndicator><cellValue>No</cellValue></threeMonthInNetworkMailOfferedIndicator>
          <threeMonthOutNetworkMailOfferedIndicator><cellValue>No</cellValue></threeMonthOutNetworkMailOfferedIndicator>
          <costSharingTypeList>
            <costSharingTypeVO>
              <networkCostType>In Network</networkCostType>
              <drugPrescriptionPeriodType>3 Month</drugPrescriptionPeriodType>
              <coPayment><cellValue>$5.00</cellValue></coPayment>
              <coInsurance><cellValue>0%</cellValue></coInsurance>
            </costSharingTypeVO>
          </costSharingTypeList>
        </formularyCostSharingTypeVO>
      </formularyCostSharingTypeList>
    </formularyVO>
  </formularyList>
  <drugList>
    <drugListVO>
      <drugListID><cellValue>1</cellValue></drugListID>
      <drugTierLevelList>
        <drugTierLevelVO>
          <drugTierLevel><cellValue>1</cellValue></drugTierLevel>
          <drugTierType><cellValue>Generic</cellValue></drugTierType>
        </drugTierLevelVO>
        <drugTierLevelVO>
          <drugTierLevel><cellValue>2</cellValue></drugTierLevel>
          <drugTierType><cellValue>Preferred Brand</cellValue></drugTierType>
        </drugTierLevelVO>
      </drugTierLevelList>
      <drugVOList>
        <drugVO>
          <rXCUI><cellValue>197361</cellValue></rXCUI>
          <tier><cellValue>1</cellValue></tier>
          <authorizationRequired><cellValue>No</cellValue></authorizationRequired>
          <stepTherapyRequired><cellValue>No</cellValue></stepTherapyRequired>
        </drugVO>
        <drugVO>
          <rXCUI><cellValue>314076</cellValue></rXCUI>
          <tier><cellValue>1</cellValue></tier>
          <authorizationRequired><cellValue>No</cellValue></authorizationRequired>
          <stepTherapyRequired><cellValue>No</cellValue></stepTherapyRequired>
        </drugVO>
        <drugVO>
          <rXCUI><cellValue>860975</cellValue></rXCUI>
          <tier><cellValue>2</cellValue></tier>
          <authorizationRequired><cellValue>Yes</cellValue></authorizationRequired>
          <stepTherapyRequired><cellValue>No</cellValue></stepTherapyRequired>
          <quantityLimit><cellValue>Yes</cellValue></quantityLimit>
        </drugVO>
      </drugVOList>
    </drugListVO>
  </drugList>
</prescriptionDrugTemplateVO>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_reader_xml.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from importer.readers import parse_document
from importer.readers.base import ReaderError
from importer.readers.xml_reader import element_identity

NS = "http://vo.ffe.cms.hhs.gov"
FIXTURE = Path(__file__).parent / "fixtures" / "formulary_dc.xml"


def test_reads_root_and_namespace():
    doc = parse_document(FIXTURE.read_bytes(), "XML")
    assert doc.local_name == "prescriptionDrugTemplateVO"
    assert doc.namespace == NS
    assert doc.encoding == "UTF-8"
    assert doc.format == "XML"


def test_element_identity_is_namespace_free_with_indexes_only_when_repeated():
    doc = parse_document(FIXTURE.read_bytes(), "XML")
    el = doc.root.xpath(
        "/x:prescriptionDrugTemplateVO/x:formularyList/x:formularyVO[1]/x:formularyCostSharingTypeList"
        "/x:formularyCostSharingTypeVO[2]/x:costSharingTypeList/x:costSharingTypeVO[1]",
        namespaces={"x": NS},
    )[0]
    assert element_identity(el, doc.root) == (
        "/formularyList/formularyVO[1]/formularyCostSharingTypeList"
        "/formularyCostSharingTypeVO[2]/costSharingTypeList/costSharingTypeVO[1]"
    )
    assert element_identity(doc.root, doc.root) == "/"


def test_identity_falls_back_to_ordinal_when_too_long():
    tag = "averyveryverylongelementname"
    xml = "<r>" + "".join(f"<{tag}>" for _ in range(12)) + "".join(f"</{tag}>" for _ in range(12)) + "</r>"
    doc = parse_document(xml.encode(), "XML")
    leaf = doc.root
    for _ in range(12):
        leaf = leaf[0]
    assert element_identity(leaf, doc.root) == ".".join(["1"] * 12)


def test_corrupt_xml():
    with pytest.raises(ReaderError) as err:
        parse_document(b"<a><b></a>", "XML")
    assert err.value.reason_code == "corrupt"


def test_external_entities_are_not_resolved():
    data = b'<!DOCTYPE r [<!ENTITY e SYSTEM "file:///c:/windows/win.ini">]><r>&e;</r>'
    doc = parse_document(data, "XML")
    assert doc.root.text is None
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_reader_xml.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.readers.xml_reader'`.

- [ ] **Step 4: Implement `readers/xml_reader.py`**

```python
"""XML reader and element identities (design §8.6, §8.10).

External entities and network access are disabled (XXE-safe). Identities are
namespace-free paths from the root, with [n] only where a name repeats among
siblings; paths over 256 characters fall back to an ordinal path like 1.3.2.
"""

from __future__ import annotations

from lxml import etree

from .base import ReaderError, XmlDocument

MAX_IDENTITY = 256


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def read_xml(data: bytes) -> XmlDocument:
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError as exc:
        raise ReaderError("corrupt", f"invalid XML: {exc}") from exc
    qname = etree.QName(root)
    encoding = root.getroottree().docinfo.encoding
    return XmlDocument(root=root, namespace=qname.namespace, local_name=qname.localname, encoding=encoding)


def local(el) -> str:
    return etree.QName(el).localname


def element_identity(el, root) -> str:
    if el is root:
        return "/"
    steps: list[str] = []
    node = el
    while node is not None and node is not root:
        name = local(node)
        before = sum(1 for _ in node.itersiblings(node.tag, preceding=True))
        after = next(node.itersiblings(node.tag), None) is not None
        steps.append(name if before == 0 and not after else f"{name}[{before + 1}]")
        node = node.getparent()
    path = "/" + "/".join(reversed(steps))
    return path if len(path) <= MAX_IDENTITY else ordinal_identity(el, root)


def ordinal_identity(el, root) -> str:
    steps: list[str] = []
    node = el
    while node is not None and node is not root:
        before = sum(1 for s in node.itersiblings(preceding=True) if isinstance(s.tag, str))
        steps.append(str(before + 1))
        node = node.getparent()
    return ".".join(reversed(steps))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_reader_xml.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/readers/xml_reader.py tests/fixtures/formulary_dc.xml tests/test_reader_xml.py
git commit -m "$(cat <<'EOF'
Add the XXE-safe XML reader with namespace-free row identities

Includes a synthetic CMS formulary fixture used by later mapping and
pipeline tests.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Fingerprint and detector

**Files:**
- Rewrite: `src/importer/fingerprint.py`, `src/importer/detector.py`
- Test: `tests/test_fingerprint.py`

**Interfaces:**
- Consumes: `Declared`, `Signature`, `SignatureHit` (Task 3); readers (Tasks 4–7).
- Produces: `fingerprint.physical_part(doc) -> tuple[str, dict]`; `fingerprint.fingerprint(doc, declared) -> Signature`; `detector.detect(doc, declared, registry) -> tuple[Signature, SignatureHit | None]`, where `registry.lookup(fingerprint: str, xml_namespace: str | None) -> SignatureHit | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_fingerprint.py`:

```python
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
from hypothesis import assume, given
from hypothesis import strategies as st

from importer.detector import detect
from importer.fingerprint import fingerprint
from importer.models import Declared, SignatureHit
from importer.readers import parse_document

FIXTURE = Path(__file__).parent / "fixtures" / "formulary_dc.xml"
CSV_A = b"URL ID,New URL,URL Type\nx,y,z\n"


def _sig(data: bytes, file_type: str, project: str = "ME"):
    return fingerprint(parse_document(data, file_type), Declared.normalize(project, "PC", file_type))


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_csv_signature_shape():
    sig = _sig(CSV_A, "CSV")
    assert sig.fingerprint.startswith("ME|PC|CSV|h:")
    assert len(sig.physical_part) == 18
    assert sig.xml_namespace is None
    assert sig.observed == {"columns": ["urlid", "newurl", "urltype"]}


def test_xml_signature_is_readable():
    sig = _sig(FIXTURE.read_bytes(), "XML", project="DC")
    assert sig.fingerprint == "DC|PC|XML|prescriptionDrugTemplateVO"
    assert sig.xml_namespace == "http://vo.ffe.cms.hhs.gov"


def test_header_case_and_punctuation_do_not_matter():
    assert _sig(b"url_id,NEW-URL,url type\n", "CSV").physical_part == _sig(CSV_A, "CSV").physical_part


def test_different_header_changes_signature():
    assert _sig(b"urlid,url,urltype\n", "CSV").fingerprint != _sig(CSV_A, "CSV").fingerprint


def test_json_key_order_does_not_matter():
    assert _sig(b'[{"a": 1, "b": 2}]', "JSON") == _sig(b'[{"b": 1, "a": 2}]', "JSON")


def test_excel_sheet_order_does_not_matter():
    one = _xlsx({"Issuer": [["id"]], "Plan": [["plan id"]]})
    two = _xlsx({"Plan": [["plan id"]], "Issuer": [["id"]]})
    assert _sig(one, "EXCEL").physical_part == _sig(two, "EXCEL").physical_part


class FakeRegistry:
    def __init__(self, namespace: str | None):
        self.namespace = namespace

    def lookup(self, fp: str, ns: str | None):
        return SignatureHit(1, fp, 10, ns) if ns == self.namespace else None


def test_detect_hit_and_namespace_mismatch():
    doc = parse_document(FIXTURE.read_bytes(), "XML")
    declared = Declared.normalize("DC", "PC", "XML")
    sig, hit = detect(doc, declared, FakeRegistry("http://vo.ffe.cms.hhs.gov"))
    assert hit is not None and hit.mapper_version_id == 10
    _, miss = detect(doc, declared, FakeRegistry("urn:other"))
    assert miss is None


names = st.lists(st.text(alphabet="abcdefgh", min_size=1, max_size=6), min_size=1, max_size=8, unique=True)


@given(names)
def test_signature_is_deterministic_and_normalized(cols):
    plain = (",".join(cols) + "\n").encode()
    noisy = (",".join(c.upper() + "_" for c in cols) + "\n").encode()
    assert _sig(plain, "CSV") == _sig(noisy, "CSV")


@given(names, names)
def test_different_ordered_headers_differ(a, b):
    assume(a != b)
    assert _sig((",".join(a) + "\n").encode(), "CSV").fingerprint != _sig((",".join(b) + "\n").encode(), "CSV").fingerprint
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_fingerprint.py -v`
Expected: FAIL, with `ImportError` / `NotImplementedError` from the stubs.

- [ ] **Step 3: Rewrite `src/importer/fingerprint.py`**

```python
"""Deterministic file signature (design §8.6).

signature = PROJECT|CLASS|FILE_TYPE|<physical part>, matched exactly.
XML: the root element's local name (namespace checked separately).
Tabular: h:<16 hex> over normalized structure (header / sheets+headers / keys).
"""

from __future__ import annotations

import hashlib

from .models import Declared, Signature
from .readers.base import Document, XmlDocument, normalize_name


def _h(parts: list[str]) -> str:
    return "h:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def physical_part(doc: Document) -> tuple[str, dict]:
    if isinstance(doc, XmlDocument):
        return doc.local_name, {"root": doc.local_name, "namespace": doc.namespace}
    if doc.format == "EXCEL":
        sheets = sorted((normalize_name(t.name), t.columns) for t in doc.tables)
        part = _h([f"{name}:{','.join(cols)}" for name, cols in sheets])
        return part, {"sheets": {t.name: t.columns for t in doc.tables}}
    table = doc.tables[0]
    if doc.format in ("JSON", "NDJSON"):
        keys = sorted(set(table.columns))
        return _h(keys), {"keys": keys}
    return _h(table.columns), {"columns": table.columns}


def fingerprint(doc: Document, declared: Declared) -> Signature:
    part, observed = physical_part(doc)
    namespace = doc.namespace if isinstance(doc, XmlDocument) else None
    return Signature(
        fingerprint=f"{declared.prefix}|{part}",
        physical_part=part,
        xml_namespace=namespace,
        observed=observed,
    )
```

- [ ] **Step 4: Rewrite `src/importer/detector.py`**

```python
"""Detect & route, model-free (design §4, §8.6).

Computes the signature and looks it up. The signature is returned even on a
miss, because the pipeline profiles the file against it (§8.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fingerprint import fingerprint
from .models import Declared, Signature, SignatureHit
from .readers.base import Document

if TYPE_CHECKING:
    from .repositories import SignatureRegistry


def detect(doc: Document, declared: Declared, registry: "SignatureRegistry") -> tuple[Signature, SignatureHit | None]:
    sig = fingerprint(doc, declared)
    return sig, registry.lookup(sig.fingerprint, sig.xml_namespace)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_fingerprint.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/fingerprint.py src/importer/detector.py tests/test_fingerprint.py
git commit -m "$(cat <<'EOF'
Implement signatures and model-free detection

Closes the fingerprint-composition open item in code: declared parameters
plus a structural physical part, matched exactly.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---
### Task 9: Repository protocols and the in-memory store

**Files:**
- Create: `src/importer/repositories.py`, `src/importer/memory_store.py`
- Modify: `src/importer/reject_store.py` (add `count_open_for_file`)
- Delete: `src/importer/registry.py` (superseded)
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: models (Task 3), `metrics.monotonic_update`, `InMemoryRejectStore`.
- Produces (exact names used by every later task):
  - Row dataclasses:
    - `LedgerRow(file_id, idempotency_key, source, original_filename, project, cls, file_type, status, signature_id, request_context, source_path, encoding, attempts, rows_total, rows_landed, rows_rejected, percent_parsed, percent_loaded, last_error)`, with the `.declared` property.
    - `RegistryRow(signature_id, fingerprint, physical_part, xml_namespace, status, mapper_version_id, observed_schema, proposal, approved_by)`.
    - `ArtifactRow(version_id, artifact_type, artifact_key, version_no, definition, is_active, created_by, content_sha256)`.
    - `StagedRow(file_id, row_identity, signature_id, artifact_version_id, fields, extras_json)`.
  - Errors: `IllegalTransition(ValueError)`, `ConcurrentTransition(RuntimeError)`, `NotFound(KeyError)`, `RowDataError(Exception)`.
  - Protocols, method by method:

    | Protocol | Methods |
    |---|---|
    | `LedgerRepository` | `create_or_get(*, idempotency_key, source, original_filename, source_path, declared, request_context) -> (LedgerRow, bool)`, `get(file_id)`, `transition(file_id, src, dst)`, `begin_reentry(file_id) -> LedgerRow`, `set_signature(file_id, signature_id)`, `set_encoding(file_id, encoding)`, `set_error(file_id, message)`, `record_metrics(file_id, *, rows_total, rows_landed, rows_rejected, percent_parsed, percent_loaded)` |
    | `SignatureRegistry` | `lookup(fingerprint, xml_namespace) -> SignatureHit | None`, `get(signature_id) -> RegistryRow`, `find_active_by_physical_part(physical_part, xml_namespace) -> list[RegistryRow]`, `propose(sig, proposal) -> int`, `register(sig, mapper_version_id, approved_by) -> int` |
    | `ArtifactRepository` | `put_version(artifact_type, artifact_key, definition, *, created_by, activate=True, content_sha256=None) -> ArtifactRow`, `get(version_id)`, `active_schema(schema_id) -> ArtifactRow | None`, `active_schemas() -> list[ArtifactRow]` |
    | `RawZone` | `ensure_tables(schema_id, columns)`, `clear_stage(file_id, target)`, `stage_batch(target, rows)` (raises `RowDataError` for data errors), `promote(file_id, target) -> int`, `count_landed(file_id, target) -> int` |
    | `AuditLog` | `quarantine(file_id, reason_code, detail, signature_id)`, `decision(file_id, action_type, observed, proposed, applied, approval)` |
    | `Tx` | attributes `ledger, registry, artifacts, rejects, raw, audit` |
    | `Store` | `transaction() -> ContextManager[Tx]` |

  - `RejectStore` gains `count_open_for_file(file_id) -> int`.
  - `memory_store.MemoryStore(fault: RawFault | None = None)`, with `.transaction()` (deep-copies the state; a commit swaps it in, an exception discards it), the `.state` property (read-only inspection for tests) and the mutable `.fault` attribute. `memory_store.RawFault(bad_identities: set[str] = set(), infra_on_stage=False, infra_on_promote=False)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_memory_store.py`:

```python
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from importer.memory_store import MemoryStore
from importer.models import ArtifactType, Declared, FileStatus, Reject, Signature, SignatureStatus
from importer.repositories import ConcurrentTransition, IllegalTransition, StagedRow

DECLARED = Declared.normalize("DC", "PC", "CSV")


def _create(tx, key="k1"):
    return tx.ledger.create_or_get(
        idempotency_key=key, source="test", original_filename="f.csv",
        source_path="/tmp/f.csv", declared=DECLARED, request_context={"run_id": "1"},
    )


def _sig(fp="DC|PC|CSV|h:1", part="h:1", ns=None):
    return Signature(fp, part, ns, {"columns": ["a"]})


def test_create_or_get_is_idempotent():
    store = MemoryStore()
    with store.transaction() as tx:
        first, created = _create(tx)
        again, created_again = _create(tx)
    assert created is True and created_again is False
    assert first.file_id == again.file_id
    assert first.status is FileStatus.DISCOVERED
    assert first.declared == DECLARED


def test_transition_guards():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        with pytest.raises(IllegalTransition):
            tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.COMPLETED)
        with pytest.raises(ConcurrentTransition):
            tx.ledger.transition(row.file_id, FileStatus.DETECTED, FileStatus.LANDED)
        tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.DETECTED)
        assert tx.ledger.get(row.file_id).status is FileStatus.DETECTED


def test_exception_rolls_back_the_transaction():
    store = MemoryStore()
    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            _create(tx)
            raise RuntimeError("boom")
    with store.transaction() as tx:
        _, created = _create(tx)
    assert created is True


def test_percent_loaded_floor():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=8, rows_rejected=2, percent_parsed=100.0, percent_loaded=80.0)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=5, rows_rejected=5, percent_parsed=100.0, percent_loaded=50.0)
        assert tx.ledger.get(row.file_id).percent_loaded == 80.0


def test_begin_reentry_increments_attempts():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.FAILED)
        tx.ledger.set_error(row.file_id, "boom")
        again = tx.ledger.begin_reentry(row.file_id)
    assert again.status is FileStatus.DISCOVERED
    assert again.attempts == 2
    assert again.last_error is None


def test_begin_reentry_requires_quarantined_or_failed():
    store = MemoryStore()
    with store.transaction() as tx:
        row, _ = _create(tx)
        with pytest.raises(IllegalTransition):
            tx.ledger.begin_reentry(row.file_id)


def test_registry_register_lookup_and_namespace():
    store = MemoryStore()
    with store.transaction() as tx:
        art = tx.artifacts.put_version(ArtifactType.MAPPER, "m", {"kind": "tabular"}, created_by="t")
        sid = tx.registry.register(_sig(ns="urn:a"), art.version_id, "tester")
        hit = tx.registry.lookup("DC|PC|CSV|h:1", "urn:a")
        assert hit is not None and hit.signature_id == sid and hit.mapper_version_id == art.version_id
        assert tx.registry.lookup("DC|PC|CSV|h:1", "urn:b") is None
        assert [r.signature_id for r in tx.registry.find_active_by_physical_part("h:1", "urn:a")] == [sid]


def test_propose_never_overrides_an_active_signature():
    store = MemoryStore()
    with store.transaction() as tx:
        art = tx.artifacts.put_version(ArtifactType.MAPPER, "m", {"kind": "tabular"}, created_by="t")
        sid = tx.registry.register(_sig(), art.version_id, "tester")
        assert tx.registry.propose(_sig(), [{"kind": "schema"}]) == sid
        assert tx.registry.get(sid).status is SignatureStatus.ACTIVE
        new_id = tx.registry.propose(_sig("ME|PC|CSV|h:1"), [{"kind": "schema"}])
        assert tx.registry.get(new_id).status is SignatureStatus.PROPOSED
        assert tx.registry.lookup("ME|PC|CSV|h:1", None) is None


def test_artifact_versions_increment_and_single_active():
    store = MemoryStore()
    with store.transaction() as tx:
        v1 = tx.artifacts.put_version(ArtifactType.SCHEMA, "s", {"columns": ["a"]}, created_by="t")
        v2 = tx.artifacts.put_version(ArtifactType.SCHEMA, "s", {"columns": ["a", "b"]}, created_by="t")
        assert (v1.version_no, v2.version_no) == (1, 2)
        assert tx.artifacts.active_schema("s").version_id == v2.version_id
        assert not tx.artifacts.get(v1.version_id).is_active
        assert [a.artifact_key for a in tx.artifacts.active_schemas()] == ["s"]


def test_reject_count_open_for_file():
    store = MemoryStore()
    with store.transaction() as tx:
        tx.rejects.add(Reject(file_id=1, row_identity="L2", raw_payload="x", reason_code="r", schema_id="s"))
        tx.rejects.add(Reject(file_id=1, row_identity="L3", raw_payload="x", reason_code="r", schema_id="s"))
        tx.rejects.add(Reject(file_id=2, row_identity="L2", raw_payload="x", reason_code="r", schema_id="s"))
        tx.rejects.mark_reclaimed(1, "L3")
        assert tx.rejects.count_open_for_file(1) == 1


@given(st.lists(st.text(alphabet="abc123", min_size=1, max_size=4), unique=True, max_size=25))
def test_promote_is_idempotent(identities):
    store = MemoryStore()
    rows = [StagedRow(1, rid, 1, 1, {"a": rid}, None) for rid in identities]
    with store.transaction() as tx:
        tx.raw.ensure_tables("t", ["a"])
        tx.raw.clear_stage(1, "t")
        tx.raw.stage_batch("t", rows)
        first = tx.raw.promote(1, "t")
        tx.raw.stage_batch("t", rows)
        second = tx.raw.promote(1, "t")
        assert first == len(identities)
        assert second == 0
        assert tx.raw.count_landed(1, "t") == len(identities)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_memory_store.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.memory_store'`.

- [ ] **Step 3: Add `count_open_for_file` to `src/importer/reject_store.py`**

Add this method to the `RejectStore` Protocol, after `all_rejects`:

```python
    def count_open_for_file(self, file_id: int) -> int:
        """Rows for ``file_id`` still in ``rejected`` status (feeds rows_rejected, §8.12)."""
        ...
```

Add this method to `InMemoryRejectStore`, after `all_rejects`:

```python
    def count_open_for_file(self, file_id: int) -> int:
        return sum(
            1 for r in self._rows.values() if r.file_id == file_id and r.status is RejectStatus.REJECTED
        )
```

- [ ] **Step 4: Create `src/importer/repositories.py`**

```python
"""Repository contracts (design §8.11): Protocols the pipeline codes against.

Two implementations: memory_store (unit tests, real rollback) and mssql_store
(SQL Server via DataEngine/SQLAlchemy Core).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from .models import (
    ArtifactType,
    Declared,
    FileStatus,
    Signature,
    SignatureHit,
    SignatureStatus,
)
from .reject_store import RejectStore


class IllegalTransition(ValueError):
    """The requested status change is not in LEGAL_TRANSITIONS."""


class ConcurrentTransition(RuntimeError):
    """The file is no longer in the expected source status."""


class NotFound(KeyError):
    """No row with that id."""


class RowDataError(Exception):
    """A staging insert failed because of row data (SQLSTATE class 22/23)."""


@dataclass
class LedgerRow:
    file_id: int
    idempotency_key: str
    source: str
    original_filename: str
    project: str
    cls: str
    file_type: str
    status: FileStatus = FileStatus.DISCOVERED
    signature_id: int | None = None
    request_context: dict | None = None
    source_path: str | None = None
    encoding: str | None = None
    attempts: int = 1
    rows_total: int | None = None
    rows_landed: int | None = None
    rows_rejected: int | None = None
    percent_parsed: float | None = None
    percent_loaded: float | None = None
    last_error: str | None = None

    @property
    def declared(self) -> Declared:
        return Declared(self.project, self.cls, self.file_type)


@dataclass
class RegistryRow:
    signature_id: int
    fingerprint: str
    physical_part: str
    xml_namespace: str | None
    status: SignatureStatus
    mapper_version_id: int | None = None
    observed_schema: dict | None = None
    proposal: list | None = None
    approved_by: str | None = None


@dataclass
class ArtifactRow:
    version_id: int
    artifact_type: ArtifactType
    artifact_key: str
    version_no: int
    definition: dict
    is_active: bool
    created_by: str | None = None
    content_sha256: str | None = None


@dataclass(frozen=True)
class StagedRow:
    file_id: int
    row_identity: str
    signature_id: int
    artifact_version_id: int
    fields: dict
    extras_json: str | None


class LedgerRepository(Protocol):
    def create_or_get(
        self, *, idempotency_key: str, source: str, original_filename: str,
        source_path: str | None, declared: Declared, request_context: dict | None,
    ) -> tuple[LedgerRow, bool]: ...
    def get(self, file_id: int) -> LedgerRow: ...
    def transition(self, file_id: int, src: FileStatus, dst: FileStatus) -> None: ...
    def begin_reentry(self, file_id: int) -> LedgerRow: ...
    def set_signature(self, file_id: int, signature_id: int | None) -> None: ...
    def set_encoding(self, file_id: int, encoding: str | None) -> None: ...
    def set_error(self, file_id: int, message: str | None) -> None: ...
    def record_metrics(
        self, file_id: int, *, rows_total: int, rows_landed: int, rows_rejected: int,
        percent_parsed: float, percent_loaded: float,
    ) -> None: ...


class SignatureRegistry(Protocol):
    def lookup(self, fingerprint: str, xml_namespace: str | None) -> SignatureHit | None: ...
    def get(self, signature_id: int) -> RegistryRow: ...
    def find_active_by_physical_part(self, physical_part: str, xml_namespace: str | None) -> list[RegistryRow]: ...
    def propose(self, sig: Signature, proposal: list[dict]) -> int: ...
    def register(self, sig: Signature, mapper_version_id: int, approved_by: str) -> int: ...


class ArtifactRepository(Protocol):
    def put_version(
        self, artifact_type: ArtifactType, artifact_key: str, definition: dict, *,
        created_by: str | None, activate: bool = True, content_sha256: str | None = None,
    ) -> ArtifactRow: ...
    def get(self, version_id: int) -> ArtifactRow: ...
    def active_schema(self, schema_id: str) -> ArtifactRow | None: ...
    def active_schemas(self) -> list[ArtifactRow]: ...


class RawZone(Protocol):
    def ensure_tables(self, schema_id: str, columns: list[str]) -> None: ...
    def clear_stage(self, file_id: int, target: str) -> None: ...
    def stage_batch(self, target: str, rows: list[StagedRow]) -> None: ...
    def promote(self, file_id: int, target: str) -> int: ...
    def count_landed(self, file_id: int, target: str) -> int: ...


class AuditLog(Protocol):
    def quarantine(self, file_id: int, reason_code: str, detail: str | None, signature_id: int | None) -> None: ...
    def decision(
        self, file_id: int | None, action_type: str, observed: object, proposed: object,
        applied: bool, approval: str | None,
    ) -> None: ...


class Tx(Protocol):
    ledger: LedgerRepository
    registry: SignatureRegistry
    artifacts: ArtifactRepository
    rejects: RejectStore
    raw: RawZone
    audit: AuditLog


class Store(Protocol):
    def transaction(self) -> AbstractContextManager[Tx]: ...
```

- [ ] **Step 5: Create `src/importer/memory_store.py`**

```python
"""In-memory Store with real rollback semantics (unit tests, design §8.11).

Each transaction works on a deep copy of the state and swaps it in only on a
clean exit, so a test can prove that a failed step left nothing behind.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from .metrics import monotonic_update
from .models import (
    ArtifactType,
    Declared,
    FileStatus,
    Signature,
    SignatureHit,
    SignatureStatus,
    can_transition,
)
from .reject_store import InMemoryRejectStore
from .repositories import (
    ArtifactRow,
    ConcurrentTransition,
    IllegalTransition,
    LedgerRow,
    NotFound,
    RegistryRow,
    RowDataError,
    StagedRow,
)


@dataclass
class RawFault:
    """Test hook: make staging/promote fail on purpose."""

    bad_identities: set[str] = field(default_factory=set)
    infra_on_stage: bool = False
    infra_on_promote: bool = False


@dataclass
class _State:
    ledger: dict[int, LedgerRow] = field(default_factory=dict)
    registry: dict[int, RegistryRow] = field(default_factory=dict)
    artifacts: dict[int, ArtifactRow] = field(default_factory=dict)
    rejects: InMemoryRejectStore = field(default_factory=InMemoryRejectStore)
    columns: dict[str, list[str]] = field(default_factory=dict)
    stage: dict[str, list[StagedRow]] = field(default_factory=dict)
    raw: dict[str, dict[tuple[int, str], StagedRow]] = field(default_factory=dict)
    quarantine: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: {"file": 0, "sig": 0, "art": 0})

    def next_id(self, kind: str) -> int:
        self.counters[kind] += 1
        return self.counters[kind]


class MemoryLedger:
    def __init__(self, state: _State):
        self.s = state

    def _row(self, file_id: int) -> LedgerRow:
        try:
            return self.s.ledger[file_id]
        except KeyError:
            raise NotFound(file_id) from None

    def create_or_get(self, *, idempotency_key, source, original_filename, source_path, declared: Declared, request_context):
        for row in self.s.ledger.values():
            if row.idempotency_key == idempotency_key:
                return replace(row), False
        file_id = self.s.next_id("file")
        row = LedgerRow(
            file_id=file_id, idempotency_key=idempotency_key, source=source,
            original_filename=original_filename, project=declared.project, cls=declared.cls,
            file_type=declared.file_type, request_context=request_context, source_path=source_path,
        )
        self.s.ledger[file_id] = row
        return replace(row), True

    def get(self, file_id):
        return replace(self._row(file_id))

    def transition(self, file_id, src, dst):
        if not can_transition(src, dst):
            raise IllegalTransition(f"{src.value} -> {dst.value}")
        row = self._row(file_id)
        if row.status is not src:
            raise ConcurrentTransition(f"file {file_id} is {row.status.value}, not {src.value}")
        row.status = dst

    def begin_reentry(self, file_id):
        row = self._row(file_id)
        if row.status not in (FileStatus.QUARANTINED, FileStatus.FAILED):
            raise IllegalTransition(f"re-entry needs quarantined or failed, file {file_id} is {row.status.value}")
        self.transition(file_id, row.status, FileStatus.DISCOVERED)
        row.attempts += 1
        row.last_error = None
        return replace(row)

    def set_signature(self, file_id, signature_id):
        self._row(file_id).signature_id = signature_id

    def set_encoding(self, file_id, encoding):
        self._row(file_id).encoding = encoding

    def set_error(self, file_id, message):
        self._row(file_id).last_error = message

    def record_metrics(self, file_id, *, rows_total, rows_landed, rows_rejected, percent_parsed, percent_loaded):
        row = self._row(file_id)
        row.rows_total = rows_total
        row.rows_landed = rows_landed
        row.rows_rejected = rows_rejected
        row.percent_parsed = percent_parsed
        row.percent_loaded = monotonic_update(row.percent_loaded, percent_loaded)


class MemoryRegistry:
    def __init__(self, state: _State):
        self.s = state

    def _by_fp(self, fingerprint: str) -> RegistryRow | None:
        return next((r for r in self.s.registry.values() if r.fingerprint == fingerprint), None)

    def lookup(self, fingerprint, xml_namespace):
        row = self._by_fp(fingerprint)
        if row is None or row.status is not SignatureStatus.ACTIVE or row.mapper_version_id is None:
            return None
        if row.xml_namespace != xml_namespace:
            return None
        return SignatureHit(row.signature_id, row.fingerprint, row.mapper_version_id, row.xml_namespace)

    def get(self, signature_id):
        try:
            return replace(self.s.registry[signature_id])
        except KeyError:
            raise NotFound(signature_id) from None

    def find_active_by_physical_part(self, physical_part, xml_namespace):
        return [
            replace(r) for r in self.s.registry.values()
            if r.physical_part == physical_part and r.status is SignatureStatus.ACTIVE and r.xml_namespace == xml_namespace
        ]

    def propose(self, sig: Signature, proposal):
        existing = self._by_fp(sig.fingerprint)
        if existing is not None:
            if existing.status is SignatureStatus.PROPOSED:
                existing.observed_schema = sig.observed
                existing.proposal = proposal
            return existing.signature_id
        sid = self.s.next_id("sig")
        self.s.registry[sid] = RegistryRow(
            signature_id=sid, fingerprint=sig.fingerprint, physical_part=sig.physical_part,
            xml_namespace=sig.xml_namespace, status=SignatureStatus.PROPOSED,
            observed_schema=sig.observed, proposal=proposal,
        )
        return sid

    def register(self, sig: Signature, mapper_version_id, approved_by):
        existing = self._by_fp(sig.fingerprint)
        if existing is not None:
            existing.status = SignatureStatus.ACTIVE
            existing.mapper_version_id = mapper_version_id
            existing.physical_part = sig.physical_part
            existing.xml_namespace = sig.xml_namespace
            existing.observed_schema = sig.observed
            existing.approved_by = approved_by
            return existing.signature_id
        sid = self.s.next_id("sig")
        self.s.registry[sid] = RegistryRow(
            signature_id=sid, fingerprint=sig.fingerprint, physical_part=sig.physical_part,
            xml_namespace=sig.xml_namespace, status=SignatureStatus.ACTIVE,
            mapper_version_id=mapper_version_id, observed_schema=sig.observed, approved_by=approved_by,
        )
        return sid


class MemoryArtifacts:
    def __init__(self, state: _State):
        self.s = state

    def put_version(self, artifact_type, artifact_key, definition, *, created_by, activate=True, content_sha256=None):
        same = [a for a in self.s.artifacts.values() if a.artifact_type is artifact_type and a.artifact_key == artifact_key]
        version_no = 1 + max((a.version_no for a in same), default=0)
        if activate:
            for a in same:
                a.is_active = False
        vid = self.s.next_id("art")
        row = ArtifactRow(
            version_id=vid, artifact_type=artifact_type, artifact_key=artifact_key, version_no=version_no,
            definition=copy.deepcopy(definition), is_active=activate, created_by=created_by,
            content_sha256=content_sha256,
        )
        self.s.artifacts[vid] = row
        return replace(row)

    def get(self, version_id):
        try:
            return replace(self.s.artifacts[version_id])
        except KeyError:
            raise NotFound(version_id) from None

    def active_schema(self, schema_id):
        return next(
            (replace(a) for a in self.s.artifacts.values()
             if a.artifact_type is ArtifactType.SCHEMA and a.artifact_key == schema_id and a.is_active),
            None,
        )

    def active_schemas(self):
        return [replace(a) for a in self.s.artifacts.values() if a.artifact_type is ArtifactType.SCHEMA and a.is_active]


class MemoryRawZone:
    def __init__(self, state: _State, fault: RawFault | None):
        self.s = state
        self.fault = fault

    def ensure_tables(self, schema_id, columns):
        existing = self.s.columns.get(schema_id, [])
        self.s.columns[schema_id] = existing + [c for c in columns if c not in existing]

    def clear_stage(self, file_id, target):
        self.s.stage[target] = [r for r in self.s.stage.get(target, []) if r.file_id != file_id]

    def stage_batch(self, target, rows):
        if target not in self.s.columns:
            raise RuntimeError(f"stage.{target} does not exist")
        if self.fault and self.fault.infra_on_stage:
            raise RuntimeError("simulated connection failure while staging")
        if self.fault and any(r.row_identity in self.fault.bad_identities for r in rows):
            raise RowDataError("simulated SQLSTATE 22001: string data, right truncation")
        self.s.stage.setdefault(target, []).extend(rows)

    def promote(self, file_id, target):
        if self.fault and self.fault.infra_on_promote:
            raise RuntimeError("simulated connection failure while promoting")
        landed = self.s.raw.setdefault(target, {})
        inserted = 0
        for row in self.s.stage.get(target, []):
            if row.file_id != file_id:
                continue
            key = (file_id, row.row_identity)
            if key not in landed:
                landed[key] = row
                inserted += 1
        self.clear_stage(file_id, target)
        return inserted

    def count_landed(self, file_id, target):
        return sum(1 for (fid, _) in self.s.raw.get(target, {}) if fid == file_id)


class MemoryAudit:
    def __init__(self, state: _State):
        self.s = state

    def quarantine(self, file_id, reason_code, detail, signature_id):
        self.s.quarantine.append(
            {"file_id": file_id, "reason_code": reason_code, "detail": detail, "signature_id": signature_id}
        )

    def decision(self, file_id, action_type, observed, proposed, applied, approval):
        self.s.decisions.append({
            "file_id": file_id, "action_type": action_type, "observed": observed,
            "proposed": proposed, "applied": applied, "approval": approval,
        })


class MemoryTx:
    def __init__(self, state: _State, fault: RawFault | None):
        self.ledger = MemoryLedger(state)
        self.registry = MemoryRegistry(state)
        self.artifacts = MemoryArtifacts(state)
        self.rejects = state.rejects
        self.raw = MemoryRawZone(state, fault)
        self.audit = MemoryAudit(state)


class MemoryStore:
    def __init__(self, fault: RawFault | None = None):
        self._state = _State()
        self.fault = fault

    @property
    def state(self) -> _State:
        return self._state

    @contextmanager
    def transaction(self) -> Iterator[MemoryTx]:
        work = copy.deepcopy(self._state)
        yield MemoryTx(work, self.fault)
        self._state = work
```

- [ ] **Step 6: Delete the superseded registry stub**

```bash
git rm src/importer/registry.py
```

Then confirm nothing still imports it:
Run: `.venv/Scripts/python -c "import importer.detector, importer.memory_store; print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all green, including `tests/test_memory_store.py` and both original property suites.

- [ ] **Step 8: Commit**

```bash
git add src/importer/repositories.py src/importer/memory_store.py src/importer/reject_store.py tests/test_memory_store.py
git commit -m "$(cat <<'EOF'
Add repository protocols and an in-memory store with real rollback

Every pipeline step will run in its own transaction; the in-memory store
discards a failed step's writes so tests can prove nothing partial remains.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---
### Task 10: Declarative spec — parsing, validation, tabular extraction

**Files:**
- Create: `src/importer/mappers/spec.py`, `tests/support.py`, `tests/fixtures/url_template_a.csv`, `tests/fixtures/url_template_b.csv`
- Test: `tests/test_spec_tabular.py`

**Interfaces:**
- Consumes: `Mapper`, `RunContext` (Task 3); readers (Tasks 4–6); `xml_reader.element_identity` (Task 7).
- Produces:
  - `SpecError(ValueError)`.
  - Spec types: `Unpivot(over, name_column, value_column)`, `Extraction(target, records, fields, auto, unpivot, unmapped)`, `Spec(kind, extractions, namespaces, value_child, shapers)`.
  - `parse_spec(defn: dict, *, known_schemas: set[str] | None = None, known_shapers: set[str] | None = None) -> Spec`.
  - `DeclarativeMapper(spec: Spec, version: int = 1)` (a `Mapper`): `.targets`, `.extract(doc, ctx)`.
  - `tests/support.py`: `FIXTURES`, `fixture(name) -> Path`, `load_json(name)`, `URL_SCHEMA`, `URL_SPEC_A`, `URL_SPEC_B`, `EVENT_SCHEMA`, `EVENT_SPEC`, `ctx(schemas, request_context=None, file_id=7) -> RunContext`.

- [ ] **Step 1: Create the URL fixtures**

`tests/fixtures/url_template_a.csv`:
```
URL ID,New URL,URL Type
12345VT0010001,https://example.org/sbc/1,SBC
12345VT0010002,https://example.org/pay,Payment
```

`tests/fixtures/url_template_b.csv`:
```
urlid,url,urltype
54321VT0020001,https://example.net/sbc/9,SBC
54321VT0020002,https://example.net/pay,Payment
```

- [ ] **Step 2: Create `tests/support.py`**

```python
"""Shared test helpers and synthetic mapping definitions."""

from __future__ import annotations

import json
from pathlib import Path

from importer.mappers.base import RunContext

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Path:
    return FIXTURES / name


def load_json(name: str):
    return json.loads(fixture(name).read_text(encoding="utf-8"))


def ctx(schemas: dict[str, list[str]], request_context: dict | None = None, file_id: int = 7) -> RunContext:
    return RunContext(
        file_id=file_id, signature_id=1, artifact_version_id=1,
        request_context=request_context or {}, schemas=schemas,
    )


URL_SCHEMA = {"url_template": ["urlid", "newurl", "urltype", "issuerid", "run_id", "file_id"]}
URL_SPEC_A = {
    "kind": "tabular",
    "extractions": [{
        "target": "url_template", "records": "*", "auto": True,
        "fields": {"file_id": "$file_id", "run_id": "$context.run_id"},
    }],
}
URL_SPEC_B = {
    "kind": "tabular",
    "extractions": [{
        "target": "url_template", "records": "*", "auto": True,
        "fields": {"newurl": "url", "file_id": "$file_id", "run_id": "$context.run_id"},
    }],
}
EVENT_SCHEMA = {"event": ["id", "name", "amount"]}
EVENT_SPEC = {"kind": "tabular", "extractions": [{"target": "event", "records": "*", "auto": True}]}
```

- [ ] **Step 3: Write the failing tests**

`tests/test_spec_tabular.py`:

```python
from __future__ import annotations

import pytest

from importer.mappers.spec import DeclarativeMapper, SpecError, parse_spec
from importer.models import LandRow, RejectRow
from importer.readers import parse_document
from tests.support import URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, ctx, fixture


def _rows(spec, data, file_type, schemas, request_context=None):
    mapper = DeclarativeMapper(parse_spec(spec))
    return list(mapper.extract(parse_document(data, file_type), ctx(schemas, request_context)))


def test_variant_a_auto_maps_and_fills_special_sources():
    rows = _rows(URL_SPEC_A, fixture("url_template_a.csv").read_bytes(), "CSV", URL_SCHEMA, {"run_id": "42"})
    assert rows[0] == LandRow(
        "url_template", "L2",
        {"urlid": "12345VT0010001", "newurl": "https://example.org/sbc/1", "urltype": "SBC", "file_id": "7", "run_id": "42"},
        None,
    )
    assert len(rows) == 2


def test_variant_b_maps_url_into_newurl():
    rows = _rows(URL_SPEC_B, fixture("url_template_b.csv").read_bytes(), "CSV", URL_SCHEMA, {"run_id": "42"})
    assert rows[0].fields["newurl"] == "https://example.net/sbc/9"
    assert "url" not in rows[0].fields
    assert rows[0].extras is None


def test_unmapped_columns_go_to_extras_or_are_ignored():
    data = b"urlid,newurl,urltype,notes\n1,u,SBC,hello\n"
    assert _rows(URL_SPEC_A, data, "CSV", URL_SCHEMA)[0].extras == {"notes": "hello"}
    spec = {"kind": "tabular", "extractions": [{"target": "url_template", "records": "*", "auto": True, "unmapped": "ignore"}]}
    assert _rows(spec, data, "CSV", URL_SCHEMA)[0].extras is None


def test_reader_rejects_are_forwarded_with_the_target():
    rows = _rows(URL_SPEC_A, b"urlid,newurl,urltype\n1,u,SBC\nshort\n", "CSV", URL_SCHEMA)
    assert rows[1] == RejectRow("url_template", "L3", "short", "field_count_mismatch", "expected 3 fields, got 1")


def test_auto_matches_mixed_case_schema_columns():
    spec = {"kind": "tabular", "extractions": [{"target": "plans", "records": "*", "auto": True}]}
    rows = _rows(spec, b"Plan ID,Metal\nP1,Gold\n", "CSV", {"plans": ["PlanID", "Metal"]})
    assert rows[0].fields == {"PlanID": "P1", "Metal": "Gold"}


def test_unpivot_all_unused_columns():
    spec = {"kind": "tabular", "extractions": [{
        "target": "plan_cost", "records": "*", "fields": {"planid": "planid"},
        "unpivot": {"over": "*", "name_column": "component_name", "value_column": "component_value"},
    }]}
    rows = _rows(spec, b"planid,deductible,moop\nP1,500,8000\n", "CSV", {"plan_cost": ["planid", "component_name", "component_value"]})
    assert [r.row_identity for r in rows] == ["L2#deductible", "L2#moop"]
    assert rows[1].fields == {"planid": "P1", "component_name": "moop", "component_value": "8000"}


def test_excel_sheet_records_by_name():
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws.append(["Plan ID", "Metal"])
    ws.append(["P1", "Gold"])
    buf = io.BytesIO()
    wb.save(buf)
    spec = {"kind": "tabular", "extractions": [{"target": "plans", "records": "Plan", "auto": True}]}
    rows = _rows(spec, buf.getvalue(), "EXCEL", {"plans": ["planid", "metal"]})
    assert rows == [LandRow("plans", "Plan!R2", {"planid": "P1", "metal": "Gold"}, None)]


@pytest.mark.parametrize(
    "spec,message",
    [
        ({"extractions": []}, "kind"),
        ({"kind": "tabular", "extractions": []}, "non-empty"),
        ({"kind": "tabular", "extractions": [{"target": "bad-name", "records": "*", "auto": True}]}, "target"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*"}]}, "maps nothing"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*", "fields": {"a": "$parent(t)"}}]}, "only valid in xml"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*", "auto": True, "unmapped": "drop"}]}, "unmapped"),
        ({"kind": "tabular", "extractions": [{"target": "t", "records": "*", "fields": {"a": "$nope"}}]}, "unknown special"),
    ],
)
def test_invalid_specs(spec, message):
    with pytest.raises(SpecError, match=message):
        parse_spec(spec)


def test_target_must_have_an_active_schema():
    with pytest.raises(SpecError, match="no active schema"):
        parse_spec(URL_SPEC_A, known_schemas={"other"})


def test_tabular_spec_rejects_xml_document():
    mapper = DeclarativeMapper(parse_spec(URL_SPEC_A))
    with pytest.raises(SpecError, match="tabular spec"):
        list(mapper.extract(parse_document(b"<r/>", "XML"), ctx(URL_SCHEMA)))
```

- [ ] **Step 4: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_spec_tabular.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.mappers.spec'`.

- [ ] **Step 5: Implement `src/importer/mappers/spec.py`**

```python
"""Declarative mapping spec (design §8.9): parsing, validation and extraction.

A spec is data (stored in artifact_versions.definition). Stable code runs it;
the agent may author specs but never code. kind "tabular" covers delimited,
Excel and JSON documents; kind "xml" uses XPath relative to each record.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from lxml import etree

from ..models import LandRow, RejectRow
from ..readers.base import Record, TabularDocument, XmlDocument, normalize_name
from ..readers.xml_reader import element_identity
from .base import Mapper, RunContext

IDENT = re.compile(r"^[A-Za-z0-9_]+$")
PARENT = re.compile(r"^\$parent\(([A-Za-z0-9_]+)\)$")
CONTEXT = re.compile(r"^\$context\.([A-Za-z0-9_]+)$")
KINDS = ("xml", "tabular")


class SpecError(ValueError):
    """The spec is invalid or does not fit the document (configuration error → file failed)."""


@dataclass(frozen=True)
class Unpivot:
    over: str | tuple[str, ...]
    name_column: str
    value_column: str


@dataclass(frozen=True)
class Extraction:
    target: str
    records: str
    fields: dict
    auto: bool = False
    unpivot: Unpivot | None = None
    unmapped: str = "extras"


@dataclass(frozen=True)
class Spec:
    kind: str
    extractions: tuple[Extraction, ...]
    namespaces: dict = field(default_factory=dict)
    value_child: str | None = None
    shapers: tuple[dict, ...] = ()


def _is_special(src: str) -> bool:
    return src.startswith("$")


def _check_special(src: str, kind: str) -> None:
    if src in ("$file_id", "$row_identity") or CONTEXT.match(src):
        return
    if PARENT.match(src):
        if kind != "xml":
            raise SpecError(f"{src}: $parent(...) is only valid in xml specs")
        return
    raise SpecError(f"unknown special source {src!r}")


def parse_spec(defn: dict, *, known_schemas: set[str] | None = None, known_shapers: set[str] | None = None) -> Spec:
    if not isinstance(defn, dict):
        raise SpecError("spec must be a JSON object")
    kind = defn.get("kind")
    if kind not in KINDS:
        raise SpecError(f"kind must be one of {KINDS}, got {kind!r}")
    namespaces = defn.get("namespaces", {})
    if not isinstance(namespaces, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in namespaces.items()):
        raise SpecError("namespaces must map prefix -> URI strings")
    value_child = defn.get("value_child")
    if value_child is not None and not isinstance(value_child, str):
        raise SpecError("value_child must be a string")
    shapers = defn.get("shapers", [])
    if not isinstance(shapers, list):
        raise SpecError("shapers must be a list")
    for shaper in shapers:
        if not isinstance(shaper, dict) or not isinstance(shaper.get("name"), str) or not isinstance(shaper.get("options", {}), dict):
            raise SpecError("each shaper needs a string 'name' and an object 'options'")
        if kind != "tabular":
            raise SpecError("shapers are only valid in tabular specs")
        if known_shapers is not None and shaper["name"] not in known_shapers:
            raise SpecError(f"unknown shaper {shaper['name']!r}")
    raw = defn.get("extractions")
    if not isinstance(raw, list) or not raw:
        raise SpecError("extractions must be a non-empty list")
    extractions = tuple(_parse_extraction(e, kind, known_schemas) for e in raw)
    targets = {e.target for e in extractions}
    for extraction in extractions:
        for src in extraction.fields.values():
            match = PARENT.match(src)
            if match and match.group(1) not in targets:
                raise SpecError(f"{src}: no extraction targets {match.group(1)!r}")
    spec = Spec(kind, extractions, dict(namespaces), value_child, tuple(shapers))
    if kind == "xml":
        _compile_check(spec)
    return spec


def _parse_extraction(e: object, kind: str, known_schemas: set[str] | None) -> Extraction:
    if not isinstance(e, dict):
        raise SpecError("each extraction must be an object")
    target = e.get("target")
    if not isinstance(target, str) or not IDENT.match(target):
        raise SpecError(f"target must match [A-Za-z0-9_]+, got {target!r}")
    if known_schemas is not None and target not in known_schemas:
        raise SpecError(f"target {target!r} has no active schema definition")
    records = e.get("records")
    if not isinstance(records, str) or not records:
        raise SpecError(f"{target}: records must be a non-empty string")
    fields = e.get("fields", {})
    if not isinstance(fields, dict):
        raise SpecError(f"{target}: fields must be an object")
    for column, src in fields.items():
        if not isinstance(src, str) or not src:
            raise SpecError(f"{target}.{column}: source must be a non-empty string")
        if column == "*":
            if kind != "xml":
                raise SpecError(f"{target}: '*' (children-as-fields) is only valid in xml specs")
        elif not IDENT.match(column):
            raise SpecError(f"{target}: column {column!r} must match [A-Za-z0-9_]+")
        if _is_special(src):
            _check_special(src, kind)
    auto = e.get("auto", False)
    if not isinstance(auto, bool):
        raise SpecError(f"{target}: auto must be true or false")
    if auto and kind != "tabular":
        raise SpecError(f"{target}: auto is only valid in tabular specs")
    unpivot = None
    if "unpivot" in e:
        u = e["unpivot"]
        if not isinstance(u, dict) or set(u) != {"over", "name_column", "value_column"}:
            raise SpecError(f"{target}: unpivot needs exactly over, name_column, value_column")
        over = u["over"]
        if kind == "xml" and not isinstance(over, str):
            raise SpecError(f"{target}: xml unpivot.over must be an XPath string")
        if kind == "tabular" and not (over == "*" or (isinstance(over, list) and all(isinstance(c, str) for c in over))):
            raise SpecError(f"{target}: tabular unpivot.over must be '*' or a list of columns")
        for key in ("name_column", "value_column"):
            if not isinstance(u[key], str) or not IDENT.match(u[key]):
                raise SpecError(f"{target}: unpivot.{key} must match [A-Za-z0-9_]+")
        unpivot = Unpivot(over if isinstance(over, str) else tuple(over), u["name_column"], u["value_column"])
    unmapped = e.get("unmapped", "extras")
    if unmapped not in ("extras", "ignore"):
        raise SpecError(f"{target}: unmapped must be 'extras' or 'ignore'")
    if not fields and not auto and unpivot is None:
        raise SpecError(f"{target}: extraction maps nothing")
    return Extraction(target, records, dict(fields), auto, unpivot, unmapped)


def _compile_check(spec: Spec) -> None:
    exprs: list[str] = []
    if spec.value_child:
        exprs.append(spec.value_child)
    for extraction in spec.extractions:
        exprs.append(extraction.records)
        exprs.extend(src for src in extraction.fields.values() if not _is_special(src))
        if extraction.unpivot:
            exprs.append(extraction.unpivot.over)
    for expr in exprs:
        try:
            etree.XPath(expr, namespaces=spec.namespaces)
        except etree.XPathError as exc:
            raise SpecError(f"invalid XPath {expr!r}: {exc}") from exc


def _put(fields: dict, extras: dict, schema_set: set[str], column: str, value: object) -> None:
    (fields if column in schema_set else extras)[column] = value


class DeclarativeMapper(Mapper):
    """Runs a parsed Spec (tier 1, and tier 2 when the spec lists shapers)."""

    def __init__(self, spec: Spec, version: int = 1):
        self.spec = spec
        self.version = version
        self._compiled: dict[str, etree.XPath] = {}

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.target for e in self.spec.extractions))

    def _xpath(self, expr: str) -> etree.XPath:
        if expr not in self._compiled:
            self._compiled[expr] = etree.XPath(expr, namespaces=self.spec.namespaces)
        return self._compiled[expr]

    def extract(self, doc, ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        if self.spec.kind == "xml":
            if not isinstance(doc, XmlDocument):
                raise SpecError("an xml spec cannot map a tabular document")
            yield from self._extract_xml(doc, ctx)
            return
        if not isinstance(doc, TabularDocument):
            raise SpecError("a tabular spec cannot map an XML document")
        if self.spec.shapers:
            from ..shapers import apply_shapers
            doc = apply_shapers(doc, list(self.spec.shapers))
        yield from self._extract_tabular(doc, ctx)

    def _special(self, src: str, ctx: RunContext, row_identity: str, el=None, root=None, record_paths=None) -> str | None:
        if src == "$file_id":
            return str(ctx.file_id)
        if src == "$row_identity":
            return row_identity
        match = CONTEXT.match(src)
        if match:
            value = ctx.request_context.get(match.group(1))
            return None if value is None else str(value)
        match = PARENT.match(src)
        if match and el is not None:
            paths = (record_paths or {}).get(match.group(1), set())
            for ancestor in el.iterancestors():
                path = element_identity(ancestor, root)
                if path in paths:
                    return path
            return None
        raise SpecError(f"unsupported source {src!r}")

    # ---- tabular -------------------------------------------------------------

    def _extract_tabular(self, doc: TabularDocument, ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        rejects_emitted: set[str] = set()
        for extraction in self.spec.extractions:
            try:
                table = doc.table(extraction.records)
            except KeyError as exc:
                raise SpecError(str(exc)) from exc
            schema = ctx.schemas.get(extraction.target, [])
            schema_set = set(schema)
            mapping: dict[str, str] = {}
            if extraction.auto:
                by_norm = {normalize_name(c): c for c in schema}
                for column in table.columns:
                    norm = normalize_name(column)
                    if norm in by_norm:
                        mapping[by_norm[norm]] = column
            mapping.update(extraction.fields)
            emit_rejects = table.name not in rejects_emitted
            rejects_emitted.add(table.name)
            for record in table.records:
                if record.reject_reason:
                    if emit_rejects:
                        yield RejectRow(extraction.target, record.row_identity, record.raw, record.reject_reason, record.detail)
                    continue
                try:
                    rows = list(self._tabular_rows(extraction, record, mapping, schema_set, ctx))
                except SpecError:
                    raise
                except Exception as exc:
                    yield RejectRow(extraction.target, record.row_identity, record.raw, "mapping_error", f"{type(exc).__name__}: {exc}")
                    continue
                yield from rows

    def _tabular_rows(self, extraction: Extraction, record: Record, mapping: dict[str, str], schema_set: set[str], ctx: RunContext) -> Iterator[LandRow]:
        fields: dict = {}
        extras: dict = {}
        used: set[str] = set()
        for column, src in mapping.items():
            if _is_special(src):
                value = self._special(src, ctx, record.row_identity)
            else:
                value = record.values.get(src)
                used.add(src)
            _put(fields, extras, schema_set, column, value)
        leftovers = {c: v for c, v in record.values.items() if c not in used}
        if extraction.unpivot:
            u = extraction.unpivot
            over = list(leftovers) if u.over == "*" else list(u.over)
            rest = {c: v for c, v in leftovers.items() if c not in over}
            for column in over:
                if column not in record.values:
                    continue
                row_fields, row_extras = dict(fields), dict(extras)
                if extraction.unmapped == "extras":
                    row_extras.update(rest)
                _put(row_fields, row_extras, schema_set, u.name_column, column)
                _put(row_fields, row_extras, schema_set, u.value_column, record.values[column])
                yield LandRow(extraction.target, f"{record.row_identity}#{column}", row_fields, row_extras or None)
            return
        if extraction.unmapped == "extras":
            extras.update(leftovers)
        yield LandRow(extraction.target, record.row_identity, fields, extras or None)

    # ---- xml (Task 11) -----------------------------------------------------------

    def _extract_xml(self, doc: XmlDocument, ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        raise NotImplementedError("XML extraction lands in plan Task 11")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_spec_tabular.py -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add src/importer/mappers/spec.py tests/support.py tests/fixtures/url_template_a.csv tests/fixtures/url_template_b.csv tests/test_spec_tabular.py
git commit -m "$(cat <<'EOF'
Add the declarative mapping spec with tabular extraction

Specs are validated data; auto-mapping, special sources, unpivot and the
_extras overflow keep every source value without code per schema.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Declarative spec — XML extraction

**Files:**
- Modify: `src/importer/mappers/spec.py` (replace `_extract_xml`; add the XML helpers)
- Create: `tests/fixtures/formulary_spec.json`, `tests/fixtures/formulary_schemas.json`
- Test: `tests/test_spec_xml.py`

**Interfaces:**
- Consumes: Task 10's `DeclarativeMapper`, `_put`, `_special`; `xml_reader.element_identity`, `xml_reader.local`.
- Produces: XML extraction semantics used by the pipeline and acceptance tests:
  - field values: `value_child` first, then element text (empty element → `""`; no match → `None`);
  - multi-match: first value lands, the list goes to `_extras[col]`;
  - `"*"` children-as-fields;
  - unpivot (`rid#name`, `rid#name[n]` for repeats);
  - `$parent(target)`;
  - an unexpected exception on one record becomes `RejectRow(..., "mapping_error")`.
  - Fixtures `formulary_spec.json` / `formulary_schemas.json` are used by later tasks.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/formulary_schemas.json` (column lists from the legacy formulary parser):

```json
{
  "formulary_benefit_definition": ["fileid", "serffbindername", "run_id", "templateVersion", "issuer_id", "state_code", "formulary_id", "drug_list_id", "num_of_tiers", "drug_tier_id", "one_month", "three_month_in", "three_month_out", "cost_coverage_type", "prescription_period", "copay", "coin"],
  "formulary_tier_definition": ["fileid", "serffbindername", "run_id", "templateVersion", "issuer_id", "state_code", "drug_list_id", "tier_level", "tier_type"],
  "formulary_drug_list": ["fileid", "serffbindername", "run_id", "templateVersion", "issuer_id", "state_code", "drug_list_id", "rxcui", "tier_level", "authorizationRequired", "stepTherapyRequired"]
}
```

`tests/fixtures/formulary_spec.json`:

```json
{
  "kind": "xml",
  "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
  "value_child": "x:cellValue",
  "extractions": [
    {
      "target": "formulary_benefit_definition",
      "records": "/x:prescriptionDrugTemplateVO/x:formularyList/x:formularyVO/x:formularyCostSharingTypeList/x:formularyCostSharingTypeVO/x:costSharingTypeList/x:costSharingTypeVO",
      "fields": {
        "fileid": "$file_id",
        "serffbindername": "$context.serffbindername",
        "run_id": "$context.run_id",
        "templateVersion": "/x:prescriptionDrugTemplateVO/x:header/x:templateVersion",
        "issuer_id": "/x:prescriptionDrugTemplateVO/x:header/x:issuerId",
        "state_code": "/x:prescriptionDrugTemplateVO/x:header/x:statePostalCode",
        "formulary_id": "../../../../x:formularyID",
        "drug_list_id": "../../../../x:drugListID",
        "num_of_tiers": "../../../../x:numberTiers",
        "drug_tier_id": "../../x:drugTierLevel",
        "one_month": "../../x:oneMonthOutNetworkRetailOfferedIndicator",
        "three_month_in": "../../x:threeMonthInNetworkMailOfferedIndicator",
        "three_month_out": "../../x:threeMonthOutNetworkMailOfferedIndicator",
        "cost_coverage_type": "x:networkCostType",
        "prescription_period": "x:drugPrescriptionPeriodType",
        "copay": "x:coPayment",
        "coin": "x:coInsurance"
      }
    },
    {
      "target": "formulary_tier_definition",
      "records": "/x:prescriptionDrugTemplateVO/x:drugList/x:drugListVO/x:drugTierLevelList/x:drugTierLevelVO",
      "fields": {
        "fileid": "$file_id",
        "serffbindername": "$context.serffbindername",
        "run_id": "$context.run_id",
        "templateVersion": "/x:prescriptionDrugTemplateVO/x:header/x:templateVersion",
        "issuer_id": "/x:prescriptionDrugTemplateVO/x:header/x:issuerId",
        "state_code": "/x:prescriptionDrugTemplateVO/x:header/x:statePostalCode",
        "drug_list_id": "../../x:drugListID",
        "tier_level": "x:drugTierLevel",
        "tier_type": "x:drugTierType"
      }
    },
    {
      "target": "formulary_drug_list",
      "records": "/x:prescriptionDrugTemplateVO/x:drugList/x:drugListVO/x:drugVOList/x:drugVO",
      "fields": {
        "fileid": "$file_id",
        "serffbindername": "$context.serffbindername",
        "run_id": "$context.run_id",
        "templateVersion": "/x:prescriptionDrugTemplateVO/x:header/x:templateVersion",
        "issuer_id": "/x:prescriptionDrugTemplateVO/x:header/x:issuerId",
        "state_code": "/x:prescriptionDrugTemplateVO/x:header/x:statePostalCode",
        "drug_list_id": "../../x:drugListID",
        "rxcui": "x:rXCUI",
        "tier_level": "x:tier",
        "authorizationRequired": "x:authorizationRequired",
        "stepTherapyRequired": "x:stepTherapyRequired"
      }
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_spec_xml.py`:

```python
from __future__ import annotations

import pytest

from importer.mappers.spec import DeclarativeMapper, SpecError, parse_spec
from importer.models import LandRow, RejectRow
from importer.readers import parse_document
from tests.support import ctx, fixture, load_json

NS = "http://vo.ffe.cms.hhs.gov"
REQUEST = {"serffbindername": "ABCD-DC25-1", "run_id": "42"}


def _formulary_rows():
    mapper = DeclarativeMapper(parse_spec(load_json("formulary_spec.json")))
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    return list(mapper.extract(doc, ctx(load_json("formulary_schemas.json"), REQUEST)))


def _inline(spec_extractions, xml: str, schemas, value_child="x:cellValue"):
    spec = {"kind": "xml", "namespaces": {"x": "urn:t"}, "extractions": spec_extractions}
    if value_child:
        spec["value_child"] = value_child
    mapper = DeclarativeMapper(parse_spec(spec))
    return list(mapper.extract(parse_document(xml.encode(), "XML"), ctx(schemas)))


def test_formulary_counts_per_target():
    rows = _formulary_rows()
    counts = {}
    for row in rows:
        counts[row.target] = counts.get(row.target, 0) + 1
    assert counts == {"formulary_benefit_definition": 5, "formulary_tier_definition": 2, "formulary_drug_list": 3}


def test_formulary_first_benefit_row():
    first = _formulary_rows()[0]
    assert first.row_identity == (
        "/formularyList/formularyVO[1]/formularyCostSharingTypeList"
        "/formularyCostSharingTypeVO[1]/costSharingTypeList/costSharingTypeVO[1]"
    )
    assert first.fields == {
        "fileid": "7", "serffbindername": "ABCD-DC25-1", "run_id": "42", "templateVersion": "v13.0",
        "issuer_id": "12345", "state_code": "DC", "formulary_id": "DCF001", "drug_list_id": "1",
        "num_of_tiers": "2", "drug_tier_id": "1", "one_month": "Yes", "three_month_in": "Yes",
        "three_month_out": "No", "cost_coverage_type": "In Network", "prescription_period": "1 Month",
        "copay": "$10.00", "coin": "0%",
    }
    assert first.extras is None


def test_unmapped_child_lands_in_extras():
    drugs = [r for r in _formulary_rows() if r.target == "formulary_drug_list"]
    assert drugs[2].extras == {"quantityLimit": "Yes"}
    assert drugs[0].extras is None


def test_children_as_fields():
    xml = '<root xmlns="urn:t"><pkg><a><cellValue>1</cellValue></a><b><cellValue>2</cellValue></b><zz><cellValue>9</cellValue></zz></pkg></root>'
    rows = _inline([{"target": "pkg", "records": "/x:root/x:pkg", "fields": {"*": "x:*"}}], xml, {"pkg": ["a", "b"]})
    assert rows == [LandRow("pkg", "/pkg", {"a": "1", "b": "2"}, {"zz": "9"})]


def test_unpivot_children():
    xml = '<root xmlns="urn:t"><items><age><cellValue>21</cellValue></age><rate><cellValue>300</cellValue></rate></items></root>'
    rows = _inline(
        [{"target": "rate", "records": "/x:root/x:items", "fields": {"item": "$row_identity"},
          "unpivot": {"over": "x:*", "name_column": "component_name", "value_column": "component_value"}}],
        xml, {"rate": ["item", "component_name", "component_value"]},
    )
    assert [r.row_identity for r in rows] == ["/items#age", "/items#rate"]
    assert rows[1].fields == {"item": "/items", "component_name": "rate", "component_value": "300"}


def test_multi_match_lands_first_and_keeps_all_in_extras():
    xml = '<root xmlns="urn:t"><d><tier><cellValue>1</cellValue></tier><tier><cellValue>2</cellValue></tier></d></root>'
    rows = _inline([{"target": "d", "records": "/x:root/x:d", "fields": {"tier": "x:tier"}}], xml, {"d": ["tier"]})
    assert rows[0].fields == {"tier": "1"}
    assert rows[0].extras == {"tier": ["1", "2"]}


def test_missing_path_is_null_and_empty_element_is_empty_string():
    xml = '<root xmlns="urn:t"><d><a><cellValue/></a></d></root>'
    rows = _inline([{"target": "d", "records": "/x:root/x:d", "fields": {"a": "x:a", "b": "x:b"}}], xml, {"d": ["a", "b"]})
    assert rows[0].fields == {"a": "", "b": None}


def test_parent_reference():
    xml = '<root xmlns="urn:t"><pkg><id>P1</id><ben><n>x</n></ben></pkg><pkg><id>P2</id><ben><n>y</n></ben></pkg></root>'
    rows = _inline(
        [
            {"target": "package", "records": "/x:root/x:pkg", "fields": {"id": "x:id"}},
            {"target": "benefit", "records": "/x:root/x:pkg/x:ben", "fields": {"n": "x:n", "pkg": "$parent(package)"}},
        ],
        xml, {"package": ["id"], "benefit": ["n", "pkg"]}, value_child=None,
    )
    benefits = [r for r in rows if r.target == "benefit"]
    assert [b.fields["pkg"] for b in benefits] == ["/pkg[1]", "/pkg[2]"]


def test_record_level_exception_becomes_mapping_error(monkeypatch):
    original = DeclarativeMapper._value

    def flaky(self, el):
        value = original(self, el)
        if value == "boom":
            raise ValueError("bad value")
        return value

    monkeypatch.setattr(DeclarativeMapper, "_value", flaky)
    xml = '<root xmlns="urn:t"><d><a>ok</a></d><d><a>boom</a></d></root>'
    rows = _inline([{"target": "d", "records": "/x:root/x:d", "fields": {"a": "x:a"}}], xml, {"d": ["a"]}, value_child=None)
    assert isinstance(rows[0], LandRow)
    assert isinstance(rows[1], RejectRow) and rows[1].reason_code == "mapping_error"
    assert rows[1].row_identity == "/d[2]"


def test_invalid_xpath_is_a_spec_error():
    with pytest.raises(SpecError, match="invalid XPath"):
        parse_spec({"kind": "xml", "extractions": [{"target": "d", "records": "/x:[", "fields": {"a": "x:a"}}]})


def test_xml_spec_rejects_tabular_document():
    mapper = DeclarativeMapper(parse_spec(load_json("formulary_spec.json")))
    with pytest.raises(SpecError, match="xml spec"):
        list(mapper.extract(parse_document(b"a,b\n1,2\n", "CSV"), ctx({})))
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_spec_xml.py -v`
Expected: FAIL, with `NotImplementedError: XML extraction lands in plan Task 11`.

- [ ] **Step 4: Replace `_extract_xml` in `src/importer/mappers/spec.py` and add the XML helpers**

First add `local` to the existing `xml_reader` import line:
```python
from ..readers.xml_reader import element_identity, local
```

Then replace the whole `# ---- xml (Task 11) ----` block (the `_extract_xml` stub) with:

```python
    # ---- xml -----------------------------------------------------------------

    def _extract_xml(self, doc: XmlDocument, ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        root = doc.root
        per_extraction = [self._xpath(e.records)(root) for e in self.spec.extractions]
        record_paths: dict[str, set[str]] = {}
        for extraction, elements in zip(self.spec.extractions, per_extraction):
            record_paths.setdefault(extraction.target, set()).update(element_identity(el, root) for el in elements)
        for extraction, elements in zip(self.spec.extractions, per_extraction):
            schema_set = set(ctx.schemas.get(extraction.target, []))
            for el in elements:
                row_identity = element_identity(el, root)
                try:
                    rows = list(self._xml_rows(extraction, el, row_identity, root, schema_set, ctx, record_paths))
                except SpecError:
                    raise
                except Exception as exc:
                    yield RejectRow(
                        extraction.target, row_identity, etree.tostring(el, encoding="unicode"),
                        "mapping_error", f"{type(exc).__name__}: {exc}",
                    )
                    continue
                yield from rows

    def _xml_rows(self, extraction, el, row_identity, root, schema_set, ctx, record_paths) -> Iterator[LandRow]:
        fields: dict = {}
        extras: dict = {}
        used: set = set()
        for column, src in extraction.fields.items():
            if column == "*":
                for child in self._xpath(src)(el):
                    if isinstance(child, etree._Element):
                        used.add(child)
                        _put(fields, extras, schema_set, local(child), self._value(child))
                continue
            if _is_special(src):
                _put(fields, extras, schema_set, column, self._special(src, ctx, row_identity, el=el, root=root, record_paths=record_paths))
                continue
            values = self._values(self._xpath(src)(el), used)
            _put(fields, extras, schema_set, column, values[0] if values else None)
            if len(values) > 1:
                extras[column] = values
        if extraction.unpivot:
            seen: dict[str, int] = {}
            for child in self._xpath(extraction.unpivot.over)(el):
                if not isinstance(child, etree._Element):
                    continue
                name = local(child)
                seen[name] = seen.get(name, 0) + 1
                suffix = name if seen[name] == 1 else f"{name}[{seen[name]}]"
                row_fields, row_extras = dict(fields), dict(extras)
                _put(row_fields, row_extras, schema_set, extraction.unpivot.name_column, name)
                _put(row_fields, row_extras, schema_set, extraction.unpivot.value_column, self._value(child))
                yield LandRow(extraction.target, f"{row_identity}#{suffix}", row_fields, row_extras or None)
            return
        if extraction.unmapped == "extras":
            for child in el:
                if not isinstance(child.tag, str) or child in used or not self._is_value_element(child):
                    continue
                name = local(child)
                if name not in fields and name not in extras:
                    extras[name] = self._value(child)
        yield LandRow(extraction.target, row_identity, fields, extras or None)

    def _values(self, result, used: set) -> list[str]:
        if isinstance(result, list):
            out: list[str] = []
            for item in result:
                if isinstance(item, etree._Element):
                    used.add(item)
                    out.append(self._value(item))
                else:
                    out.append(str(item))
            return out
        if isinstance(result, bool):
            return ["true" if result else "false"]
        if isinstance(result, float):
            return [str(int(result)) if result.is_integer() else str(result)]
        return [str(result)]

    def _value(self, el) -> str:
        if self.spec.value_child:
            hit = self._xpath(self.spec.value_child)(el)
            if hit and isinstance(hit[0], etree._Element):
                return hit[0].text if hit[0].text is not None else ""
        return el.text if el.text is not None else ""

    def _is_value_element(self, el) -> bool:
        if self.spec.value_child and self._xpath(self.spec.value_child)(el):
            return True
        return len(el) == 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_spec_xml.py tests/test_spec_tabular.py -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/mappers/spec.py tests/fixtures/formulary_spec.json tests/fixtures/formulary_schemas.json tests/test_spec_xml.py
git commit -m "$(cat <<'EOF'
Add XPath-based XML extraction to the declarative spec

One XML file now feeds several schemas with ancestor context, cellValue
unwrapping, children-as-fields, unpivot and parent references.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Shapers (`excel.anchor_header`, `excel.crosstab_unpivot`)

**Files:**
- Create: `src/importer/shapers/__init__.py`, `src/importer/shapers/excel.py`
- Test: `tests/test_shapers.py`

**Interfaces:**
- Consumes: `readers.base` (`Record`, `Table`, `TabularDocument`, `normalize_name`, `dedupe_names`).
- Produces:
  - Registry: `shapers.shaper(name)` decorator; `shapers.registered_shapers() -> set[str]`; `shapers.apply_shapers(doc, shapers: list[dict]) -> TabularDocument`.
  - `excel.anchor_header(doc, *, sheet, anchor, search_rows=4, search_cols=14)`: header rows 0..anchor-row are flattened with `_`; rows above the anchor row are forward-filled horizontally (merged cells); data rows follow.
  - `excel.crosstab_unpivot(doc, *, sheet, id_columns, name_column="benefit_name", value_column="value")`: one record per non-`None` non-id cell, `row_identity = "<rid>#<column>"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_shapers.py`:

```python
from __future__ import annotations

import io

import openpyxl
import pytest

from importer.mappers.spec import DeclarativeMapper, parse_spec
from importer.readers import parse_document
from importer.shapers import apply_shapers, registered_shapers
from tests.support import ctx


def sopa_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOPA"
    ws.append([None, None, "Primary Care Visit", None, "Specialist Visit", None])
    ws.append(["Plan ID", "2nd Tier Utilization", "Copay", "Coinsurance", "Copay", "Coinsurance"])
    ws.append(["P1", "0%", "$20", "0%", "$40", "10%"])
    ws.append(["P2", "10%", "$30", None, "$50", "20%"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_builtin_shapers_are_registered():
    assert {"excel.anchor_header", "excel.crosstab_unpivot"} <= registered_shapers()


def test_anchor_header_flattens_multi_row_headers():
    doc = apply_shapers(parse_document(sopa_workbook(), "EXCEL"),
                        [{"name": "excel.anchor_header", "options": {"sheet": "SOPA", "anchor": "2ndTierUtilization"}}])
    table = doc.table("SOPA")
    assert table.columns == [
        "planid", "2ndtierutilization", "primarycarevisit_copay", "primarycarevisit_coinsurance",
        "specialistvisit_copay", "specialistvisit_coinsurance",
    ]
    assert [r.row_identity for r in table.records] == ["SOPA!R3", "SOPA!R4"]
    assert table.records[1].values["primarycarevisit_coinsurance"] is None


def test_anchor_not_found_raises():
    with pytest.raises(ValueError, match="anchor"):
        apply_shapers(parse_document(sopa_workbook(), "EXCEL"),
                      [{"name": "excel.anchor_header", "options": {"sheet": "SOPA", "anchor": "nothere"}}])


def test_crosstab_unpivot_through_a_spec():
    spec = {
        "kind": "tabular",
        "shapers": [
            {"name": "excel.anchor_header", "options": {"sheet": "SOPA", "anchor": "2ndTierUtilization"}},
            {"name": "excel.crosstab_unpivot", "options": {"sheet": "SOPA", "id_columns": ["planid", "2ndtierutilization"]}},
        ],
        "extractions": [{"target": "plan_variant_benefit", "records": "SOPA", "auto": True}],
    }
    schema = {"plan_variant_benefit": ["planid", "2ndtierutilization", "benefit_name", "value"]}
    rows = list(DeclarativeMapper(parse_spec(spec, known_shapers=registered_shapers()))
                .extract(parse_document(sopa_workbook(), "EXCEL"), ctx(schema)))
    assert len(rows) == 7
    assert rows[0].row_identity == "SOPA!R3#primarycarevisit_copay"
    assert rows[0].fields == {"planid": "P1", "2ndtierutilization": "0%", "benefit_name": "primarycarevisit_copay", "value": "$20"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_shapers.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.shapers'`.

- [ ] **Step 3: Implement `src/importer/shapers/__init__.py`**

```python
"""Shaper registry (design §8.5 tier 2): reviewed structural helpers.

Shapers reshape awkward layouts (anchor-cell headers, cross-tabs) into plain
tables. They never clean or coerce values — that stays downstream (§0).
"""

from __future__ import annotations

from collections.abc import Callable

from ..readers.base import TabularDocument

Shaper = Callable[..., TabularDocument]
_REGISTRY: dict[str, Shaper] = {}


def shaper(name: str) -> Callable[[Shaper], Shaper]:
    def register(fn: Shaper) -> Shaper:
        if name in _REGISTRY:
            raise ValueError(f"shaper {name!r} is already registered")
        _REGISTRY[name] = fn
        return fn
    return register


def _load_builtin() -> None:
    from . import excel  # noqa: F401  (registers on import)


def registered_shapers() -> set[str]:
    _load_builtin()
    return set(_REGISTRY)


def apply_shapers(doc: TabularDocument, shapers: list[dict]) -> TabularDocument:
    _load_builtin()
    for step in shapers:
        doc = _REGISTRY[step["name"]](doc, **step.get("options", {}))
    return doc
```

- [ ] **Step 4: Implement `src/importer/shapers/excel.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_shapers.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/shapers tests/test_shapers.py
git commit -m "$(cat <<'EOF'
Add the shaper registry with Excel anchor-header and cross-tab shapers

Report-style workbooks (SOPA/DNTL) land without manual file edits; only the
structural steps of the legacy parser are ported, cleaning stays downstream.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Mapper loading (plugins), preview, and registration helpers

**Files:**
- Create: `src/importer/mappers/loading.py`, `src/importer/mappers/preview.py`, `src/importer/mappers/plugins/__init__.py`, `src/importer/admin.py`
- Test: `tests/test_loading_and_admin.py`

**Interfaces:**
- Consumes: `ArtifactRow`, `Tx` (Task 9); `parse_spec`, `DeclarativeMapper` (Tasks 10–11); `registered_shapers` (Task 12); `fingerprint` (Task 8); `parse_document` (Task 4).
- Produces:
  - In `loading`: `MapperLoadError(reason_code, detail)`, `PLUGINS_DIR`, `file_sha256(path) -> str`, `load_mapper(artifact: ArtifactRow, *, known_schemas: set[str], plugins_dir: Path = PLUGINS_DIR) -> Mapper`.
  - In `preview`: `PreviewResult(counts, sample, extras_keys, rejects)` with `.to_dict()`, and `preview(mapper, doc, ctx, n=20) -> PreviewResult`.
  - In `admin`:
    - `register_schema(tx, schema_id, columns, *, created_by) -> ArtifactRow`
    - `register_mapper(tx, key, definition, *, created_by, plugins_dir=PLUGINS_DIR) -> ArtifactRow` (validates first; pins the plugin hash)
    - `register_signature(tx, data: bytes, declared, mapper_version_id, *, approved_by) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_loading_and_admin.py`:

```python
from __future__ import annotations

import pytest

from importer.admin import register_mapper, register_schema, register_signature
from importer.mappers.loading import MapperLoadError, load_mapper
from importer.mappers.preview import preview
from importer.memory_store import MemoryStore
from importer.models import Declared
from importer.readers import parse_document
from tests.support import EVENT_SCHEMA, URL_SCHEMA, URL_SPEC_A, ctx, fixture, load_json

PLUGIN = '''
from importer.mappers.base import Mapper
from importer.models import LandRow


class Echo(Mapper):
    version = 1
    targets = ("event",)

    def extract(self, doc, ctx):
        for record in doc.table("*").records:
            yield LandRow("event", record.row_identity, {"id": record.values.get("id")})
'''


def _schemas(tx, schemas):
    for schema_id, columns in schemas.items():
        register_schema(tx, schema_id, columns, created_by="test")


def test_load_declarative_formulary_mapper():
    store = MemoryStore()
    with store.transaction() as tx:
        _schemas(tx, load_json("formulary_schemas.json"))
        row = register_mapper(tx, "formulary", load_json("formulary_spec.json"), created_by="test")
        mapper = load_mapper(row, known_schemas=set(load_json("formulary_schemas.json")))
    assert mapper.targets == ("formulary_benefit_definition", "formulary_tier_definition", "formulary_drug_list")
    assert mapper.version == row.version_no


def test_register_mapper_rejects_unknown_target_and_writes_nothing():
    store = MemoryStore()
    with pytest.raises(MapperLoadError) as err:
        with store.transaction() as tx:
            register_mapper(tx, "urls", URL_SPEC_A, created_by="test")
    assert err.value.reason_code == "invalid_spec"
    assert store.state.artifacts == {}


def test_unknown_kind():
    store = MemoryStore()
    with pytest.raises(MapperLoadError) as err:
        with store.transaction() as tx:
            register_mapper(tx, "x", {"kind": "magic"}, created_by="test")
    assert err.value.reason_code == "unknown_mapper_kind"


def test_plugin_hash_is_pinned_and_checked(tmp_path):
    (tmp_path / "echo.py").write_text(PLUGIN, encoding="utf-8")
    store = MemoryStore()
    with store.transaction() as tx:
        _schemas(tx, EVENT_SCHEMA)
        row = register_mapper(tx, "echo", {"kind": "plugin", "ref": "echo:Echo", "version": 1}, created_by="test", plugins_dir=tmp_path)
    assert len(row.definition["sha256"]) == 64
    mapper = load_mapper(row, known_schemas={"event"}, plugins_dir=tmp_path)
    rows = list(mapper.extract(parse_document(b"id\n1\n", "CSV"), ctx(EVENT_SCHEMA)))
    assert rows[0].fields == {"id": "1"}

    (tmp_path / "echo.py").write_text(PLUGIN + "\n# edited in place\n", encoding="utf-8")
    with pytest.raises(MapperLoadError) as err:
        load_mapper(row, known_schemas={"event"}, plugins_dir=tmp_path)
    assert err.value.reason_code == "plugin_hash_mismatch"


def test_plugin_version_must_match(tmp_path):
    (tmp_path / "echo.py").write_text(PLUGIN, encoding="utf-8")
    store = MemoryStore()
    with pytest.raises(MapperLoadError) as err:
        with store.transaction() as tx:
            _schemas(tx, EVENT_SCHEMA)
            register_mapper(tx, "echo", {"kind": "plugin", "ref": "echo:Echo", "version": 2}, created_by="test", plugins_dir=tmp_path)
    assert err.value.reason_code == "plugin_version_mismatch"


def test_register_schema_validates_columns():
    store = MemoryStore()
    with store.transaction() as tx:
        with pytest.raises(ValueError, match="reserved"):
            register_schema(tx, "s", ["_file_id"], created_by="t")
        with pytest.raises(ValueError, match="duplicate"):
            register_schema(tx, "s", ["a", "A"], created_by="t")
        with pytest.raises(ValueError, match="identifier"):
            register_schema(tx, "s", ["bad col"], created_by="t")


def test_register_signature_activates_lookup():
    store = MemoryStore()
    declared = Declared.normalize("ME", "PC", "CSV")
    data = fixture("url_template_a.csv").read_bytes()
    with store.transaction() as tx:
        _schemas(tx, URL_SCHEMA)
        row = register_mapper(tx, "urls_a", URL_SPEC_A, created_by="test")
        sid = register_signature(tx, data, declared, row.version_id, approved_by="test")
        fp = tx.registry.get(sid).fingerprint
        assert tx.registry.lookup(fp, None).mapper_version_id == row.version_id


def test_preview_counts_and_samples():
    store = MemoryStore()
    with store.transaction() as tx:
        _schemas(tx, URL_SCHEMA)
        row = register_mapper(tx, "urls_a", URL_SPEC_A, created_by="test")
    mapper = load_mapper(row, known_schemas={"url_template"})
    result = preview(mapper, parse_document(fixture("url_template_a.csv").read_bytes(), "CSV"), ctx(URL_SCHEMA), n=1)
    data = result.to_dict()
    assert data["counts"] == {"url_template": 2}
    assert len(data["sample"]["url_template"]) == 1
    assert data["sample"]["url_template"][0]["row_identity"] == "L2"
    assert data["rejects"] == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_loading_and_admin.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.admin'`.

- [ ] **Step 3: Implement `src/importer/mappers/plugins/__init__.py`**

```python
"""Human-saved plugin mappers (design §8.5 tier 3).

Each module here holds a Mapper subclass with a ``version`` class attribute.
A plugin becomes live only when a person commits it AND a mapper artifact
pins its SHA-256; editing a file in place makes the loader refuse it.
"""
```

- [ ] **Step 4: Implement `src/importer/mappers/loading.py`**

```python
"""Resolve a mapper artifact version to a Mapper (design §8.9)."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

from ..models import ArtifactType
from ..repositories import ArtifactRow
from ..shapers import registered_shapers
from .base import Mapper
from .spec import DeclarativeMapper, SpecError, parse_spec

PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"
_MODULE = re.compile(r"^[a-z_][a-z0-9_]*$")


class MapperLoadError(Exception):
    """The mapper artifact cannot be used (configuration error → file failed)."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_mapper(artifact: ArtifactRow, *, known_schemas: set[str], plugins_dir: Path = PLUGINS_DIR) -> Mapper:
    if artifact.artifact_type is not ArtifactType.MAPPER:
        raise MapperLoadError("not_a_mapper", f"artifact {artifact.version_id} is a {artifact.artifact_type.value}")
    definition = artifact.definition
    kind = definition.get("kind")
    if kind in ("xml", "tabular"):
        try:
            spec = parse_spec(definition, known_schemas=known_schemas, known_shapers=registered_shapers())
        except SpecError as exc:
            raise MapperLoadError("invalid_spec", str(exc)) from exc
        return DeclarativeMapper(spec, version=artifact.version_no)
    if kind == "plugin":
        return _load_plugin(definition, known_schemas, plugins_dir)
    raise MapperLoadError("unknown_mapper_kind", f"kind={kind!r}")


def _load_plugin(definition: dict, known_schemas: set[str], plugins_dir: Path) -> Mapper:
    ref = str(definition.get("ref", ""))
    module, _, class_name = ref.partition(":")
    if not _MODULE.match(module) or not class_name.isidentifier():
        raise MapperLoadError("invalid_plugin_ref", ref)
    path = plugins_dir / f"{module}.py"
    if not path.exists():
        raise MapperLoadError("plugin_missing", str(path))
    actual = file_sha256(path)
    if actual != definition.get("sha256"):
        raise MapperLoadError("plugin_hash_mismatch", f"{path.name}: pinned {definition.get('sha256')}, found {actual}")
    spec = importlib.util.spec_from_file_location(f"importer_plugin_{module}", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    cls = getattr(loaded, class_name, None)
    if not (isinstance(cls, type) and issubclass(cls, Mapper)):
        raise MapperLoadError("invalid_plugin_class", ref)
    if getattr(cls, "version", None) != definition.get("version"):
        raise MapperLoadError("plugin_version_mismatch", f"{ref}: class version {getattr(cls, 'version', None)}, artifact {definition.get('version')}")
    mapper = cls()
    unknown = [t for t in mapper.targets if t not in known_schemas]
    if unknown:
        raise MapperLoadError("invalid_spec", f"{ref}: targets {unknown} have no active schema definition")
    return mapper
```

- [ ] **Step 5: Implement `src/importer/mappers/preview.py`**

```python
"""Dry run of a mapper against a document (design §8.9); no database writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import RejectRow
from .base import Mapper, RunContext


@dataclass
class PreviewResult:
    counts: dict[str, int]
    sample: dict[str, list[dict]]
    extras_keys: dict[str, list[str]]
    rejects: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


def preview(mapper: Mapper, doc, ctx: RunContext, n: int = 20) -> PreviewResult:
    counts: dict[str, int] = {}
    sample: dict[str, list[dict]] = {}
    extras: dict[str, set[str]] = {}
    rejects: dict[str, int] = {}
    for row in mapper.extract(doc, ctx):
        if isinstance(row, RejectRow):
            rejects[row.reason_code] = rejects.get(row.reason_code, 0) + 1
            continue
        counts[row.target] = counts.get(row.target, 0) + 1
        bucket = sample.setdefault(row.target, [])
        if len(bucket) < n:
            entry = {"row_identity": row.row_identity, **row.fields}
            if row.extras:
                entry["_extras"] = row.extras
            bucket.append(entry)
        if row.extras:
            extras.setdefault(row.target, set()).update(row.extras)
    return PreviewResult(counts, sample, {t: sorted(k) for t, k in extras.items()}, rejects)
```

- [ ] **Step 6: Implement `src/importer/admin.py`**

```python
"""Registration helpers shared by the CLI and tests (hand registration, §8.1)."""

from __future__ import annotations

import re
from pathlib import Path

from .fingerprint import fingerprint
from .mappers.loading import PLUGINS_DIR, file_sha256, load_mapper
from .models import ArtifactType, Declared
from .readers import parse_document
from .repositories import ArtifactRow, Tx

IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def register_schema(tx: Tx, schema_id: str, columns: list[str], *, created_by: str | None) -> ArtifactRow:
    if not IDENT.match(schema_id):
        raise ValueError(f"schema id {schema_id!r} is not a valid identifier")
    seen: set[str] = set()
    for column in columns:
        if not IDENT.match(column):
            raise ValueError(f"column {column!r} is not a valid identifier")
        if column.startswith("_"):
            raise ValueError(f"column {column!r}: names starting with '_' are reserved for lineage")
        if column.lower() in seen:
            raise ValueError(f"duplicate column {column!r} (SQL Server names are case-insensitive)")
        seen.add(column.lower())
    return tx.artifacts.put_version(ArtifactType.SCHEMA, schema_id, {"columns": list(columns)}, created_by=created_by)


def register_mapper(tx: Tx, key: str, definition: dict, *, created_by: str | None, plugins_dir: Path = PLUGINS_DIR) -> ArtifactRow:
    defn = dict(definition)
    sha = None
    if defn.get("kind") == "plugin":
        module = str(defn.get("ref", "")).partition(":")[0]
        path = plugins_dir / f"{module}.py"
        if path.exists():
            sha = defn["sha256"] = file_sha256(path)
    known = {a.artifact_key for a in tx.artifacts.active_schemas()}
    draft = ArtifactRow(0, ArtifactType.MAPPER, key, 0, defn, False, created_by, sha)
    load_mapper(draft, known_schemas=known, plugins_dir=plugins_dir)  # validate before writing
    return tx.artifacts.put_version(ArtifactType.MAPPER, key, defn, created_by=created_by, content_sha256=sha)


def register_signature(tx: Tx, data: bytes, declared: Declared, mapper_version_id: int, *, approved_by: str) -> int:
    sig = fingerprint(parse_document(data, declared.file_type), declared)
    return tx.registry.register(sig, mapper_version_id, approved_by)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_loading_and_admin.py -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add src/importer/mappers/loading.py src/importer/mappers/preview.py src/importer/mappers/plugins/__init__.py src/importer/admin.py tests/test_loading_and_admin.py
git commit -m "$(cat <<'EOF'
Add mapper loading with hash-pinned plugins, preview, and registration

A plugin edited in place is refused until a new version pins its hash, so
"versioned, never edited in place" holds for code too.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---
### Task 14: Miss profiler and matcher

**Files:**
- Create: `src/importer/profiler.py`
- Modify: `tests/support.py` (add `setup_signature`)
- Test: `tests/test_profiler.py`

**Interfaces:**
- Consumes: readers, `Signature`, `SignatureRegistry`, `ArtifactRepository`, `admin` helpers.
- Produces:
  - `profile(doc) -> dict` (`{"kind": "xml", "root", "namespace", "paths": {path: count}}` or `{"kind": "tabular", "format", "tables": {name: columns}}`).
  - `Candidate(kind, score, signature_id, fingerprint, mapper_version_id, schema_id, table, matched, missing, extra)` with `.summary() -> str` and `.to_dict() -> dict`.
  - `match(sig, prof, registry, artifacts, *, limit=5, min_score=0.5) -> list[Candidate]`.
  - `tests.support.setup_signature(store, sample: Path, declared, schemas, mapper_def, key) -> int` (returns the mapper `version_id`).

- [ ] **Step 1: Add `setup_signature` to `tests/support.py`**

Append:

```python
def setup_signature(store, sample: Path, declared, schemas: dict[str, list[str]], mapper_def: dict, key: str) -> int:
    """Hand-register schemas + a mapper + a signature (what `importer mapper add` / `signature register` do)."""
    from importer.admin import register_mapper, register_schema, register_signature

    with store.transaction() as tx:
        for schema_id, columns in schemas.items():
            register_schema(tx, schema_id, columns, created_by="test")
        mapper = register_mapper(tx, key, mapper_def, created_by="test")
        register_signature(tx, sample.read_bytes(), declared, mapper.version_id, approved_by="test")
    return mapper.version_id
```

- [ ] **Step 2: Write the failing tests**

`tests/test_profiler.py`:

```python
from __future__ import annotations

from importer.admin import register_schema
from importer.fingerprint import fingerprint
from importer.memory_store import MemoryStore
from importer.models import Declared
from importer.profiler import match, profile
from importer.readers import parse_document
from tests.support import URL_SCHEMA, fixture, load_json, setup_signature


def test_xml_profile_counts_paths():
    prof = profile(parse_document(fixture("formulary_dc.xml").read_bytes(), "XML"))
    assert prof["kind"] == "xml"
    assert prof["root"] == "prescriptionDrugTemplateVO"
    assert prof["paths"]["/prescriptionDrugTemplateVO/drugList/drugListVO/drugVOList/drugVO"] == 3


def test_tabular_profile_lists_columns():
    prof = profile(parse_document(fixture("url_template_b.csv").read_bytes(), "CSV"))
    assert prof == {"kind": "tabular", "format": "DELIMITED", "tables": {"*": ["urlid", "url", "urltype"]}}


def test_same_template_under_another_project_proposes_mapper_reuse():
    store = MemoryStore()
    mapper_id = setup_signature(
        store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
        load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary",
    )
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    sig = fingerprint(doc, Declared.normalize("ME", "PC", "XML"))
    with store.transaction() as tx:
        candidates = match(sig, profile(doc), tx.registry, tx.artifacts)
    assert candidates[0].kind == "reuse_mapper"
    assert candidates[0].mapper_version_id == mapper_id
    assert candidates[0].summary() == f"looks like DC|PC|XML|prescriptionDrugTemplateVO → reuse mapper {mapper_id}"


def test_tabular_overlap_names_extra_and_missing_columns():
    store = MemoryStore()
    with store.transaction() as tx:
        register_schema(tx, "url_template", URL_SCHEMA["url_template"], created_by="t")
    doc = parse_document(fixture("url_template_b.csv").read_bytes(), "CSV")
    sig = fingerprint(doc, Declared.normalize("VT", "PC", "CSV"))
    with store.transaction() as tx:
        candidates = match(sig, profile(doc), tx.registry, tx.artifacts)
    best = candidates[0]
    assert best.kind == "schema" and best.schema_id == "url_template"
    assert best.score == 0.67
    assert best.extra == ["url"]
    assert best.missing == ["fileid", "issuerid", "newurl", "runid"]
    assert best.summary() == "looks like url_template (0.67; extra: url; missing: fileid, issuerid, newurl, runid)"
    assert best.to_dict()["schema_id"] == "url_template"


def test_low_overlap_is_not_proposed():
    store = MemoryStore()
    with store.transaction() as tx:
        register_schema(tx, "url_template", URL_SCHEMA["url_template"], created_by="t")
    doc = parse_document(b"alpha,beta,gamma\n1,2,3\n", "CSV")
    sig = fingerprint(doc, Declared.normalize("VT", "PC", "CSV"))
    with store.transaction() as tx:
        assert match(sig, profile(doc), tx.registry, tx.artifacts) == []
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_profiler.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.profiler'`.

- [ ] **Step 4: Implement `src/importer/profiler.py`**

```python
"""Miss handling: observed-schema profile and "looks like" matching (design §8.4).

Deterministic — no model. Candidates, best first:
  1. reuse_mapper — an active signature with the same physical part (and XML
     namespace) under another project/class, e.g. another state's template.
  2. schema — tabular files whose columns are largely found in a schema.
     Score = share of the file's columns present in the schema (Jaccard as the
     tie-break); mirrored tables carry lineage/context columns files never have.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .models import Signature
from .readers.base import Document, XmlDocument, normalize_name
from .readers.xml_reader import local

MAX_PATHS = 500


def profile(doc: Document) -> dict:
    if isinstance(doc, XmlDocument):
        counts: dict[str, int] = {}
        for el in doc.root.iter():
            if not isinstance(el.tag, str):
                continue
            path = "/" + "/".join(local(node) for node in reversed([el, *el.iterancestors()]))
            if path in counts or len(counts) < MAX_PATHS:
                counts[path] = counts.get(path, 0) + 1
        return {"kind": "xml", "root": doc.local_name, "namespace": doc.namespace, "paths": counts}
    return {"kind": "tabular", "format": doc.format, "tables": {t.name: t.columns for t in doc.tables}}


@dataclass
class Candidate:
    kind: str  # reuse_mapper | schema
    score: float
    signature_id: int | None = None
    fingerprint: str | None = None
    mapper_version_id: int | None = None
    schema_id: str | None = None
    table: str | None = None
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    jaccard: float = 0.0

    def summary(self) -> str:
        if self.kind == "reuse_mapper":
            return f"looks like {self.fingerprint} → reuse mapper {self.mapper_version_id}"
        parts = [f"{self.score:.2f}"]
        if self.extra:
            parts.append("extra: " + ", ".join(self.extra))
        if self.missing:
            parts.append("missing: " + ", ".join(self.missing))
        return f"looks like {self.schema_id} ({'; '.join(parts)})"

    def to_dict(self) -> dict:
        return asdict(self)


def match(sig: Signature, prof: dict, registry, artifacts, *, limit: int = 5, min_score: float = 0.5) -> list[Candidate]:
    candidates = [
        Candidate("reuse_mapper", 1.0, signature_id=r.signature_id, fingerprint=r.fingerprint,
                  mapper_version_id=r.mapper_version_id)
        for r in registry.find_active_by_physical_part(sig.physical_part, sig.xml_namespace)
        if r.fingerprint != sig.fingerprint
    ]
    if prof["kind"] == "tabular":
        schemas = [(a.artifact_key, a.definition["columns"]) for a in artifacts.active_schemas()]
        for table_name, columns in prof["tables"].items():
            observed = set(columns)
            if not observed:
                continue
            for schema_id, schema_columns in schemas:
                canonical = {normalize_name(c) for c in schema_columns}
                common = observed & canonical
                score = round(len(common) / len(observed), 2)
                if score < min_score:
                    continue
                candidates.append(Candidate(
                    "schema", score, schema_id=schema_id, table=table_name,
                    matched=sorted(common), missing=sorted(canonical - observed), extra=sorted(observed - canonical),
                    jaccard=round(len(common) / len(observed | canonical), 2),
                ))
    candidates.sort(key=lambda c: (-c.score, -c.jaccard, c.kind != "reuse_mapper", c.fingerprint or c.schema_id or ""))
    return candidates[:limit]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_profiler.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/profiler.py tests/support.py tests/test_profiler.py
git commit -m "$(cat <<'EOF'
Add the deterministic miss profiler and "looks like" matcher

Unknown-but-readable files get ranked candidates (template reuse across
projects, column overlap with schemas) without any model in the loop.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Row pre-check and the loader (stage with bisection, promote)

**Files:**
- Create: `src/importer/precheck.py`
- Rewrite: `src/importer/loader.py`
- Test: `tests/test_precheck_loader.py`

**Interfaces:**
- Consumes: `LandRow`, `RejectRow` (Task 3); `RawZone`, `RowDataError`, `StagedRow` (Task 9); `MemoryStore`, `RawFault` (Task 9).
- Produces:
  - In `precheck`: `MAX_ROW_IDENTITY = 256`, `storable_identity(rid) -> str`, `CheckedRow(row, extras_json)`, and `PreCheck().check(row: LandRow) -> CheckedRow | RejectRow`. Reason codes: `row_identity_too_long`, `duplicate_row_identity`, `unencodable_value`, `extras_not_serializable`.
  - In `loader`: `DEFAULT_BATCH_SIZE` (the Task 2 decision; 5000 by default), `stage(raw, file_id, rows_by_target, batch_size=DEFAULT_BATCH_SIZE) -> list[RejectRow]` (reason `db_insert_error` for rows isolated by bisection), and `promote(raw, file_id, targets) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_precheck_loader.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_precheck_loader.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.precheck'`.

- [ ] **Step 3: Implement `src/importer/precheck.py`**

```python
"""Per-row landability pre-check before staging (design §8.10, layer 1).

Turns anything SQL Server would reject into a RejectRow up front, so batched
inserts never fail on data in the normal case.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import LandRow, RejectRow

MAX_ROW_IDENTITY = 256


def _encodable(value: object) -> bool:
    if value is None:
        return True
    try:
        str(value).encode("utf-16-le")
        return True
    except UnicodeEncodeError:
        return False


def storable_identity(rid: str) -> str:
    """rid itself if it fits NVARCHAR(256) and is encodable, else a stable hash."""
    if len(rid) <= MAX_ROW_IDENTITY and _encodable(rid):
        return rid
    return "h:" + hashlib.sha256(rid.encode("utf-8", "surrogatepass")).hexdigest()


def _payload(row: LandRow) -> str:
    return json.dumps(
        {"row_identity": row.row_identity, "fields": row.fields, "extras": row.extras},
        ensure_ascii=True, default=repr,
    )


@dataclass(frozen=True)
class CheckedRow:
    row: LandRow
    extras_json: str | None


class PreCheck:
    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def check(self, row: LandRow) -> CheckedRow | RejectRow:
        rid = row.row_identity
        if len(rid) > MAX_ROW_IDENTITY:
            return RejectRow(row.target, storable_identity(rid), _payload(row), "row_identity_too_long",
                             f"{len(rid)} characters: {rid[:200]}")
        key = (row.target, rid)
        occurrence = self._seen.get(key, 0)
        self._seen[key] = occurrence + 1
        if occurrence:
            return RejectRow(row.target, storable_identity(f"{rid}#dup{occurrence}"), _payload(row),
                             "duplicate_row_identity", f"occurrence {occurrence + 1} of {rid}")
        if not all(_encodable(v) for v in (rid, *row.fields.keys(), *row.fields.values())):
            return RejectRow(row.target, storable_identity(rid), _payload(row), "unencodable_value",
                             "a value contains characters SQL Server cannot store")
        extras_json = None
        if row.extras:
            try:
                extras_json = json.dumps(row.extras, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                return RejectRow(row.target, storable_identity(rid), _payload(row), "extras_not_serializable", str(exc))
            if not _encodable(extras_json):
                return RejectRow(row.target, storable_identity(rid), _payload(row), "unencodable_value",
                                 "extras contain characters SQL Server cannot store")
        return CheckedRow(row, extras_json)
```

- [ ] **Step 4: Rewrite `src/importer/loader.py`**

```python
"""Loader — stage with bisection, then promote (design §8.10).

Staging tables are all-NVARCHAR(MAX) heaps, so a data failure there is
unexpected; if one happens, the batch is halved recursively until the bad
row(s) are isolated as rejects. Infrastructure errors propagate (file failed).
"""

from __future__ import annotations

import json

from .models import RejectRow
from .repositories import RawZone, RowDataError, StagedRow

DEFAULT_BATCH_SIZE = 5000  # Task 2 spike decision


def _payload(row: StagedRow) -> str:
    return json.dumps(
        {"row_identity": row.row_identity, "fields": row.fields, "extras": row.extras_json},
        ensure_ascii=True, default=repr,
    )


def stage(raw: RawZone, file_id: int, rows_by_target: dict[str, list[StagedRow]], batch_size: int = DEFAULT_BATCH_SIZE) -> list[RejectRow]:
    rejects: list[RejectRow] = []
    for target, rows in rows_by_target.items():
        raw.clear_stage(file_id, target)
        for start in range(0, len(rows), batch_size):
            _stage_bisect(raw, target, rows[start:start + batch_size], rejects)
    return rejects


def _stage_bisect(raw: RawZone, target: str, batch: list[StagedRow], rejects: list[RejectRow]) -> None:
    if not batch:
        return
    try:
        raw.stage_batch(target, batch)
        return
    except RowDataError as exc:
        if len(batch) == 1:
            rejects.append(RejectRow(target, batch[0].row_identity, _payload(batch[0]), "db_insert_error", str(exc)))
            return
    middle = len(batch) // 2
    _stage_bisect(raw, target, batch[:middle], rejects)
    _stage_bisect(raw, target, batch[middle:], rejects)


def promote(raw: RawZone, file_id: int, targets) -> int:
    return sum(raw.promote(file_id, target) for target in targets)
```

If Task 2 chose a different batch size, set `DEFAULT_BATCH_SIZE` to it here.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_precheck_loader.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/importer/precheck.py src/importer/loader.py tests/test_precheck_loader.py
git commit -m "$(cat <<'EOF'
Add row pre-checks and the bisecting loader

Rows the database would reject become rejects before batching; any data
error that slips through is isolated by bisection instead of sinking a batch.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: The pipeline — `process()` and `retry()`

**Files:**
- Rewrite: `src/importer/pipeline.py`
- Create: `tests/fixtures/events.ndjson`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 3–15.
- Produces:
  - `pipeline.process(path, *, project, cls, file_type, source, context=None, store, batch_size=DEFAULT_BATCH_SIZE, plugins_dir=PLUGINS_DIR) -> FileResult`.
  - `pipeline.retry(file_id, *, store, batch_size=DEFAULT_BATCH_SIZE, plugins_dir=PLUGINS_DIR) -> FileResult`.
  - `pipeline.READER_REJECT_CODES`.
  - Failure reasons written to `ledger.last_error` (format `"<reason>: <ExcType>: <message>"`): `mapping_failed`, `stage_failed`, `promote_failed`, `conservation_violation`, `complete_failed`, and the `MapperLoadError` codes.

- [ ] **Step 1: Create the NDJSON fixture `tests/fixtures/events.ndjson`**

```
{"id": "1", "name": "alpha", "amount": "10"}
{"id": "2", "name": "beta", "amount": "20"}
this line is not json
{"id": "3", "name": "gamma", "amount": "30"}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_pipeline.py`:

```python
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from importer.admin import register_signature
from importer.memory_store import MemoryStore, RawFault
from importer.models import Declared, FileStatus
from importer.pipeline import process, retry
from tests.support import (
    EVENT_SCHEMA, EVENT_SPEC, URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, fixture, load_json, setup_signature,
)

REQUEST = {"serffbindername": "ABCD-DC25-1", "run_id": "42"}


def _formulary_store() -> tuple[MemoryStore, int]:
    store = MemoryStore()
    mapper_id = setup_signature(
        store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
        load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary",
    )
    return store, mapper_id


def _run(store, path, project="DC", cls="PC", file_type="XML", context=None):
    return process(path, project=project, cls=cls, file_type=file_type, source="test", context=context, store=store)


def _me_copy(tmp_path: Path) -> Path:
    path = tmp_path / "formulary_me.xml"
    path.write_bytes(fixture("formulary_dc.xml").read_bytes() + b"\n<!-- ME delivery -->\n")
    return path


def test_known_formulary_lands_into_three_raw_tables():
    store, _ = _formulary_store()
    result = _run(store, fixture("formulary_dc.xml"), context=REQUEST)
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.rows_landed, result.rows_rejected) == (10, 10, 0)
    assert result.percent_loaded == 100.0 and result.percent_parsed == 100.0
    raw = store.state.raw
    assert {t: len(rows) for t, rows in raw.items()} == {
        "formulary_benefit_definition": 5, "formulary_tier_definition": 2, "formulary_drug_list": 3,
    }
    first = next(iter(raw["formulary_benefit_definition"].values()))
    assert first.fields["serffbindername"] == "ABCD-DC25-1"
    ledger = store.state.ledger[result.file_id]
    assert ledger.signature_id is not None and ledger.encoding == "UTF-8"
    assert result.to_dict()["status"] == "completed"


def test_unknown_project_is_quarantined_with_a_reuse_proposal(tmp_path):
    store, mapper_id = _formulary_store()
    result = _run(store, _me_copy(tmp_path), project="ME")
    assert result.status is FileStatus.QUARANTINED
    assert result.quarantine_reason == "unroutable"
    assert result.proposal_summary == f"looks like DC|PC|XML|prescriptionDrugTemplateVO → reuse mapper {mapper_id}"
    assert store.state.quarantine[-1]["reason_code"] == "unroutable"
    assert store.state.decisions[-1]["action_type"] == "profile_match"
    assert result.percent_loaded == 0.0


def test_binding_then_retry_completes_the_parked_file(tmp_path):
    store, mapper_id = _formulary_store()
    path = _me_copy(tmp_path)
    parked = _run(store, path, project="ME")
    with store.transaction() as tx:
        register_signature(tx, path.read_bytes(), Declared.normalize("ME", "PC", "XML"), mapper_id, approved_by="test")
    result = retry(parked.file_id, store=store)
    assert result.status is FileStatus.COMPLETED
    assert result.percent_loaded == 100.0
    assert store.state.ledger[parked.file_id].attempts == 2


def test_two_csv_variants_land_in_one_schema():
    store = MemoryStore()
    setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    setup_signature(store, fixture("url_template_b.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_B, "urls_b")
    a = _run(store, fixture("url_template_a.csv"), project="VT", file_type="CSV", context={"run_id": "7"})
    b = _run(store, fixture("url_template_b.csv"), project="VT", file_type="CSV", context={"run_id": "7"})
    assert a.status is b.status is FileStatus.COMPLETED
    rows = store.state.raw["url_template"]
    assert len(rows) == 4
    assert {r.fields["newurl"] for r in rows.values()} == {
        "https://example.org/sbc/1", "https://example.org/pay", "https://example.net/sbc/9", "https://example.net/pay",
    }
    assert store.state.ledger[a.file_id].signature_id != store.state.ledger[b.file_id].signature_id


def test_bad_ndjson_line_completes_with_failures():
    store = MemoryStore()
    setup_signature(store, fixture("events.ndjson"), Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    result = _run(store, fixture("events.ndjson"), cls="PD", file_type="NDJSON")
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.rows_landed, result.rows_rejected) == (4, 3, 1)
    assert result.percent_loaded == 75.0 and result.percent_parsed == 75.0
    [reject] = store.state.rejects.all_rejects()
    assert (reject.reason_code, reject.row_identity, reject.schema_id) == ("invalid_json_line", "L3", "event")


def test_byte_identical_redelivery_returns_the_same_file():
    store, _ = _formulary_store()
    first = _run(store, fixture("formulary_dc.xml"))
    again = _run(store, fixture("formulary_dc.xml"))
    assert again.file_id == first.file_id
    assert again.status is FileStatus.COMPLETED
    assert len(store.state.raw["formulary_drug_list"]) == 3


def test_corrupt_and_mislabelled_files_are_quarantined(tmp_path):
    store = MemoryStore()
    corrupt = tmp_path / "bad.xml"
    corrupt.write_bytes(b"<a><b></a>")
    assert _run(store, corrupt).quarantine_reason == "corrupt"
    mislabelled = tmp_path / "data.csv"
    mislabelled.write_bytes(b"a,b\n1,2\n")
    assert _run(store, mislabelled, file_type="XML").quarantine_reason == "unparseable"


def test_broken_mapper_artifact_fails_the_file():
    store, mapper_id = _formulary_store()
    store.state.artifacts[mapper_id].definition = {"kind": "bogus"}
    result = _run(store, fixture("formulary_dc.xml"))
    assert result.status is FileStatus.FAILED
    assert store.state.ledger[result.file_id].last_error.startswith("unknown_mapper_kind")


def test_stage_infrastructure_failure_then_retry():
    store, _ = _formulary_store()
    store.fault = RawFault(infra_on_stage=True)
    failed = _run(store, fixture("formulary_dc.xml"))
    assert failed.status is FileStatus.FAILED
    assert store.state.ledger[failed.file_id].last_error.startswith("stage_failed")
    assert store.state.raw == {}
    store.fault = None
    result = retry(failed.file_id, store=store)
    assert result.status is FileStatus.COMPLETED and result.rows_landed == 10


def test_promote_failure_rolls_back_and_fails():
    store, _ = _formulary_store()
    store.fault = RawFault(infra_on_promote=True)
    result = _run(store, fixture("formulary_dc.xml"))
    assert result.status is FileStatus.FAILED
    assert store.state.ledger[result.file_id].last_error.startswith("promote_failed")
    assert store.state.rejects.all_rejects() == []


def test_database_data_error_becomes_a_reject():
    store = MemoryStore()
    setup_signature(store, fixture("events.ndjson"), Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    store.fault = RawFault(bad_identities={"L2"})
    result = _run(store, fixture("events.ndjson"), cls="PD", file_type="NDJSON")
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_landed, result.rows_rejected) == (2, 2)
    assert sorted(r.reason_code for r in store.state.rejects.all_rejects()) == ["db_insert_error", "invalid_json_line"]


def test_header_only_csv_completes_with_zero_rows(tmp_path):
    store = MemoryStore()
    setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"URL ID,New URL,URL Type\n")
    result = _run(store, empty, project="VT", file_type="CSV")
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.percent_loaded) == (0, 0.0)


line = st.one_of(
    st.builds(lambda i: '{"id": "%d", "name": "n", "amount": "1"}' % i, st.integers(0, 10**6)),
    st.sampled_from(["oops", "{not json", "[1, 2]"]),
)


@settings(max_examples=40, deadline=None)
@given(st.lists(line, min_size=1, max_size=30))
def test_conservation_landed_plus_rejected_equals_total(lines):
    store = MemoryStore()
    setup_signature(store, fixture("events.ndjson"), Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    body = '{"id": "seed", "name": "n", "amount": "1"}\n' + "\n".join(lines) + "\n"
    fd, name = tempfile.mkstemp(suffix=".ndjson")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode())
        result = _run(store, name, cls="PD", file_type="NDJSON")
    finally:
        os.unlink(name)
    assert result.status is FileStatus.COMPLETED
    assert result.rows_landed + result.rows_rejected == result.rows_total
```

The conservation test always starts with a valid object line. The NDJSON declared check needs the first line to be an object, and every key set must include the signature's keys.

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL, because the current `pipeline.process` raises `NotImplementedError` / has the wrong signature.

- [ ] **Step 4: Rewrite `src/importer/pipeline.py`**

```python
"""Fast-path orchestrator — the state machine over the ledger (design §8.3).

Each step is its own transaction and moves the ledger exactly one status:
discovered → detected → landed → promoted → completed, or → quarantined /
failed. A failed step rolls back its own writes; the file is then marked
failed (control-plane problem) and can re-enter via retry(). The model is
never in this path.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import replace
from pathlib import Path

from .detector import detect
from .loader import DEFAULT_BATCH_SIZE, promote, stage
from .mappers.base import RunContext
from .mappers.loading import PLUGINS_DIR, MapperLoadError, load_mapper
from .metrics import compute_percent
from .models import Declared, FileResult, FileStatus, Reject, RejectRow
from .precheck import PreCheck, storable_identity
from .profiler import match, profile
from .readers import parse_document
from .readers.base import ReaderError
from .repositories import LedgerRow, StagedRow, Store

log = logging.getLogger("importer")
READER_REJECT_CODES = frozenset({"field_count_mismatch", "invalid_json_line", "invalid_json_record"})


class _Stop(Exception):
    def __init__(self, result: FileResult):
        super().__init__(result.status.value)
        self.result = result


class _ConservationError(Exception):
    pass


def process(path, *, project: str, cls: str, file_type: str, source: str, context: dict | None = None,
            store: Store, batch_size: int = DEFAULT_BATCH_SIZE, plugins_dir: Path = PLUGINS_DIR) -> FileResult:
    declared = Declared.normalize(project, cls, file_type)
    source_path = Path(path)
    data = source_path.read_bytes()
    key = hashlib.sha256(data).hexdigest()
    with store.transaction() as tx:
        row, created = tx.ledger.create_or_get(
            idempotency_key=key, source=source, original_filename=source_path.name,
            source_path=str(source_path.resolve()), declared=declared, request_context=context,
        )
    if not created:
        log.info("file_id=%s re-delivery of a known file (status=%s); nothing re-landed", row.file_id, row.status.value)
        return _result_from_ledger(row)
    log.info("file_id=%s discovered %s as %s", row.file_id, source_path.name, declared.prefix)
    return _Run(store, row.file_id, data, declared, context or {}, batch_size, plugins_dir).execute()


def retry(file_id: int, *, store: Store, batch_size: int = DEFAULT_BATCH_SIZE, plugins_dir: Path = PLUGINS_DIR) -> FileResult:
    with store.transaction() as tx:
        row = tx.ledger.get(file_id)
    data = Path(row.source_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != row.idempotency_key:
        raise ValueError(f"{row.source_path} changed since file {file_id} was discovered; deliver it as a new file")
    with store.transaction() as tx:
        row = tx.ledger.begin_reentry(file_id)
    log.info("file_id=%s re-entry attempt %s", file_id, row.attempts)
    return _Run(store, file_id, data, row.declared, row.request_context or {}, batch_size, plugins_dir).execute()


def _result_from_ledger(row: LedgerRow) -> FileResult:
    return FileResult(
        file_id=row.file_id, status=row.status, rows_total=row.rows_total or 0, rows_landed=row.rows_landed or 0,
        rows_rejected=row.rows_rejected or 0, percent_parsed=row.percent_parsed, percent_loaded=row.percent_loaded,
    )


class _Run:
    def __init__(self, store: Store, file_id: int, data: bytes, declared: Declared, context: dict,
                 batch_size: int, plugins_dir: Path):
        self.store = store
        self.file_id = file_id
        self.data = data
        self.declared = declared
        self.context = context
        self.batch_size = batch_size
        self.plugins_dir = plugins_dir
        self.status = FileStatus.DISCOVERED
        self.started = time.perf_counter()

    # ---- helpers ---------------------------------------------------------------

    def _step(self, dst: FileStatus, work=None):
        """Run ``work(tx)`` and the status change in one transaction."""
        with self.store.transaction() as tx:
            result = work(tx) if work else None
            tx.ledger.transition(self.file_id, self.status, dst)
        log.info("file_id=%s %s -> %s %dms", self.file_id, self.status.value, dst.value,
                 int((time.perf_counter() - self.started) * 1000))
        self.status = dst
        return result

    def _result(self) -> FileResult:
        with self.store.transaction() as tx:
            return _result_from_ledger(tx.ledger.get(self.file_id))

    def _fail(self, reason: str, exc: BaseException | None = None) -> FileResult:
        message = f"{reason}: {type(exc).__name__}: {exc}" if exc else reason
        log.error("file_id=%s failed: %s", self.file_id, message)
        self._step(FileStatus.FAILED, lambda tx: tx.ledger.set_error(self.file_id, message))
        return self._result()

    def _zero_metrics(self, tx) -> None:
        tx.ledger.record_metrics(self.file_id, rows_total=0, rows_landed=0, rows_rejected=0,
                                 percent_parsed=0.0, percent_loaded=0.0)

    def _quarantine(self, reason_code: str, detail: str) -> FileResult:
        def work(tx):
            tx.audit.quarantine(self.file_id, reason_code, detail, None)
            self._zero_metrics(tx)
        self._step(FileStatus.QUARANTINED, work)
        return replace(self._result(), quarantine_reason=reason_code)

    # ---- the state machine -------------------------------------------------------

    def execute(self) -> FileResult:
        try:
            doc = self._read()
            with self.store.transaction() as tx:
                sig, hit = detect(doc, self.declared, tx.registry)
            if hit is None:
                return self._unroutable(doc, sig)
            return self._land(doc, hit)
        except _Stop as stop:
            return stop.result

    def _read(self):
        try:
            doc = parse_document(self.data, self.declared.file_type)
        except ReaderError as exc:
            raise _Stop(self._quarantine(exc.reason_code, exc.detail)) from exc
        with self.store.transaction() as tx:
            tx.ledger.set_encoding(self.file_id, doc.encoding)
        return doc

    def _unroutable(self, doc, sig) -> FileResult:
        prof = profile(doc)
        with self.store.transaction() as tx:
            candidates = match(sig, prof, tx.registry, tx.artifacts)
        summary = candidates[0].summary() if candidates else "no similar signature or schema found"
        proposal = [c.to_dict() for c in candidates]

        def work(tx):
            signature_id = tx.registry.propose(sig, proposal)
            tx.ledger.set_signature(self.file_id, signature_id)
            tx.audit.decision(self.file_id, "profile_match", observed=prof,
                              proposed={"signature": sig.fingerprint, "candidates": proposal},
                              applied=False, approval=None)
            tx.audit.quarantine(self.file_id, "unroutable", summary, signature_id)
            self._zero_metrics(tx)

        self._step(FileStatus.QUARANTINED, work)
        return replace(self._result(), quarantine_reason="unroutable", proposal_summary=summary)

    def _land(self, doc, hit) -> FileResult:
        self._step(FileStatus.DETECTED, lambda tx: tx.ledger.set_signature(self.file_id, hit.signature_id))
        try:
            with self.store.transaction() as tx:
                artifact = tx.artifacts.get(hit.mapper_version_id)
                schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
            mapper = load_mapper(artifact, known_schemas=set(schemas), plugins_dir=self.plugins_dir)
        except MapperLoadError as exc:
            return self._fail(exc.reason_code, exc)
        targets = mapper.targets
        ctx = RunContext(self.file_id, hit.signature_id, artifact.version_id, self.context,
                         {t: schemas[t] for t in targets})
        try:
            emitted = list(mapper.extract(doc, ctx))
        except Exception as exc:  # spec/doc mismatch or a plugin bug: configuration problem
            return self._fail("mapping_failed", exc)

        precheck = PreCheck()
        staged: dict[str, list[StagedRow]] = {t: [] for t in targets}
        rejects: list[RejectRow] = []
        reader_rejects = 0
        for item in emitted:
            if isinstance(item, RejectRow):
                rejects.append(item)
                reader_rejects += item.reason_code in READER_REJECT_CODES
                continue
            if item.target not in staged:
                return self._fail("mapping_failed", ValueError(f"mapper emitted undeclared target {item.target!r}"))
            checked = precheck.check(item)
            if isinstance(checked, RejectRow):
                rejects.append(checked)
                continue
            staged[item.target].append(StagedRow(self.file_id, item.row_identity, hit.signature_id,
                                                 artifact.version_id, item.fields, checked.extras_json))
        total = len(emitted)

        def stage_work(tx):
            for target in targets:
                tx.raw.ensure_tables(target, schemas[target])
            return stage(tx.raw, self.file_id, staged, self.batch_size)

        try:
            rejects.extend(self._step(FileStatus.LANDED, stage_work))
        except Exception as exc:
            return self._fail("stage_failed", exc)

        reject_rows = self._unique_rejects(rejects)

        def promote_work(tx):
            promote(tx.raw, self.file_id, targets)
            for reject in reject_rows:
                tx.rejects.add(reject)

        try:
            self._step(FileStatus.PROMOTED, promote_work)
        except Exception as exc:
            return self._fail("promote_failed", exc)

        def complete_work(tx):
            landed = sum(tx.raw.count_landed(self.file_id, t) for t in targets)
            rejected = tx.rejects.count_open_for_file(self.file_id)
            if landed + rejected != total:
                raise _ConservationError(f"landed {landed} + rejected {rejected} != total {total}")
            tx.ledger.record_metrics(
                self.file_id, rows_total=total, rows_landed=landed, rows_rejected=rejected,
                percent_parsed=compute_percent(total - reader_rejects, total),
                percent_loaded=compute_percent(landed, total),
            )

        try:
            self._step(FileStatus.COMPLETED, complete_work)
        except _ConservationError as exc:
            return self._fail("conservation_violation", exc)
        except Exception as exc:
            return self._fail("complete_failed", exc)
        return self._result()

    def _unique_rejects(self, rejects: list[RejectRow]) -> list[Reject]:
        out: list[Reject] = []
        used: set[str] = set()
        for reject in rejects:
            identity = storable_identity(reject.row_identity)
            if identity in used:
                n = 2
                while storable_identity(f"{reject.row_identity}#{n}") in used:
                    n += 1
                identity = storable_identity(f"{reject.row_identity}#{n}")
            used.add(identity)
            out.append(Reject(file_id=self.file_id, row_identity=identity, raw_payload=reject.raw_payload,
                              reason_code=reject.reason_code, schema_id=reject.target, reason_detail=reject.detail))
        return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py -v`
Expected: all passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/importer/pipeline.py tests/fixtures/events.ndjson tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
Implement the fast-path state machine with quarantine, failure and retry

Each status change is its own transaction; conservation (landed + rejected
= total) is checked before a file completes, and parked files re-enter.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---
### Task 17: Control-plane DDL and raw/stage DDL generation

**Files:**
- Rewrite: `sql/001_schema.sql`
- Create: `src/importer/schema_tables.py`
- Test: `tests/test_schema_tables.py`
- Confluence: page 3775135746 (*Schema DDL (MSSQL)*)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - In `schema_tables`:
    - `IDENT`, `LINEAGE_COLUMNS = ["_file_id", "_row_identity", "_signature_id", "_artifact_version_id", "_extras"]`.
    - `q(name) -> str` (validated, bracket-quoted), `validate_identifier(name)`, `validate_columns(columns)`.
    - `create_raw_sql(schema_id, columns) -> str`, `create_stage_sql(schema_id, columns) -> str`, `add_column_sql(schema, table, column) -> str`.
    - `ENSURE_SCHEMAS_SQL`, `information_schema_sql(database) -> str` (two `?` params: schema, table), `REBUILD_DROP_SQL`.
  - `sql/001_schema.sql` with the §8.11 columns, plus `file_ledger.last_error`.

- [ ] **Step 1: Write the failing tests**

`tests/test_schema_tables.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from importer.schema_tables import (
    add_column_sql, create_raw_sql, create_stage_sql, information_schema_sql, q, validate_columns,
)

DDL = (Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql").read_text(encoding="utf-8")


def test_raw_table_has_lineage_canonical_columns_and_pk():
    sql = create_raw_sql("url_template", ["urlid", "newurl"])
    assert sql.startswith("CREATE TABLE raw.[url_template] (")
    for fragment in ("[_file_id] BIGINT NOT NULL", "[_row_identity] NVARCHAR(256) NOT NULL",
                     "[_extras] NVARCHAR(MAX) NULL", "[_landed_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()",
                     "[urlid] NVARCHAR(MAX) NULL",
                     "CONSTRAINT [pk_raw_url_template] PRIMARY KEY CLUSTERED ([_file_id], [_row_identity])"):
        assert fragment in sql


def test_stage_table_is_a_heap_without_landed_at():
    sql = create_stage_sql("url_template", ["urlid"])
    assert sql.startswith("CREATE TABLE stage.[url_template] (")
    assert "PRIMARY KEY" not in sql and "_landed_at" not in sql


def test_add_column_and_information_schema_sql():
    assert add_column_sql("raw", "t", "newcol") == "ALTER TABLE raw.[t] ADD [newcol] NVARCHAR(MAX) NULL"
    assert information_schema_sql("legacy_db") == (
        "SELECT COLUMN_NAME FROM [legacy_db].INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION"
    )


@pytest.mark.parametrize("name", ["bad name", "x];DROP TABLE y;--", ""])
def test_identifiers_are_validated(name):
    with pytest.raises(ValueError):
        q(name)


def test_columns_reject_reserved_and_duplicates():
    with pytest.raises(ValueError, match="reserved"):
        validate_columns(["_extras"])
    with pytest.raises(ValueError, match="duplicate"):
        validate_columns(["a", "A"])


@pytest.mark.parametrize("fragment", [
    "'schema','mapper','rule'", "artifact_key      NVARCHAR(128) NOT NULL", "content_sha256",
    "mapper_version_id BIGINT NULL REFERENCES dbo.artifact_versions(version_id)", "physical_part",
    "xml_namespace", "observed_schema", "[class]", "attempts          INT NOT NULL DEFAULT 1",
    "last_error", "request_context", "signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id)",
])
def test_control_plane_ddl_has_m2a_columns(fragment):
    assert fragment in DDL


def test_ddl_creates_referenced_tables_first():
    assert DDL.index("CREATE TABLE dbo.artifact_versions") < DDL.index("CREATE TABLE dbo.signature_registry") < DDL.index("CREATE TABLE dbo.file_ledger")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_schema_tables.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.schema_tables'`.

- [ ] **Step 3: Implement `src/importer/schema_tables.py`**

```python
"""Raw/stage DDL generation from schema definitions (design §8.10, §8.11).

Stable code generates every physical table from versioned schema data; the
agent never writes DDL. Identifiers are validated and bracket-quoted.
"""

from __future__ import annotations

import re

IDENT = re.compile(r"^[A-Za-z0-9_]+$")
LINEAGE_COLUMNS = ["_file_id", "_row_identity", "_signature_id", "_artifact_version_id", "_extras"]
_LINEAGE_TYPES = {
    "_file_id": "BIGINT NOT NULL",
    "_row_identity": "NVARCHAR(256) NOT NULL",
    "_signature_id": "BIGINT NOT NULL",
    "_artifact_version_id": "BIGINT NOT NULL",
    "_extras": "NVARCHAR(MAX) NULL",
}

ENSURE_SCHEMAS_SQL = (
    "IF SCHEMA_ID(N'raw') IS NULL EXEC(N'CREATE SCHEMA raw'); "
    "IF SCHEMA_ID(N'stage') IS NULL EXEC(N'CREATE SCHEMA stage');"
)

REBUILD_DROP_SQL = """
DECLARE @sql NVARCHAR(MAX) = N'';
SELECT @sql += N'DROP TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N';'
FROM sys.tables AS t JOIN sys.schemas AS s ON s.schema_id = t.schema_id
WHERE s.name IN (N'raw', N'stage');
EXEC sp_executesql @sql;
IF SCHEMA_ID(N'raw') IS NOT NULL EXEC(N'DROP SCHEMA raw');
IF SCHEMA_ID(N'stage') IS NOT NULL EXEC(N'DROP SCHEMA stage');
DROP TABLE IF EXISTS dbo.decision_log, dbo.file_quarantine, dbo.reject_repository,
    dbo.file_ledger, dbo.signature_registry, dbo.artifact_versions;
"""


def validate_identifier(name: str, what: str = "identifier") -> None:
    if not isinstance(name, str) or not IDENT.match(name):
        raise ValueError(f"{what} {name!r} must match [A-Za-z0-9_]+")


def q(name: str) -> str:
    validate_identifier(name)
    return f"[{name}]"


def validate_columns(columns: list[str]) -> None:
    seen: set[str] = set()
    for column in columns:
        validate_identifier(column, "column")
        if column.startswith("_"):
            raise ValueError(f"column {column!r}: names starting with '_' are reserved for lineage")
        if column.lower() in seen:
            raise ValueError(f"duplicate column {column!r} (SQL Server names are case-insensitive)")
        seen.add(column.lower())


def _columns_sql(columns: list[str]) -> list[str]:
    validate_columns(columns)
    return [f"{q(c)} NVARCHAR(MAX) NULL" for c in columns]


def create_raw_sql(schema_id: str, columns: list[str]) -> str:
    parts = [f"{q(n)} {_LINEAGE_TYPES[n]}" for n in LINEAGE_COLUMNS]
    parts.append("[_landed_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
    parts += _columns_sql(columns)
    parts.append(f"CONSTRAINT {q('pk_raw_' + schema_id)} PRIMARY KEY CLUSTERED ([_file_id], [_row_identity])")
    return f"CREATE TABLE raw.{q(schema_id)} (\n    " + ",\n    ".join(parts) + "\n)"


def create_stage_sql(schema_id: str, columns: list[str]) -> str:
    parts = [f"{q(n)} {_LINEAGE_TYPES[n]}" for n in LINEAGE_COLUMNS] + _columns_sql(columns)
    return f"CREATE TABLE stage.{q(schema_id)} (\n    " + ",\n    ".join(parts) + "\n)"


def add_column_sql(schema: str, table: str, column: str) -> str:
    if schema not in ("raw", "stage"):
        raise ValueError(f"schema must be raw or stage, got {schema!r}")
    return f"ALTER TABLE {schema}.{q(table)} ADD {q(column)} NVARCHAR(MAX) NULL"


def information_schema_sql(database: str) -> str:
    return (
        f"SELECT COLUMN_NAME FROM {q(database)}.INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION"
    )
```

- [ ] **Step 4: Rewrite `sql/001_schema.sql`**

```sql
/* ============================================================================
   Data Agent: Importer — control-plane schema (MSSQL / T-SQL)
   Maps to design record §2 (five tables), §3 status lifecycle, §8.11 (M2a).
   Raw landing-zone tables are PER SCHEMA (raw.<schema_id>, stage.<schema_id>)
   and are generated by the importer from versioned schema definitions.

   Authoritative copy. Mirrors the "Schema DDL (MSSQL)" Confluence page
   (pages/3775135746). Keep the two in sync.
   Prototype policy (§8.11): this create script is edited in place and git is
   its history. `importer db rebuild` (DEV only) drops and re-creates it all.
   ========================================================================== */

/* 3. ARTIFACT VERSIONS — schema, mapper & repair-rule versions; never edited in place. §2,§5,§8.9-8.11 */
CREATE TABLE dbo.artifact_versions (
    version_id        BIGINT IDENTITY(1,1) PRIMARY KEY,
    artifact_type     VARCHAR(8) NOT NULL
        CONSTRAINT ck_artifact_type CHECK (artifact_type IN ('schema','mapper','rule')),
    artifact_key      NVARCHAR(128) NOT NULL,          -- schema id (schema/rule) | mapper name (mapper)
    version_no        INT NOT NULL,
    definition        NVARCHAR(MAX) NOT NULL,          -- JSON: column list | mapping spec | rule-as-data
    is_active         BIT NOT NULL DEFAULT 0,
    content_sha256    CHAR(64) NULL,                   -- plugin mappers: pinned file hash
    created_by        NVARCHAR(128) NULL,              -- 'agent' | user
    approved_by       NVARCHAR(128) NULL,
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_artifact UNIQUE (artifact_type, artifact_key, version_no)
);

/* 2. SIGNATURE REGISTRY — PROJECT|CLASS|FILE_TYPE|<physical> -> mapper version. §2,§4,§8.4,§8.6 */
CREATE TABLE dbo.signature_registry (
    signature_id      BIGINT IDENTITY(1,1) PRIMARY KEY,
    fingerprint       NVARCHAR(512) NOT NULL,          -- e.g. DC|PC|XML|prescriptionDrugTemplateVO
    physical_part     NVARCHAR(256) NOT NULL,          -- XML root local name | h:<16 hex>
    xml_namespace     NVARCHAR(512) NULL,              -- must also match for an XML hit
    mapper_version_id BIGINT NULL REFERENCES dbo.artifact_versions(version_id),
    status            VARCHAR(16) NOT NULL DEFAULT 'proposed',
        CONSTRAINT ck_sig_status CHECK (status IN ('proposed','active','retired')),
    observed_schema   NVARCHAR(MAX) NULL,              -- JSON: header / sheets / keys / root
    proposal          NVARCHAR(MAX) NULL,              -- JSON: ranked "looks like" candidates
    landing_contract  NVARCHAR(MAX) NULL,              -- JSON: structural landability rules (reserved)
    provenance        NVARCHAR(MAX) NULL,              -- who/what added it, from which file
    approved_by       NVARCHAR(128) NULL,
    approved_at       DATETIME2 NULL,
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_sig_fingerprint UNIQUE (fingerprint)
);
CREATE INDEX ix_sig_physical ON dbo.signature_registry(physical_part);

/* 1. FILE LEDGER — one row per file; trigger source + audit trail + dedupe. §2,§3,§8.3,§8.12 */
CREATE TABLE dbo.file_ledger (
    file_id           BIGINT IDENTITY(1,1) PRIMARY KEY,
    idempotency_key   NVARCHAR(256) NOT NULL,          -- interim: SHA-256 of file bytes (re-delivery open item)
    source            NVARCHAR(128) NOT NULL,
    original_filename NVARCHAR(512) NOT NULL,
    source_path       NVARCHAR(1024) NULL,
    project           NVARCHAR(32) NOT NULL,           -- declared by the requestor (§8.2)
    [class]           NVARCHAR(32) NOT NULL,           -- PC | PD | Other...
    file_type         NVARCHAR(16) NOT NULL,           -- CSV | TSV | PIPE | DELIMITED | EXCEL | JSON | NDJSON | XML
    signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id),
    request_context   NVARCHAR(MAX) NULL,              -- JSON, e.g. serffbindername, run_id
    encoding          VARCHAR(16) NULL,
    status            VARCHAR(16)   NOT NULL DEFAULT 'discovered',
        CONSTRAINT ck_ledger_status CHECK (status IN
            ('discovered','detected','landed','promoted','completed','quarantined','failed')),
    attempts          INT NOT NULL DEFAULT 1,          -- re-entry count (§8.12)
    last_error        NVARCHAR(MAX) NULL,              -- why the file failed
    rows_total        INT NULL,
    rows_landed       INT NULL,
    rows_rejected     INT NULL,
    percent_parsed    DECIMAL(5,2) NULL,               -- §7
    percent_loaded    DECIMAL(5,2) NULL,               -- §7 monotonic floor
    discovered_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_ledger_idem UNIQUE (idempotency_key)
);
CREATE INDEX ix_ledger_status ON dbo.file_ledger(status);

/* 4a. REJECT REPOSITORY — one row per rejected ROW; rows never fail the file. §2,§7,§8.10
   reason codes: field_count_mismatch, invalid_json_line, invalid_json_record, mapping_error,
   unencodable_value, row_identity_too_long, duplicate_row_identity, extras_not_serializable,
   db_insert_error */
CREATE TABLE dbo.reject_repository (
    reject_id         BIGINT IDENTITY(1,1) PRIMARY KEY,
    file_id           BIGINT NOT NULL REFERENCES dbo.file_ledger(file_id),
    schema_id         NVARCHAR(128) NULL,              -- target schema
    row_identity      NVARCHAR(256) NOT NULL,          -- idempotent reclaim key
    raw_payload       NVARCHAR(MAX) NOT NULL,
    reason_code       VARCHAR(64) NOT NULL,            -- for DIAGNOSIS (not matching)
    reason_detail     NVARCHAR(MAX) NULL,
    status            VARCHAR(12) NOT NULL DEFAULT 'rejected'
        CONSTRAINT ck_reject_status CHECK (status IN ('rejected','reclaimed')),
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    reclaimed_at      DATETIME2 NULL,
    CONSTRAINT uq_reject_row UNIQUE (file_id, row_identity)   -- enables idempotent reclaim
);
CREATE INDEX ix_reject_schema_status ON dbo.reject_repository(schema_id, status);

/* 4b. FILE QUARANTINE — file-level, only for files that cannot even start. §2,§3,§8.4 */
CREATE TABLE dbo.file_quarantine (
    quarantine_id     BIGINT IDENTITY(1,1) PRIMARY KEY,
    file_id           BIGINT NOT NULL REFERENCES dbo.file_ledger(file_id),
    reason_code       VARCHAR(64) NOT NULL,            -- unroutable | unparseable | corrupt
    reason_detail     NVARCHAR(MAX) NULL,              -- unroutable: the "looks like" summary
    signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id),
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

/* 5. DECISION LOG — every agent (and profile/match) action, for audit. §2,§8.4 */
CREATE TABLE dbo.decision_log (
    decision_id       BIGINT IDENTITY(1,1) PRIMARY KEY,
    file_id           BIGINT NULL REFERENCES dbo.file_ledger(file_id),
    action_type       NVARCHAR(64) NOT NULL,           -- profile_match | bind_signature | activate_schema |
                                                       -- diagnose | propose_rule | add_signature | reclaim
    observed          NVARCHAR(MAX) NULL,
    proposed          NVARCHAR(MAX) NULL,
    applied           BIT NOT NULL DEFAULT 0,
    approval          NVARCHAR(128) NULL,              -- 'auto' | user id (new-schema gate)
    created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_schema_tables.py -v`
Expected: all passed.

- [ ] **Step 6: Sync the Confluence Schema DDL page**

Use the Atlassian MCP:
1. Fetch page 3775135746 as HTML (`getConfluencePage`, `contentFormat: "html"`).
2. Read the `updateConfluencePage` HTML guide (`getContentFormatGuide`).
3. Replace only the SQL code block's contents with the new `sql/001_schema.sql`.
4. Update the intro sentence to mention per-schema `raw.<schema_id>` / `stage.<schema_id>` tables generated by the importer.
5. Keep everything else, and use version message "M2a control-plane DDL (sync with sql/001_schema.sql)".

- [ ] **Step 7: Commit**

```bash
git add sql/001_schema.sql src/importer/schema_tables.py tests/test_schema_tables.py
git commit -m "$(cat <<'EOF'
Update the control-plane DDL for M2a and add raw/stage DDL generation

Ledger tracks declared parameters, signature, attempts and last_error; the
registry points at mapper versions; artifacts gain schema definitions.
Synced to the Schema DDL Confluence page.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: SQL Server store

**Files:**
- Create: `src/importer/mssql_store.py`, `tests/integration/__init__.py`, `tests/integration/conftest.py`
- Test: `tests/integration/test_mssql_store.py`

**Interfaces:**
- Consumes: the Task 9 Protocols and dataclasses; `schema_tables` (Task 17); `importer.db.get_engine` (Task 1).
- Produces: `mssql_store.MssqlStore(engine)` with `.transaction()` yielding `MssqlTx` (`ledger, registry, artifacts, rejects, raw, audit, conn`); `mssql_store.USE_SETINPUTSIZES` (the Task 2 decision); `mssql_store.DATA_SQLSTATE_CLASSES = ("22", "23")`.

Prerequisite: the DEV database has been built from the Task 17 DDL. Run `importer db rebuild` once Task 19 exists. Until then, run the script once by hand, with the user present:
```bash
.venv/Scripts/python -c "from importer.db import get_engine; from importer.schema_tables import REBUILD_DROP_SQL; ddl = open('sql/001_schema.sql', encoding='utf-8').read(); conn = get_engine().connect(); conn.exec_driver_sql(REBUILD_DROP_SQL); conn.exec_driver_sql(ddl); conn.commit(); print('rebuilt')"
```

- [ ] **Step 1: Create the integration package and fixture**

`tests/integration/__init__.py`: an empty file.

`tests/integration/conftest.py`:

```python
"""Integration fixtures: real DEV SQL Server via database.env (opt-in: pytest -m integration)."""

from __future__ import annotations

import pytest

from importer.db import ConfigError, load_connection_document


@pytest.fixture(scope="session")
def mssql_store():
    try:
        load_connection_document()
    except ConfigError as exc:
        pytest.skip(f"no DEV database configured: {exc}")
    from importer.db import get_engine
    from importer.mssql_store import MssqlStore

    return MssqlStore(get_engine())
```

- [ ] **Step 2: Write the failing integration tests**

`tests/integration/test_mssql_store.py`:

```python
from __future__ import annotations

import uuid

import pytest

from importer.loader import promote, stage
from importer.models import ArtifactType, Declared, FileStatus, Reject, Signature, SignatureStatus
from importer.repositories import ConcurrentTransition, StagedRow

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _create(tx, key):
    return tx.ledger.create_or_get(
        idempotency_key=key, source="it", original_filename="f.csv", source_path="C:/tmp/f.csv",
        declared=Declared.normalize("IT", "PC", "CSV"), request_context={"run_id": "1"},
    )


def test_ledger_roundtrip_floor_and_reentry(mssql_store):
    key = f"it-{_uid()}"
    with mssql_store.transaction() as tx:
        row, created = _create(tx, key)
        assert created and row.status is FileStatus.DISCOVERED and row.request_context == {"run_id": "1"}
        again, created_again = _create(tx, key)
        assert not created_again and again.file_id == row.file_id
        with pytest.raises(ConcurrentTransition):
            tx.ledger.transition(row.file_id, FileStatus.DETECTED, FileStatus.LANDED)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=8, rows_rejected=2, percent_parsed=100.0, percent_loaded=80.0)
        tx.ledger.record_metrics(row.file_id, rows_total=10, rows_landed=5, rows_rejected=5, percent_parsed=100.0, percent_loaded=50.0)
        assert tx.ledger.get(row.file_id).percent_loaded == 80.0
        tx.ledger.transition(row.file_id, FileStatus.DISCOVERED, FileStatus.FAILED)
        tx.ledger.set_error(row.file_id, "boom")
        back = tx.ledger.begin_reentry(row.file_id)
        assert back.status is FileStatus.DISCOVERED and back.attempts == 2 and back.last_error is None


def test_transaction_rolls_back(mssql_store):
    key = f"it-{_uid()}"
    with pytest.raises(RuntimeError):
        with mssql_store.transaction() as tx:
            _create(tx, key)
            raise RuntimeError("boom")
    with mssql_store.transaction() as tx:
        _, created = _create(tx, key)
    assert created


def test_registry_artifacts_and_audit(mssql_store):
    uid = _uid()
    with mssql_store.transaction() as tx:
        v1 = tx.artifacts.put_version(ArtifactType.SCHEMA, f"s_{uid}", {"columns": ["a"]}, created_by="it")
        v2 = tx.artifacts.put_version(ArtifactType.SCHEMA, f"s_{uid}", {"columns": ["a", "b"]}, created_by="it")
        assert (v1.version_no, v2.version_no) == (1, 2)
        assert tx.artifacts.active_schema(f"s_{uid}").definition == {"columns": ["a", "b"]}
        mapper = tx.artifacts.put_version(ArtifactType.MAPPER, f"m_{uid}", {"kind": "tabular"}, created_by="it")
        sig = Signature(f"IT|PC|CSV|h:{uid}", f"h:{uid}", None, {"columns": ["a"]})
        proposed = tx.registry.propose(sig, [{"kind": "schema"}])
        assert tx.registry.get(proposed).status is SignatureStatus.PROPOSED
        assert tx.registry.lookup(sig.fingerprint, None) is None
        active = tx.registry.register(sig, mapper.version_id, "it")
        assert active == proposed
        assert tx.registry.lookup(sig.fingerprint, None).mapper_version_id == mapper.version_id
        row, _ = _create(tx, f"it-{uid}")
        tx.audit.quarantine(row.file_id, "unroutable", "looks like x", active)
        tx.audit.decision(row.file_id, "profile_match", {"kind": "tabular"}, {"candidates": []}, False, None)


def test_rejects_are_idempotent(mssql_store):
    with mssql_store.transaction() as tx:
        row, _ = _create(tx, f"it-{_uid()}")
        reject = Reject(file_id=row.file_id, row_identity="L3", raw_payload="x", reason_code="field_count_mismatch", schema_id="s")
        assert tx.rejects.add(reject) is True
        assert tx.rejects.add(reject) is False
        assert tx.rejects.count_open_for_file(row.file_id) == 1
        assert tx.rejects.mark_reclaimed(row.file_id, "L3") is True
        assert tx.rejects.mark_reclaimed(row.file_id, "L3") is False
        assert tx.rejects.count_open_for_file(row.file_id) == 0


def test_raw_zone_stage_promote_and_bisection(mssql_store):
    target = f"it_{_uid()}"
    with mssql_store.transaction() as tx:
        row, _ = _create(tx, f"it-{_uid()}")
        tx.raw.ensure_tables(target, ["a", "b"])
        rows = [StagedRow(row.file_id, f"L{i}", 1, 1, {"a": str(i), "b": "x"}, None) for i in range(2, 22)]
        rows[5] = StagedRow(row.file_id, "L" + "9" * 300, 1, 1, {"a": "too long id"}, None)  # forces SQLSTATE 22001
        rejects = stage(tx.raw, row.file_id, {target: rows}, batch_size=8)
        assert [r.reason_code for r in rejects] == ["db_insert_error"]
        assert promote(tx.raw, row.file_id, [target]) == 19
        tx.raw.stage_batch(target, rows[:3])
        assert promote(tx.raw, row.file_id, [target]) == 0
        assert tx.raw.count_landed(row.file_id, target) == 19
        tx.raw.ensure_tables(target, ["a", "b", "c"])  # schema version adds a column
    with mssql_store.transaction() as tx:
        assert tx.raw.count_landed(row.file_id, target) == 19
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest -m integration tests/integration/test_mssql_store.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'importer.mssql_store'`. (If `database.env` is absent, the tests skip instead; finish Task 2 first.)

- [ ] **Step 4: Implement `src/importer/mssql_store.py`**

```python
"""SQL Server repositories (design §8.11): SQLAlchemy Core, bound parameters only.

Engine comes from importer.db.get_engine() (DataEngine). One MssqlTx per
engine.begin() transaction; every repository shares that connection.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pyodbc
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .models import (
    ArtifactType,
    Declared,
    FileStatus,
    Reject,
    RejectStatus,
    Signature,
    SignatureHit,
    SignatureStatus,
    can_transition,
)
from .repositories import (
    ArtifactRow,
    ConcurrentTransition,
    IllegalTransition,
    LedgerRow,
    NotFound,
    RegistryRow,
    RowDataError,
    StagedRow,
)
from .schema_tables import (
    ENSURE_SCHEMAS_SQL,
    LINEAGE_COLUMNS,
    add_column_sql,
    create_raw_sql,
    create_stage_sql,
    q,
    validate_columns,
    validate_identifier,
)

USE_SETINPUTSIZES = False  # Task 2 spike decision
DATA_SQLSTATE_CLASSES = ("22", "23")


def _dump(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _load(value):
    return None if value is None else json.loads(value)


def _float(value) -> float | None:
    return None if value is None else float(value)


LEDGER_COLS = (
    "file_id, idempotency_key, source, original_filename, project, [class], file_type, status, signature_id, "
    "request_context, source_path, encoding, attempts, rows_total, rows_landed, rows_rejected, "
    "percent_parsed, percent_loaded, last_error"
)


def _ledger_row(m) -> LedgerRow:
    return LedgerRow(
        file_id=m["file_id"], idempotency_key=m["idempotency_key"], source=m["source"],
        original_filename=m["original_filename"], project=m["project"], cls=m["class"], file_type=m["file_type"],
        status=FileStatus(m["status"]), signature_id=m["signature_id"], request_context=_load(m["request_context"]),
        source_path=m["source_path"], encoding=m["encoding"], attempts=m["attempts"], rows_total=m["rows_total"],
        rows_landed=m["rows_landed"], rows_rejected=m["rows_rejected"], percent_parsed=_float(m["percent_parsed"]),
        percent_loaded=_float(m["percent_loaded"]), last_error=m["last_error"],
    )


class MssqlLedger:
    def __init__(self, conn: Connection):
        self.c = conn

    def _fetch(self, where: str, params: dict) -> LedgerRow | None:
        m = self.c.execute(text(f"SELECT {LEDGER_COLS} FROM dbo.file_ledger WHERE {where}"), params).mappings().first()
        return None if m is None else _ledger_row(m)

    def create_or_get(self, *, idempotency_key, source, original_filename, source_path, declared: Declared, request_context):
        existing = self._fetch("idempotency_key = :k", {"k": idempotency_key})
        if existing is not None:
            return existing, False
        file_id = self.c.execute(text(
            "INSERT INTO dbo.file_ledger (idempotency_key, source, original_filename, source_path, project, [class], "
            "file_type, request_context) OUTPUT INSERTED.file_id "
            "VALUES (:k, :source, :name, :path, :project, :cls, :ftype, :ctx)"
        ), {"k": idempotency_key, "source": source, "name": original_filename, "path": source_path,
            "project": declared.project, "cls": declared.cls, "ftype": declared.file_type,
            "ctx": _dump(request_context)}).scalar_one()
        return self.get(file_id), True

    def get(self, file_id):
        row = self._fetch("file_id = :id", {"id": file_id})
        if row is None:
            raise NotFound(file_id)
        return row

    def transition(self, file_id, src, dst):
        if not can_transition(src, dst):
            raise IllegalTransition(f"{src.value} -> {dst.value}")
        changed = self.c.execute(text(
            "UPDATE dbo.file_ledger SET status = :dst, updated_at = SYSUTCDATETIME() WHERE file_id = :id AND status = :src"
        ), {"dst": dst.value, "src": src.value, "id": file_id}).rowcount
        if changed != 1:
            raise ConcurrentTransition(f"file {file_id} is not in status {src.value}")

    def begin_reentry(self, file_id):
        row = self.get(file_id)
        if row.status not in (FileStatus.QUARANTINED, FileStatus.FAILED):
            raise IllegalTransition(f"re-entry needs quarantined or failed, file {file_id} is {row.status.value}")
        self.transition(file_id, row.status, FileStatus.DISCOVERED)
        self.c.execute(text("UPDATE dbo.file_ledger SET attempts = attempts + 1, last_error = NULL WHERE file_id = :id"),
                       {"id": file_id})
        return self.get(file_id)

    def _set(self, file_id, column: str, value) -> None:
        self.c.execute(text(f"UPDATE dbo.file_ledger SET {column} = :v, updated_at = SYSUTCDATETIME() WHERE file_id = :id"),
                       {"v": value, "id": file_id})

    def set_signature(self, file_id, signature_id):
        self._set(file_id, "signature_id", signature_id)

    def set_encoding(self, file_id, encoding):
        self._set(file_id, "encoding", encoding)

    def set_error(self, file_id, message):
        self._set(file_id, "last_error", message)

    def record_metrics(self, file_id, *, rows_total, rows_landed, rows_rejected, percent_parsed, percent_loaded):
        self.c.execute(text(
            "UPDATE dbo.file_ledger SET rows_total = :t, rows_landed = :l, rows_rejected = :r, percent_parsed = :pp, "
            "percent_loaded = CASE WHEN percent_loaded IS NULL OR :pl > percent_loaded THEN :pl ELSE percent_loaded END, "
            "updated_at = SYSUTCDATETIME() WHERE file_id = :id"
        ), {"t": rows_total, "l": rows_landed, "r": rows_rejected, "pp": percent_parsed, "pl": percent_loaded, "id": file_id})


REG_COLS = "signature_id, fingerprint, physical_part, xml_namespace, status, mapper_version_id, observed_schema, proposal, approved_by"


def _registry_row(m) -> RegistryRow:
    return RegistryRow(
        signature_id=m["signature_id"], fingerprint=m["fingerprint"], physical_part=m["physical_part"],
        xml_namespace=m["xml_namespace"], status=SignatureStatus(m["status"]), mapper_version_id=m["mapper_version_id"],
        observed_schema=_load(m["observed_schema"]), proposal=_load(m["proposal"]), approved_by=m["approved_by"],
    )


class MssqlRegistry:
    def __init__(self, conn: Connection):
        self.c = conn

    def _by_fp(self, fingerprint: str) -> RegistryRow | None:
        m = self.c.execute(text(f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE fingerprint = :fp"),
                           {"fp": fingerprint}).mappings().first()
        return None if m is None else _registry_row(m)

    def lookup(self, fingerprint, xml_namespace):
        row = self._by_fp(fingerprint)
        if row is None or row.status is not SignatureStatus.ACTIVE or row.mapper_version_id is None:
            return None
        if row.xml_namespace != xml_namespace:
            return None
        return SignatureHit(row.signature_id, row.fingerprint, row.mapper_version_id, row.xml_namespace)

    def get(self, signature_id):
        m = self.c.execute(text(f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE signature_id = :id"),
                           {"id": signature_id}).mappings().first()
        if m is None:
            raise NotFound(signature_id)
        return _registry_row(m)

    def find_active_by_physical_part(self, physical_part, xml_namespace):
        rows = self.c.execute(text(
            f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE physical_part = :p AND status = 'active'"
        ), {"p": physical_part}).mappings().all()
        return [r for r in (_registry_row(m) for m in rows) if r.xml_namespace == xml_namespace]

    def propose(self, sig: Signature, proposal):
        existing = self._by_fp(sig.fingerprint)
        if existing is not None:
            if existing.status is SignatureStatus.PROPOSED:
                self.c.execute(text(
                    "UPDATE dbo.signature_registry SET observed_schema = :obs, proposal = :prop WHERE signature_id = :id"
                ), {"obs": _dump(sig.observed), "prop": _dump(proposal), "id": existing.signature_id})
            return existing.signature_id
        return self.c.execute(text(
            "INSERT INTO dbo.signature_registry (fingerprint, physical_part, xml_namespace, status, observed_schema, "
            "proposal, provenance) OUTPUT INSERTED.signature_id "
            "VALUES (:fp, :part, :ns, 'proposed', :obs, :prop, 'profile_match')"
        ), {"fp": sig.fingerprint, "part": sig.physical_part, "ns": sig.xml_namespace,
            "obs": _dump(sig.observed), "prop": _dump(proposal)}).scalar_one()

    def register(self, sig: Signature, mapper_version_id, approved_by):
        existing = self._by_fp(sig.fingerprint)
        params = {"fp": sig.fingerprint, "part": sig.physical_part, "ns": sig.xml_namespace,
                  "obs": _dump(sig.observed), "mv": mapper_version_id, "by": approved_by}
        if existing is not None:
            self.c.execute(text(
                "UPDATE dbo.signature_registry SET status = 'active', mapper_version_id = :mv, physical_part = :part, "
                "xml_namespace = :ns, observed_schema = :obs, approved_by = :by, approved_at = SYSUTCDATETIME() "
                "WHERE fingerprint = :fp"
            ), params)
            return existing.signature_id
        return self.c.execute(text(
            "INSERT INTO dbo.signature_registry (fingerprint, physical_part, xml_namespace, status, mapper_version_id, "
            "observed_schema, provenance, approved_by, approved_at) OUTPUT INSERTED.signature_id "
            "VALUES (:fp, :part, :ns, 'active', :mv, :obs, 'hand_registration', :by, SYSUTCDATETIME())"
        ), params).scalar_one()


ART_COLS = "version_id, artifact_type, artifact_key, version_no, definition, is_active, created_by, content_sha256"


def _artifact_row(m) -> ArtifactRow:
    return ArtifactRow(
        version_id=m["version_id"], artifact_type=ArtifactType(m["artifact_type"]), artifact_key=m["artifact_key"],
        version_no=m["version_no"], definition=json.loads(m["definition"]), is_active=bool(m["is_active"]),
        created_by=m["created_by"], content_sha256=m["content_sha256"],
    )


class MssqlArtifacts:
    def __init__(self, conn: Connection):
        self.c = conn

    def put_version(self, artifact_type, artifact_key, definition, *, created_by, activate=True, content_sha256=None):
        params = {"t": artifact_type.value, "k": artifact_key}
        version_no = self.c.execute(text(
            "SELECT ISNULL(MAX(version_no), 0) + 1 FROM dbo.artifact_versions WITH (UPDLOCK, HOLDLOCK) "
            "WHERE artifact_type = :t AND artifact_key = :k"
        ), params).scalar_one()
        version_id = self.c.execute(text(
            "INSERT INTO dbo.artifact_versions (artifact_type, artifact_key, version_no, definition, is_active, "
            "content_sha256, created_by) OUTPUT INSERTED.version_id VALUES (:t, :k, :n, :d, 0, :sha, :by)"
        ), {**params, "n": version_no, "d": json.dumps(definition, ensure_ascii=False), "sha": content_sha256,
            "by": created_by}).scalar_one()
        if activate:
            self.c.execute(text(
                "UPDATE dbo.artifact_versions SET is_active = CASE WHEN version_id = :vid THEN 1 ELSE 0 END "
                "WHERE artifact_type = :t AND artifact_key = :k"
            ), {**params, "vid": version_id})
        return self.get(version_id)

    def get(self, version_id):
        m = self.c.execute(text(f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE version_id = :id"),
                           {"id": version_id}).mappings().first()
        if m is None:
            raise NotFound(version_id)
        return _artifact_row(m)

    def active_schema(self, schema_id):
        m = self.c.execute(text(
            f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE artifact_type = 'schema' AND artifact_key = :k AND is_active = 1"
        ), {"k": schema_id}).mappings().first()
        return None if m is None else _artifact_row(m)

    def active_schemas(self):
        rows = self.c.execute(text(
            f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE artifact_type = 'schema' AND is_active = 1"
        )).mappings().all()
        return [_artifact_row(m) for m in rows]


REJECT_COLS = "file_id, row_identity, raw_payload, reason_code, schema_id, reason_detail, status, created_at, reclaimed_at"


def _reject(m) -> Reject:
    return Reject(
        file_id=m["file_id"], row_identity=m["row_identity"], raw_payload=m["raw_payload"], reason_code=m["reason_code"],
        schema_id=m["schema_id"], reason_detail=m["reason_detail"], status=RejectStatus(m["status"]),
        created_at=m["created_at"], reclaimed_at=m["reclaimed_at"],
    )


class MssqlRejectStore:
    def __init__(self, conn: Connection):
        self.c = conn

    def add(self, reject: Reject) -> bool:
        inserted = self.c.execute(text(
            "INSERT INTO dbo.reject_repository (file_id, schema_id, row_identity, raw_payload, reason_code, reason_detail, status) "
            "SELECT :f, :s, :rid, :p, :rc, :rd, :st WHERE NOT EXISTS "
            "(SELECT 1 FROM dbo.reject_repository WHERE file_id = :f AND row_identity = :rid)"
        ), {"f": reject.file_id, "s": reject.schema_id, "rid": reject.row_identity, "p": reject.raw_payload,
            "rc": reject.reason_code, "rd": reject.reason_detail, "st": reject.status.value}).rowcount
        return inserted == 1

    def open_rejects_for_schema(self, schema_id: str) -> list[Reject]:
        rows = self.c.execute(text(
            f"SELECT {REJECT_COLS} FROM dbo.reject_repository WHERE schema_id = :s AND status = 'rejected'"
        ), {"s": schema_id}).mappings().all()
        return [_reject(m) for m in rows]

    def mark_reclaimed(self, file_id: int, row_identity: str) -> bool:
        changed = self.c.execute(text(
            "UPDATE dbo.reject_repository SET status = 'reclaimed', reclaimed_at = SYSUTCDATETIME() "
            "WHERE file_id = :f AND row_identity = :rid AND status = 'rejected'"
        ), {"f": file_id, "rid": row_identity}).rowcount
        return changed == 1

    def all_rejects(self) -> list[Reject]:
        return [_reject(m) for m in self.c.execute(text(f"SELECT {REJECT_COLS} FROM dbo.reject_repository")).mappings().all()]

    def count_open_for_file(self, file_id: int) -> int:
        return self.c.execute(text(
            "SELECT COUNT(*) FROM dbo.reject_repository WHERE file_id = :f AND status = 'rejected'"
        ), {"f": file_id}).scalar_one()


class MssqlAudit:
    def __init__(self, conn: Connection):
        self.c = conn

    def quarantine(self, file_id, reason_code, detail, signature_id):
        self.c.execute(text(
            "INSERT INTO dbo.file_quarantine (file_id, reason_code, reason_detail, signature_id) VALUES (:f, :rc, :d, :s)"
        ), {"f": file_id, "rc": reason_code, "d": detail, "s": signature_id})

    def decision(self, file_id, action_type, observed, proposed, applied, approval):
        self.c.execute(text(
            "INSERT INTO dbo.decision_log (file_id, action_type, observed, proposed, applied, approval) "
            "VALUES (:f, :a, :o, :p, :ap, :appr)"
        ), {"f": file_id, "a": action_type, "o": _dump(observed), "p": _dump(proposed), "ap": 1 if applied else 0,
            "appr": approval})


class MssqlRawZone:
    def __init__(self, conn: Connection):
        self.c = conn
        self._columns: dict[str, list[str]] = {}

    def _table_columns(self, schema: str, table: str) -> list[str] | None:
        names = self.c.execute(text(
            "SELECT c.name FROM sys.columns AS c JOIN sys.tables AS t ON t.object_id = c.object_id "
            "JOIN sys.schemas AS s ON s.schema_id = t.schema_id WHERE s.name = :s AND t.name = :t ORDER BY c.column_id"
        ), {"s": schema, "t": table}).scalars().all()
        return list(names) or None

    def _canonical(self, target: str) -> list[str]:
        if target not in self._columns:
            existing = self._table_columns("stage", target)
            if existing is None:
                raise RuntimeError(f"stage.{target} does not exist; ensure_tables was never called for it")
            self._columns[target] = [c for c in existing if not c.startswith("_")]
        return self._columns[target]

    def ensure_tables(self, schema_id, columns):
        validate_identifier(schema_id, "schema id")
        validate_columns(columns)
        self.c.exec_driver_sql(ENSURE_SCHEMAS_SQL)
        for schema, create in (("raw", create_raw_sql), ("stage", create_stage_sql)):
            existing = self._table_columns(schema, schema_id)
            if existing is None:
                self.c.exec_driver_sql(create(schema_id, columns))
                continue
            have = {c.lower() for c in existing}
            for column in columns:
                if column.lower() not in have:
                    self.c.exec_driver_sql(add_column_sql(schema, schema_id, column))
        self._columns.pop(schema_id, None)

    def clear_stage(self, file_id, target):
        self.c.execute(text(f"DELETE FROM stage.{q(target)} WHERE _file_id = :f"), {"f": file_id})

    def stage_batch(self, target, rows: list[StagedRow]):
        columns = self._canonical(target)
        names = LINEAGE_COLUMNS + columns
        sql = (f"INSERT INTO stage.{q(target)} ({', '.join(q(n) for n in names)}) "
               f"VALUES ({', '.join('?' for _ in names)})")
        params = [
            (r.file_id, r.row_identity, r.signature_id, r.artifact_version_id, r.extras_json,
             *(r.fields.get(c) for c in columns))
            for r in rows
        ]
        with self.c.begin_nested():
            cursor = self.c.connection.cursor()
            try:
                cursor.fast_executemany = True
                if USE_SETINPUTSIZES:
                    cursor.setinputsizes(
                        [(pyodbc.SQL_BIGINT, 0, 0), (pyodbc.SQL_WVARCHAR, 256, 0), (pyodbc.SQL_BIGINT, 0, 0),
                         (pyodbc.SQL_BIGINT, 0, 0)] + [(pyodbc.SQL_WVARCHAR, 0, 0)] * (len(names) - 4)
                    )
                cursor.executemany(sql, params)
            except pyodbc.Error as exc:
                state = exc.args[0] if exc.args else ""
                if isinstance(state, str) and state[:2] in DATA_SQLSTATE_CLASSES:
                    raise RowDataError(str(exc)) from exc
                raise
            finally:
                cursor.close()

    def promote(self, file_id, target):
        names = LINEAGE_COLUMNS + self._canonical(target)
        column_list = ", ".join(q(n) for n in names)
        inserted = self.c.execute(text(
            f"INSERT INTO raw.{q(target)} ({column_list}) "
            f"SELECT {', '.join('s.' + q(n) for n in names)} FROM stage.{q(target)} AS s "
            f"WHERE s._file_id = :f AND NOT EXISTS (SELECT 1 FROM raw.{q(target)} AS r "
            f"WHERE r._file_id = s._file_id AND r._row_identity = s._row_identity)"
        ), {"f": file_id}).rowcount
        self.c.execute(text(f"DELETE FROM stage.{q(target)} WHERE _file_id = :f"), {"f": file_id})
        return inserted

    def count_landed(self, file_id, target):
        if self._table_columns("raw", target) is None:
            return 0
        return self.c.execute(text(f"SELECT COUNT(*) FROM raw.{q(target)} WHERE _file_id = :f"), {"f": file_id}).scalar_one()


class MssqlTx:
    def __init__(self, conn: Connection):
        self.conn = conn
        self.ledger = MssqlLedger(conn)
        self.registry = MssqlRegistry(conn)
        self.artifacts = MssqlArtifacts(conn)
        self.rejects = MssqlRejectStore(conn)
        self.raw = MssqlRawZone(conn)
        self.audit = MssqlAudit(conn)


class MssqlStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def transaction(self) -> Iterator[MssqlTx]:
        with self.engine.begin() as conn:
            yield MssqlTx(conn)
```

If Task 2 chose `USE_SETINPUTSIZES = True`, set it here.

- [ ] **Step 5: Run the integration tests (user present for MFA if Entra)**

Run: `.venv/Scripts/python -m pytest -m integration tests/integration/test_mssql_store.py -v`
Expected: 5 passed.

- [ ] **Step 6: Run the unit suite to confirm nothing imports DataEngine**

Run: `.venv/Scripts/python -m pytest`
Expected: all green, with integration tests deselected.

- [ ] **Step 7: Commit**

```bash
git add src/importer/mssql_store.py tests/integration
git commit -m "$(cat <<'EOF'
Add the SQL Server store on SQLAlchemy Core

Repositories share one transaction per step; staging uses fast_executemany
inside a savepoint so data errors surface as RowDataError for bisection.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: `importer` CLI

**Files:**
- Create: `src/importer/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pipeline.process/retry`, `admin.*`, `mappers.loading.load_mapper`, `mappers.preview.preview`, `schema_tables`, `db`, `config`, `MemoryStore`/`MssqlStore`.
- Produces: `cli.build_parser() -> argparse.ArgumentParser`; `cli.main(argv=None, store=None) -> int`. Exit codes for `process`/`retry`: 0 completed, 2 quarantined, 1 failed. Commands:

  | Command | Arguments |
  |---|---|
  | `process` | `path --project --class --type --source [--context k=v]` |
  | `preview` | `path --mapper-version --project --class --type [--context] [--rows]` |
  | `retry` | `file_id` |
  | `schema import` | `db.schema.table [--as id]` |
  | `mapper add` | `spec.json --key name` |
  | `signature register` | `sample --project --class --type --mapper-version` |
  | `db rebuild` | `[--confirm NAME]` |

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
from __future__ import annotations

import json

import pytest

from importer import cli
from importer.memory_store import MemoryStore
from tests.support import URL_SCHEMA, URL_SPEC_A, fixture


@pytest.fixture
def store():
    s = MemoryStore()
    from importer.admin import register_schema
    with s.transaction() as tx:
        register_schema(tx, "url_template", URL_SCHEMA["url_template"], created_by="test")
    return s


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def test_mapper_add_signature_register_and_process(store, tmp_path, capsys):
    spec = tmp_path / "urls.json"
    spec.write_text(json.dumps(URL_SPEC_A), encoding="utf-8")
    assert cli.main(["mapper", "add", str(spec), "--key", "urls_a"], store=store) == 0
    mapper = _json_out(capsys)
    sample = str(fixture("url_template_a.csv"))
    assert cli.main(["signature", "register", sample, "--project", "vt", "--class", "pc", "--type", "csv",
                     "--mapper-version", str(mapper["version_id"])], store=store) == 0
    assert _json_out(capsys)["fingerprint"].startswith("VT|PC|CSV|h:")
    code = cli.main(["process", sample, "--project", "VT", "--class", "PC", "--type", "CSV", "--source", "cli",
                     "--context", "run_id=9"], store=store)
    result = _json_out(capsys)
    assert code == 0 and result["status"] == "completed" and result["rows_landed"] == 2


def test_process_unknown_file_exits_2(store, capsys):
    code = cli.main(["process", str(fixture("url_template_b.csv")), "--project", "VT", "--class", "PC",
                     "--type", "CSV", "--source", "cli"], store=store)
    result = _json_out(capsys)
    assert code == 2 and result["quarantine_reason"] == "unroutable"
    assert result["proposal_summary"].startswith("looks like url_template")


def test_preview(store, tmp_path, capsys):
    spec = tmp_path / "urls.json"
    spec.write_text(json.dumps(URL_SPEC_A), encoding="utf-8")
    cli.main(["mapper", "add", str(spec), "--key", "urls_a"], store=store)
    version = _json_out(capsys)["version_id"]
    assert cli.main(["preview", str(fixture("url_template_a.csv")), "--mapper-version", str(version),
                     "--project", "VT", "--class", "PC", "--type", "CSV"], store=store) == 0
    assert _json_out(capsys)["counts"] == {"url_template": 2}


def test_bad_context_pair_is_rejected(store):
    with pytest.raises(SystemExit):
        cli.main(["process", str(fixture("url_template_a.csv")), "--project", "VT", "--class", "PC",
                  "--type", "CSV", "--source", "cli", "--context", "novalue"], store=store)


def test_rebuild_refuses_outside_dev(monkeypatch, capsys):
    monkeypatch.setattr("importer.config.importer_env", lambda: None)
    assert cli.main(["db", "rebuild", "--confirm", "x"]) == 1
    assert "IMPORTER_ENV" in capsys.readouterr().err


def test_rebuild_requires_matching_database_name(monkeypatch, capsys):
    monkeypatch.setattr("importer.config.importer_env", lambda: "DEV")
    monkeypatch.setattr("importer.db.load_connection_document", lambda *a, **k: {"database": "imp_dev"})
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: pytest.fail("must not connect"))
    assert cli.main(["db", "rebuild", "--confirm", "wrong"]) == 1
    assert "did not match" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: FAIL, with `ImportError: cannot import name 'cli'`.

- [ ] **Step 3: Implement `src/importer/cli.py`**

```python
"""importer — command-line interface (design spec §11)."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

EXIT_CODES = {"completed": 0, "quarantined": 2, "failed": 1}


def _context(pairs: list[str] | None) -> dict:
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--context expects KEY=VALUE, got {pair!r}")
        out[key] = value
    return out


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _default_store():
    from .db import get_engine
    from .mssql_store import MssqlStore

    return MssqlStore(get_engine())


def cmd_process(args, store) -> int:
    from .pipeline import process

    result = process(args.path, project=args.project, cls=args.cls, file_type=args.type, source=args.source,
                     context=_context(args.context), store=store)
    _print(result.to_dict())
    return EXIT_CODES.get(result.status.value, 1)


def cmd_retry(args, store) -> int:
    from .pipeline import retry

    result = retry(args.file_id, store=store)
    _print(result.to_dict())
    return EXIT_CODES.get(result.status.value, 1)


def cmd_preview(args, store) -> int:
    from .mappers.base import RunContext
    from .mappers.loading import load_mapper
    from .mappers.preview import preview
    from .models import Declared
    from .readers import open_document

    declared = Declared.normalize(args.project, args.cls, args.type)
    with store.transaction() as tx:
        artifact = tx.artifacts.get(args.mapper_version)
        schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
    mapper = load_mapper(artifact, known_schemas=set(schemas))
    doc = open_document(args.path, declared.file_type)
    ctx = RunContext(file_id=0, signature_id=0, artifact_version_id=artifact.version_id,
                     request_context=_context(args.context), schemas={t: schemas[t] for t in mapper.targets})
    _print(preview(mapper, doc, ctx, n=args.rows).to_dict())
    return 0


def cmd_schema_import(args, store) -> int:
    from .admin import register_schema
    from .schema_tables import information_schema_sql

    parts = args.table.split(".")
    if len(parts) != 3:
        raise SystemExit("schema import expects DATABASE.SCHEMA.TABLE")
    database, schema, table = parts
    with store.transaction() as tx:
        columns = [r[0] for r in tx.conn.exec_driver_sql(information_schema_sql(database), (schema, table)).all()]
        if not columns:
            raise SystemExit(f"{args.table} has no columns (or does not exist)")
        row = register_schema(tx, args.as_ or table, columns, created_by=getpass.getuser())
    _print({"schema_id": row.artifact_key, "version_id": row.version_id, "version_no": row.version_no, "columns": columns})
    return 0


def cmd_mapper_add(args, store) -> int:
    from .admin import register_mapper

    definition = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    with store.transaction() as tx:
        row = register_mapper(tx, args.key, definition, created_by=getpass.getuser())
    _print({"mapper": row.artifact_key, "version_id": row.version_id, "version_no": row.version_no})
    return 0


def cmd_signature_register(args, store) -> int:
    from .admin import register_signature
    from .models import Declared

    declared = Declared.normalize(args.project, args.cls, args.type)
    with store.transaction() as tx:
        signature_id = register_signature(tx, Path(args.sample).read_bytes(), declared, args.mapper_version,
                                          approved_by=getpass.getuser())
        fingerprint = tx.registry.get(signature_id).fingerprint
    _print({"signature_id": signature_id, "fingerprint": fingerprint})
    return 0


def cmd_db_rebuild(args) -> int:
    from . import config, db
    from .schema_tables import REBUILD_DROP_SQL

    if config.importer_env() != "DEV":
        print("refusing: IMPORTER_ENV is not DEV (set it in .env)", file=sys.stderr)
        return 1
    database = db.load_connection_document()["database"]
    typed = args.confirm if args.confirm is not None else input(
        f"This drops every importer table in {database}. Type the database name to confirm: ")
    if typed.strip() != database:
        print("confirmation did not match the database name; nothing changed", file=sys.stderr)
        return 1
    script = (config.home() / "sql" / "001_schema.sql").read_text(encoding="utf-8")
    with db.get_engine().begin() as conn:
        conn.exec_driver_sql(REBUILD_DROP_SQL)
        conn.exec_driver_sql(script)
    print(f"rebuilt the importer schema in {database}")
    return 0


def _declared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True)
    parser.add_argument("--class", dest="cls", required=True)
    parser.add_argument("--type", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="importer", description="ELT import agent — deterministic fast path (M2a)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", help="import one file")
    p.add_argument("path")
    _declared(p)
    p.add_argument("--source", required=True)
    p.add_argument("--context", action="append", metavar="KEY=VALUE")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("preview", help="dry-run a mapper against a file")
    p.add_argument("path")
    p.add_argument("--mapper-version", type=int, required=True)
    _declared(p)
    p.add_argument("--context", action="append", metavar="KEY=VALUE")
    p.add_argument("--rows", type=int, default=20)
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("retry", help="re-enter a quarantined or failed file")
    p.add_argument("file_id", type=int)
    p.set_defaults(func=cmd_retry)

    schema = sub.add_parser("schema").add_subparsers(dest="schema_command", required=True)
    p = schema.add_parser("import", help="create a schema definition from an existing table")
    p.add_argument("table", help="DATABASE.SCHEMA.TABLE")
    p.add_argument("--as", dest="as_")
    p.set_defaults(func=cmd_schema_import)

    mapper = sub.add_parser("mapper").add_subparsers(dest="mapper_command", required=True)
    p = mapper.add_parser("add", help="add a mapper version from a JSON spec")
    p.add_argument("spec")
    p.add_argument("--key", required=True)
    p.set_defaults(func=cmd_mapper_add)

    signature = sub.add_parser("signature").add_subparsers(dest="signature_command", required=True)
    p = signature.add_parser("register", help="hand-register a sample file's signature to a mapper")
    p.add_argument("sample")
    _declared(p)
    p.add_argument("--mapper-version", type=int, required=True)
    p.set_defaults(func=cmd_signature_register)

    database = sub.add_parser("db").add_subparsers(dest="db_command", required=True)
    p = database.add_parser("rebuild", help="DEV only: drop and re-create the importer schema")
    p.add_argument("--confirm", metavar="DATABASE_NAME")
    p.set_defaults(func=None)
    return parser


def main(argv: list[str] | None = None, store=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "db":
        return cmd_db_rebuild(args)
    return args.func(args, store if store is not None else _default_store())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: 6 passed.

- [ ] **Step 5: Smoke-test the console script**

Run: `.venv/Scripts/importer --help`
Expected: usage text listing `process, preview, retry, schema, mapper, signature, db`.

- [ ] **Step 6: Commit**

```bash
git add src/importer/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
Add the importer CLI

process/retry/preview plus the hand-registration commands (schema import,
mapper add, signature register) and a guarded DEV-only db rebuild.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: M2a acceptance on DEV, documentation, and the design record

**Files:**
- Create: `tests/integration/test_acceptance.py`
- Modify: `CLAUDE.md`, `README.md`
- Confluence: Design Decisions §8 (3774676996), project page (3773956110), Blueprint (3774775300)

**Interfaces:**
- Consumes: everything above, via `MssqlStore`.
- Produces: proof of the five §8.13 acceptance cases against the real DEV SQL Server; updated docs and design record.

- [ ] **Step 1: Rebuild DEV from the final DDL (user present)**

Run: `.venv/Scripts/importer db rebuild`
When prompted, the user types the database name.
Expected: `rebuilt the importer schema in <db>`.

- [ ] **Step 2: Write the acceptance tests**

`tests/integration/test_acceptance.py`:

```python
"""M2a acceptance (design §8.13) against the DEV SQL Server."""

from __future__ import annotations

import json
import uuid

import pytest

from importer.admin import register_mapper, register_schema, register_signature
from importer.models import Declared, FileStatus
from importer.pipeline import process
from tests.support import EVENT_SCHEMA, EVENT_SPEC, URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, fixture, load_json

pytestmark = pytest.mark.integration


def _unique_copy(tmp_path, name: str, comment: bytes) -> object:
    path = tmp_path / name
    path.write_bytes(fixture(name).read_bytes() + comment)
    return path


def _setup(store, sample, declared, schemas, spec, key) -> int:
    with store.transaction() as tx:
        for schema_id, columns in schemas.items():
            register_schema(tx, schema_id, columns, created_by="acceptance")
        mapper = register_mapper(tx, key, spec, created_by="acceptance")
        register_signature(tx, sample.read_bytes(), declared, mapper.version_id, approved_by="acceptance")
    return mapper.version_id


def _landed(store, file_id: int, target: str) -> int:
    with store.transaction() as tx:
        return tx.raw.count_landed(file_id, target)


def test_1_formulary_lands_three_tables(mssql_store, tmp_path):
    path = _unique_copy(tmp_path, "formulary_dc.xml", f"\n<!-- {uuid.uuid4()} -->\n".encode())
    _setup(mssql_store, path, Declared.normalize("DC", "PC", "XML"), load_json("formulary_schemas.json"),
           load_json("formulary_spec.json"), "formulary")
    result = process(path, project="DC", cls="PC", file_type="XML", source="acceptance",
                     context={"serffbindername": "ABCD-DC25-1", "run_id": "1"}, store=mssql_store)
    assert result.status is FileStatus.COMPLETED and result.percent_loaded == 100.0
    assert [_landed(mssql_store, result.file_id, t) for t in
            ("formulary_benefit_definition", "formulary_tier_definition", "formulary_drug_list")] == [5, 2, 3]


def test_2_two_variants_one_schema(mssql_store, tmp_path):
    tag = uuid.uuid4().hex[:8]
    a = tmp_path / "a.csv"
    a.write_text(fixture("url_template_a.csv").read_text() + f"A{tag},https://example.org/{tag},SBC\n")
    b = tmp_path / "b.csv"
    b.write_text(fixture("url_template_b.csv").read_text() + f"B{tag},https://example.net/{tag},SBC\n")
    _setup(mssql_store, a, Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    _setup(mssql_store, b, Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_B, "urls_b")
    ra = process(a, project="VT", cls="PC", file_type="CSV", source="acceptance", store=mssql_store)
    rb = process(b, project="VT", cls="PC", file_type="CSV", source="acceptance", store=mssql_store)
    assert ra.status is rb.status is FileStatus.COMPLETED
    assert _landed(mssql_store, ra.file_id, "url_template") == 3
    assert _landed(mssql_store, rb.file_id, "url_template") == 3


def test_3_bad_ndjson_line_completes_with_a_reject(mssql_store, tmp_path):
    path = tmp_path / "events.ndjson"
    path.write_bytes(fixture("events.ndjson").read_bytes() + json.dumps({"id": uuid.uuid4().hex, "name": "z", "amount": "1"}).encode() + b"\n")
    _setup(mssql_store, path, Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    result = process(path, project="DC", cls="PD", file_type="NDJSON", source="acceptance", store=mssql_store)
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.rows_landed, result.rows_rejected) == (5, 4, 1)
    assert result.percent_loaded == 80.0


def test_4_unknown_project_is_quarantined_with_reuse_proposal(mssql_store, tmp_path):
    mapper_id = _setup(mssql_store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
                       load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary")
    project = "Z" + uuid.uuid4().hex[:6].upper()
    path = _unique_copy(tmp_path, "formulary_dc.xml", f"\n<!-- {project} -->\n".encode())
    result = process(path, project=project, cls="PC", file_type="XML", source="acceptance", store=mssql_store)
    assert result.status is FileStatus.QUARANTINED and result.quarantine_reason == "unroutable"
    assert result.proposal_summary == f"looks like DC|PC|XML|prescriptionDrugTemplateVO → reuse mapper {mapper_id}"


def test_5_byte_identical_redelivery(mssql_store, tmp_path):
    path = _unique_copy(tmp_path, "formulary_dc.xml", f"\n<!-- {uuid.uuid4()} -->\n".encode())
    _setup(mssql_store, path, Declared.normalize("DC", "PC", "XML"), load_json("formulary_schemas.json"),
           load_json("formulary_spec.json"), "formulary")
    first = process(path, project="DC", cls="PC", file_type="XML", source="acceptance", store=mssql_store)
    again = process(path, project="DC", cls="PC", file_type="XML", source="acceptance", store=mssql_store)
    assert again.file_id == first.file_id
    assert _landed(mssql_store, first.file_id, "formulary_drug_list") == 3
```

Test 4 registers the formulary mapper again under the newest mapper version; the `DC` signature is re-pointed to it, so the expected `mapper_id` in the summary is that newest version.

- [ ] **Step 3: Run the acceptance tests (user present)**

Run: `.venv/Scripts/python -m pytest -m integration -v`
Expected: all integration tests pass (5 store tests + 5 acceptance tests).

- [ ] **Step 4: Update `CLAUDE.md`**

Make three edits:

1. Replace the `## Repo layout` block with:

````markdown
## Repo layout

```
sql/001_schema.sql         Authoritative control-plane DDL (prototype: edited in place; git is history)
scripts/                   One-off spikes (fast_executemany)
src/importer/
  models.py                Statuses (+ re-entry), Declared, Signature, LandRow/RejectRow, FileResult
  metrics.py               percent_parsed / percent_loaded, monotonic floor          [invariant #1]
  reject_store.py          Reject repository Protocol + in-memory reference
  reclaim.py               Idempotent reclaim                                         [invariant #2]
  readers/                 delimited, Excel, JSON, NDJSON, XML -> Document
  fingerprint.py, detector.py   PROJECT|CLASS|TYPE|<physical> signatures, model-free lookup
  mappers/                 Mapper contract, declarative spec, loading (hash-pinned plugins), preview
  shapers/                 Reviewed structural helpers (excel.anchor_header, excel.crosstab_unpivot)
  profiler.py              Miss profiling + "looks like" matching
  precheck.py, loader.py   Row triage, stage with bisection, promote
  repositories.py          Store/Tx Protocols; memory_store.py (tests) / mssql_store.py (DEV)
  schema_tables.py         raw./stage. DDL generation, rebuild drop SQL
  pipeline.py              process() / retry() — the state machine
  admin.py, cli.py         Registration helpers and the `importer` CLI
  config.py, db.py         .env / database.env; DataEngine imported lazily in db.get_engine()
  agent/harness.py         Agent slow path (Claude Agent SDK)                         [stub → M2b/M5]
tests/                     Unit + property suites; tests/integration needs DEV (pytest -m integration)
```
````

2. Replace the `## Running` block with:

````markdown
## Running

```bash
pip install -e ".[dev]"            # into .venv
pytest                             # unit + property suites (integration deselected)
pytest -m integration              # needs database.env 'importer' entry (DEV SQL Server)
importer --help                    # process / preview / retry / schema import / mapper add / signature register / db rebuild
```

`database.env` (gitignored) holds the DataEngine connection named `importer` (Windows `"trusted": "yes"` or
Entra `UN` only — never a `PW`). `.env` holds `IMPORTER_ENV=DEV` to allow `importer db rebuild`.
````

3. Replace `## Current status & next milestone` with:

```markdown
## Current status & next milestone

M2a (deterministic fast path) is implemented: all six readers, exact signatures, declarative/shaper/plugin
mappers, raw zone mirroring existing destination tables, bisecting loader, miss profiling with "looks like"
proposals, re-entry. Next: **M2b** — confirm-and-bind onboarding (attended prompt + review command) and the
Claude Agent SDK slow path; acceptance is the very first real file. Every M2a result type has `to_dict()`
so M2b can expose it as an agent tool.
```

Also remove "Fingerprint composition" from `## Open items` (resolved in Design Decisions §8.6).

- [ ] **Step 5: Update `README.md`**

Make four edits:
1. Replace its `## Layout` block with the same layout block as CLAUDE.md.
2. Replace `## Run the tests` with the same `## Running` block.
3. In the build-sequence table, set M2's State to `M2a implemented (Design Decisions §8); M2b next`.
4. Remove "Signature fingerprint composition" from the open items list.

- [ ] **Step 6: Update Confluence**

Use the Atlassian MCP. For each page: fetch HTML → edit only the stated parts → update, keeping all existing content and the strike-through convention on Design Decisions.

- **Design Decisions (3774676996):**
  - Change the §8 heading to `8. M2 fast-path design (M2a IMPLEMENTED)`.
  - Update the note panel to say M2a is implemented on branch `working`.
  - Append `8.14 Refinements made during planning (DECIDED)` listing the seven refinements from this plan's header, each one line.
- **Project page (3773956110):**
  - Tick the fast-path task with the note "M2a implemented; M2b (onboarding + agent) next".
  - Add one dated line to the Current state panel.
- **Blueprint (3774775300):** set the M2 row's state to "M2a done", in the same style as the existing update notes.

- [ ] **Step 7: Run everything one last time**

Run: `.venv/Scripts/python -m pytest && .venv/Scripts/python -m pytest -m integration`
Expected: both green.

- [ ] **Step 8: Commit**

```bash
git add tests/integration/test_acceptance.py CLAUDE.md README.md
git commit -m "$(cat <<'EOF'
Prove M2a acceptance on DEV and document the fast path

All five design §8.13 cases pass against the DEV SQL Server; CLAUDE.md,
README and the Confluence design record reflect the implemented M2a.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review (completed while writing)

- **Spec coverage, §2:** six readers (Tasks 4–7), signatures (8), three mapper tiers (10–13), raw/stage generation and schema import (17, 18, 19), loader (15), repositories (9, 18), profiler (14), re-entry and retry (3, 9, 16), CLI (19), DDL (17), docs (20). **§4:** declared parameters (3). **§5:** sniffing, encoding, physical parts, row identities, reader rejects (4–8). **§6:** interface, spec, shapers, plugins, preview (3, 10–13). **§7:** profiler and matcher (14). **§8:** pre-check, bisection, promote (15, 18). **§9:** DDL (17). **§10:** metrics and conservation (16), DB-side floor (18). **§11:** config and CLI (1, 19). **§12:** deps (1). **§13:** spike (2), unit/property/integration and acceptance (4–20). **§14:** docs and Confluence (17, 20).
- **Type consistency:** `SignatureHit(signature_id, fingerprint, mapper_version_id, xml_namespace)`, `StagedRow(...)`, `RunContext(...)` and the `Tx` attributes are identical everywhere they appear. `detect()` returns `(Signature, SignatureHit | None)` in both Task 8 and Task 16.
- **No placeholders:** the only values deliberately left to execution are the two Task 2 spike decisions (`DEFAULT_BATCH_SIZE`, `USE_SETINPUTSIZES`). Both have concrete defaults in code and a stated decision rule.
