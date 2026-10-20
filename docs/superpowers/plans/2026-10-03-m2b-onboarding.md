# M2b — Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a file's signature is unknown, a person in their own Claude client reviews a drafted column mapping (drafted deterministically or by Claude through the importer's MCP server). On approval, the signature is bound and every waiting file lands.

**Architecture:**
- **Namespace:** raw tables are named after their destination address (`npvcs.relational.url_template` → `npvcs_relational.url_template`).
- **Settings:** stored as a versioned `config` artifact in the control database. The database marks itself DEV or PROD.
- **Schema import:** reads destination tables over per-destination connections, or from a file.
- **Onboarding core (`onboarding.py`):** owns proposal review states and the deterministic drafts, and binds a signature in one transaction.
- **MCP server (`mcp_server.py`):** exposes the core as tools plus an `onboard` prompt, with a sign-off gate on the binding tools.
- **Locators:** files are always read from their shared location.

**Tech Stack:**
- **Existing:** Python 3.14 venv, SQLAlchemy 2.x Core, pyodbc, DataEngine, lxml, openpyxl, xlrd, python-dotenv, pytest, Hypothesis.
- **New:** the official MCP Python SDK (`mcp`, v2 API: `MCPServer`), `azure-storage-blob` and `azure-identity` (Blob locators), and `anyio`'s pytest plugin (ships with `mcp`).

**Spec:** `docs/superpowers/specs/2026-10-03-m2b-onboarding-design.md`. Decision record: Confluence *Design Decisions* §8.15–8.22.

## Global Constraints

**Invariants and the data boundary**
- `tests/test_metrics_monotonic.py` and `tests/test_reclaim_idempotent.py` stay green and **unchanged**. `metrics.py`, `reclaim.py` and `rules/base.py` are not modified.
- The model is never in the row-data path. Only metadata and capped samples (default 5 distinct values × 200 characters per column) cross MCP. Sample values are never written to `decision_log` or to the `importer` logger.
- Rule-as-data: nothing submitted through onboarding or MCP may be `"kind": "plugin"`. The agent never writes or edits code.
- No Claude Agent SDK, no Anthropic API key, no Key Vault. `claude-agent-sdk` is not added.

**Settings and environment**
- **Every setting lives in the control database** as artifact type `config`, key `importer`, versioned. `database.env` (DataEngine's `importer` entry) is the only local file. `.env` and `IMPORTER_ENV` are retired. `python-dotenv` stays, because `db.py` uses it to parse `database.env`.
- **DEV guard:** `db rebuild` requires **both** `dbo.database_identity.environment = 'DEV'` **and** `'dev'` in `DB_NAME()` (case-insensitive, read from the server), plus the typed database-name confirmation. `dbo.database_identity` is never dropped by rebuild, and its row is written only by `importer db init`.

**Naming and identifiers**
- **Addresses:** `database.schema.table`, lowercased. Each part matches `^[A-Za-z0-9_]+$`.
  - Raw table: `[<db>_<schema>].[<table>]`. Stage table: `stage.[<db>_<schema>__<table>]`. Raw PK: `pk_<db>_<schema>__<table>`.
  - Every generated identifier is at most 128 characters.
  - Reserved raw-schema names: `dbo, stage, sys, guest, information_schema, meta, raw`, plus any name starting with `db_`.
- SQL values are always bound parameters. Identifiers are validated and bracket-quoted.

**Files and approvals**
- **Locators:** files are read from their locator, stored verbatim: UNC/SMB `\\server\share\…`, Azure Files `\\<acct>.file.core.windows.net\…`, or Blob `https://<acct>.blob.core.windows.net/<container>/…`. Local paths are for development and tests only. Every read after discovery checks the SHA-256 against `idempotency_key`.
- **Sign-off:** binding a signature (`accept_mapping`) and registering schemas (`register_schema`) require human approval. The approver is the OS user (`getpass.getuser()`), never a model-supplied value.

**DDL, commands and commits**
- DDL policy (prototype): edit `sql/001_schema.sql` in place, with git as the history. Mirror every change on Confluence page 3775135746 in the same task.
- Commands run from the repo root in Git Bash: `.venv/Scripts/python -m pytest …`. Plain `pytest` excludes `integration`-marked tests.
- Work happens on branch `m2b`. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Refinements to the spec made while planning

These are recorded in the spec in Task 0 and on Confluence in Task 15.
1. **`python-dotenv` stays.** `db.py` parses `database.env` (a dotenv-format file) with it. Only `.env` / `IMPORTER_ENV` are retired. This corrects spec §5 and §11.
2. **No `revising` state.** A revision is simply another `submit_mapping` with a `note`, so `revising` is never set. The states used are `needs_mapping`, `ready_for_review`, `accepted` and `rejected`.
3. **Relaxed spec targets.** `parse_spec` accepts targets of one to three `[A-Za-z0-9_]+` parts separated by dots, so unit tests can keep short inline targets. In practice targets are addresses, because the target must be a registered schema whenever `known_schemas` is given (always, outside unit tests).
4. **`get_file_profile` reads the file.** It returns the full profile (XML element paths with counts), because the registry row only stores the root and namespace for XML.
5. **Re-import that only loses columns reports `unchanged`.** It lists the losses in `dropped_at_source`. Columns are never dropped, so a new version would be identical.

## File structure

```
src/importer/
  addresses.py        NEW  Address parsing, physical names, collision check (§8.15)
  settings.py         NEW  config artifact: destinations, sample caps (§8.17)
  environment.py      NEW  database_identity, DEV guard, banner, db init (§8.17)
  schema_sources.py   NEW  LiveSource / file columns, wildcard expansion, import report (§8.16)
  locators.py         NEW  read files by locator, hash check (§8.21)
  proposals.py        NEW  proposal object shape + review defaults (§8.18)
  onboarding.py       NEW  pending, suggest, profile, samples, search, preview, submit, reject, accept (§8.18)
  mcp_server.py       NEW  MCPServer tools + 'onboard' prompt + sign-off gate (§8.19)
  mcp_reference.md    NEW  spec-format reference served to the model (package data)
  models.py           CHG  ArtifactType.CONFIG
  repositories.py     CHG  new repository methods; RegistryRow.proposal is a dict; ArtifactRow.approved_by
  memory_store.py     CHG  implements the new methods
  mssql_store.py      CHG  implements the new methods; raw zone by address
  schema_tables.py    CHG  address-based DDL, importer-owned schemas, rebuild SQL
  admin.py            CHG  register_schema(address) with destination check + versioning outcome
  mappers/spec.py     CHG  dotted targets
  pipeline.py         CHG  locators
  db.py               CHG  engine_for(server, database)
  config.py           CHG  importer_env() removed
  cli.py              CHG  banner, config, db init/rebuild guard, schema import, pending, mcp
  agent/harness.py    CHG  docstring only
sql/001_schema.sql    CHG  'config' artifact type, database_identity, comments
.mcp.json             NEW  registers `importer mcp` for Claude Code in this repo
docs/mapping-manual.md NEW hand-mapping manual
tests/                test_addresses, test_settings, test_environment, test_schema_sources, test_locators,
                      test_proposals_repos, test_onboarding, test_onboarding_accept, test_mcp_server (new);
                      support, test_cli, test_loading_and_admin, test_profiler, test_schema_tables,
                      test_pipeline, test_spec_* (changed); integration/* (changed + test_m2b.py)
```

---

### Task 0: Branch check, dependencies and MCP API check

**Files:**
- Modify: `pyproject.toml`
- Modify: `docs/superpowers/specs/2026-10-03-m2b-onboarding-design.md` (refinements 1–5)

**Interfaces:**
- Produces: installed `mcp>=2`, `azure-storage-blob>=12.19`, `azure-identity>=1.15`. Confirmed import paths, used by Task 12:
  - `from mcp.server import MCPServer`
  - `from mcp.server.mcpserver import Context`
  - `from mcp import Client`
  - `from mcp.types import ToolAnnotations`

- [ ] **Step 1: Confirm the branch**

Run: `git branch --show-current`
Expected: `m2b`. If it says anything else, stop and ask.

- [ ] **Step 2: Add the dependencies**

In `pyproject.toml`, replace the dependency list's last two lines:

```toml
    "python-dotenv>=1.0",
    # "claude-agent-sdk",   # agent slow path (M2b / M5)
]
```

with:

```toml
    "python-dotenv>=1.0",          # parses database.env
    "mcp>=2",                      # importer mcp server (design §8.19)
    "azure-storage-blob>=12.19",   # Blob locators (design §8.21), imported lazily
    "azure-identity>=1.15",        # Entra credential for Blob, imported lazily
]
```

Also add `"pydantic>=2"` to the same list if `pip show pydantic` shows it is not already a dependency of `mcp` (it normally is; skip if present).

- [ ] **Step 3: Install and check the MCP API surface**

Run:
```bash
.venv/Scripts/python -m pip install -e ".[dev]" && .venv/Scripts/python - <<'EOF'
import mcp, importlib.metadata as md
print("mcp", md.version("mcp"))
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp import Client
from mcp.types import ToolAnnotations
import inspect
s = MCPServer("probe")
print("tool", hasattr(s, "tool"), "prompt", hasattr(s, "prompt"), "run", hasattr(s, "run"))
print("run signature", inspect.signature(MCPServer.run))
print("annotation fields", list(ToolAnnotations.model_fields))
print("elicit", hasattr(Context, "elicit"))
import anyio, azure.storage.blob, azure.identity
print("ok")
EOF
```
Expected:
- an `mcp 2.x` version line;
- `tool True prompt True run True`;
- a `run` signature that accepts `transport` (Task 11 calls `run(transport="stdio")`; if the parameter is named differently, use that name);
- the annotation field names. Task 11's `_annotations` helper handles both `read_only_hint`-style and `readOnlyHint`-style names;
- `elicit True`;
- `ok`.

If any import fails, the installed SDK is v1:
1. Use `from mcp.server.fastmcp import FastMCP as MCPServer, Context`.
2. For in-process tests, use `from mcp.shared.memory import create_connected_server_and_client_session`.
3. Record the substitution in this task's commit message, and adjust Task 12 accordingly.

- [ ] **Step 4: Record how the server tells whether the client supports elicitation**

Run:
```bash
.venv/Scripts/python - <<'EOF'
import inspect
from mcp.server.mcpserver import Context
print([n for n in dir(Context) if not n.startswith("_")])
EOF
```
Expected: the list includes `session` and `elicit`.

Task 12's `_supports_elicitation(ctx)` reads `ctx.session.client_params.capabilities.elicitation`. If `client_params` is not an attribute of the session class, find the attribute holding the client's `InitializeRequestParams`:

```bash
.venv/Scripts/python -c "import mcp.server.session as m, inspect; print(inspect.getsource(m)[:4000])"
```

Then use that attribute in Task 12, and note it in the commit message.

- [ ] **Step 5: Apply refinements 1–5 to the spec**

In `docs/superpowers/specs/2026-10-03-m2b-onboarding-design.md`:
- In §5, replace "`.env`, `IMPORTER_ENV` and `python-dotenv` are removed." with "`.env` and `IMPORTER_ENV` are removed (`python-dotenv` stays: `db.py` parses `database.env` with it)."
- In §11, replace the bullet "Remove `python-dotenv`. …" with "Keep `python-dotenv` (it parses `database.env`)."
- In §7.1, after the `state` field list, add: "`revising` is not used: a revision is another `submit_mapping` carrying a `note`."
- Append a section `## 15. Refinements made while planning` that lists refinements 1–5 from the plan header verbatim.

- [ ] **Step 6: Run the existing suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all passed (171), nothing failing.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml docs/superpowers/specs/2026-10-03-m2b-onboarding-design.md
git commit -m "Add M2b dependencies (mcp, azure blob) and record planning refinements

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Destination addresses

**Files:**
- Create: `src/importer/addresses.py`
- Test: `tests/test_addresses.py`

**Interfaces:**
- Produces:
  - `AddressError(ValueError)`.
  - `Address(database, schema, table)`, frozen, with properties `key`, `raw_schema`, `stage_table` and `pk_name`.
  - `parse(text: str) -> Address`.
  - `collision(address: Address, existing: Iterable[str]) -> str | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_addresses.py`:
```python
from __future__ import annotations

import pytest

from importer.addresses import AddressError, collision, parse


def test_parse_lowercases_and_derives_physical_names():
    a = parse("NPVCS.relational.URL_Template")
    assert a.key == "npvcs.relational.url_template"
    assert (a.raw_schema, a.table) == ("npvcs_relational", "url_template")
    assert a.stage_table == "npvcs_relational__url_template"
    assert a.pk_name == "pk_npvcs_relational__url_template"


@pytest.mark.parametrize("text", ["npvcs.url_template", "a.b.c.d", "", "npvcs..t", "np-vcs.dbo.t", "npvcs.dbo.my table"])
def test_malformed_addresses_are_refused(text):
    with pytest.raises(AddressError):
        parse(text)


def test_reserved_names():
    with pytest.raises(AddressError, match="reserved"):
        parse("db.datareader.t")          # raw schema db_datareader
    assert parse("sys.x.t").raw_schema == "sys_x"   # not itself reserved


def test_identifier_length_limit():
    with pytest.raises(AddressError, match="128"):
        parse(f"npvcs.dbo.{'t' * 125}")   # stage name npvcs_dbo__ttt... > 128


def test_collision_detects_same_raw_or_stage_table():
    assert collision(parse("a_b.c.t"), ["a.b_c.t"]) == "a.b_c.t"
    assert collision(parse("x.y.a__b"), ["x.y__a.b"]) == "x.y__a.b"   # same stage table
    assert collision(parse("npvcs.dbo.t"), ["npvcs.dbo.t", "rx.dbo.t"]) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_addresses.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.addresses'`.

- [ ] **Step 3: Implement**

`src/importer/addresses.py`:
```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_addresses.py -v`
Expected: 10 passed (4 tests, plus 6 parametrized malformed-address cases).

- [ ] **Step 5: Commit**

```bash
git add src/importer/addresses.py tests/test_addresses.py
git commit -m "Add destination addresses and derived raw-zone names

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Config artifact and shared settings

**Files:**
- Modify: `src/importer/models.py` (`ArtifactType`)
- Modify: `src/importer/repositories.py` (`ArtifactRow.approved_by`, `ArtifactRepository.active`, `ArtifactRepository.activate`)
- Modify: `src/importer/memory_store.py` (`MemoryArtifacts`)
- Create: `src/importer/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `Tx.artifacts`, `Tx.audit`.
- Produces:
  - `ArtifactType.CONFIG = "config"`.
  - `ArtifactRow.approved_by: str | None = None`.
  - `ArtifactRepository.active(artifact_type: ArtifactType, key: str) -> ArtifactRow | None`.
  - `ArtifactRepository.activate(version_id: int, *, approved_by: str | None) -> ArtifactRow`.
  - `settings`:
    - constant `CONFIG_KEY = "importer"`;
    - exception `SettingsError(ValueError)`;
    - dataclass `Destination(name, server, database)`;
    - dataclass `Settings(destinations: dict[str, Destination], max_values: int, max_chars: int)` with `.destination(name) -> Destination | None`;
    - `document(artifacts) -> dict`;
    - `load(artifacts) -> Settings`;
    - `set_destination(tx, name, *, server, database, by) -> ArtifactRow`;
    - `unset_destination(tx, name, *, by) -> ArtifactRow`;
    - `set_value(tx, key, value, *, by) -> ArtifactRow`.

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:
```python
from __future__ import annotations

import pytest

from importer import settings
from importer.memory_store import MemoryStore
from importer.models import ArtifactType


def test_defaults_without_a_config_version():
    store = MemoryStore()
    with store.transaction() as tx:
        s = settings.load(tx.artifacts)
    assert s.destinations == {} and (s.max_values, s.max_chars) == (5, 200)
    assert s.destination("NPVCS") is None


def test_set_and_unset_destination_versions_and_logs():
    store = MemoryStore()
    with store.transaction() as tx:
        settings.set_destination(tx, "npvcs", server="cssnpi", database="NPVCS", by="tony")
        settings.set_destination(tx, "RX", server="cssnpi", database="RX", by="tony")
        s = settings.load(tx.artifacts)
        assert s.destination("NpVcS").database == "NPVCS"
        assert sorted(s.destinations) == ["NPVCS", "RX"]
        settings.unset_destination(tx, "rx", by="tony")
        assert settings.load(tx.artifacts).destination("RX") is None
    configs = [a for a in store.state.artifacts.values() if a.artifact_type is ArtifactType.CONFIG]
    assert [a.version_no for a in configs] == [1, 2, 3]
    assert [a.is_active for a in configs] == [False, False, True]
    assert [d["action_type"] for d in store.state.decisions] == ["config_change"] * 3


def test_set_value_and_validation():
    store = MemoryStore()
    with store.transaction() as tx:
        settings.set_value(tx, "samples.max_values", 3, by="t")
        assert settings.load(tx.artifacts).max_values == 3
        with pytest.raises(settings.SettingsError):
            settings.set_value(tx, "samples.nope", 3, by="t")
        with pytest.raises(settings.SettingsError):
            settings.set_value(tx, "samples.max_chars", 0, by="t")
        with pytest.raises(settings.SettingsError):
            settings.set_destination(tx, "bad name", server="s", database="d", by="t")
        with pytest.raises(settings.SettingsError):
            settings.unset_destination(tx, "MISSING", by="t")


def test_artifacts_active_and_activate():
    store = MemoryStore()
    with store.transaction() as tx:
        v1 = tx.artifacts.put_version(ArtifactType.MAPPER, "sig.1", {"kind": "tabular"}, created_by="t", activate=False)
        assert tx.artifacts.active(ArtifactType.MAPPER, "sig.1") is None
        row = tx.artifacts.activate(v1.version_id, approved_by="tony")
        assert row.is_active and row.approved_by == "tony"
        assert tx.artifacts.active(ArtifactType.MAPPER, "sig.1").version_id == v1.version_id
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_settings.py -v`
Expected: FAIL with `ImportError: cannot import name 'settings'`.

- [ ] **Step 3: Implement the model and repository changes**

In `src/importer/models.py`, change `ArtifactType` to:
```python
class ArtifactType(str, Enum):
    """Versioned artifact kinds in ``artifact_versions`` (§2, §8.10, §8.17)."""

    SCHEMA = "schema"
    MAPPER = "mapper"
    RULE = "rule"
    CONFIG = "config"
```

In `src/importer/repositories.py`, give `ArtifactRow` a final field `approved_by: str | None = None`. Then add two methods to `ArtifactRepository`:
```python
    def active(self, artifact_type: ArtifactType, key: str) -> ArtifactRow | None: ...
    def activate(self, version_id: int, *, approved_by: str | None) -> ArtifactRow: ...
```

In `src/importer/memory_store.py`, add to `MemoryArtifacts`:
```python
    def active(self, artifact_type, key):
        return next(
            (replace(a) for a in self.s.artifacts.values()
             if a.artifact_type is artifact_type and a.artifact_key == key and a.is_active),
            None,
        )

    def activate(self, version_id, *, approved_by):
        try:
            row = self.s.artifacts[version_id]
        except KeyError:
            raise NotFound(version_id) from None
        for a in self.s.artifacts.values():
            if a.artifact_type is row.artifact_type and a.artifact_key == row.artifact_key:
                a.is_active = a.version_id == version_id
        row.approved_by = approved_by
        return replace(row)
```

- [ ] **Step 4: Implement `settings.py`**

`src/importer/settings.py`:
```python
"""Shared settings in the control database (design §8.17).

Every client and user must behave identically, so settings are a versioned
`config` artifact, never a local file. Only the pointer to the control database
(the DataEngine `importer` entry in database.env) is local.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from .models import ArtifactType
from .repositories import ArtifactRow

CONFIG_KEY = "importer"
DEFAULT_SAMPLES = {"max_values": 5, "max_chars": 200}
NUMERIC_KEYS = frozenset({"samples.max_values", "samples.max_chars"})
_NAME = re.compile(r"^[A-Za-z0-9_]+$")


class SettingsError(ValueError):
    """An invalid settings change."""


@dataclass(frozen=True)
class Destination:
    name: str
    server: str
    database: str


@dataclass(frozen=True)
class Settings:
    destinations: dict[str, Destination]
    max_values: int
    max_chars: int

    def destination(self, name: str) -> Destination | None:
        return self.destinations.get(name.upper())


def document(artifacts) -> dict:
    row = artifacts.active(ArtifactType.CONFIG, CONFIG_KEY)
    doc = copy.deepcopy(row.definition) if row else {}
    doc["destinations"] = {k.upper(): v for k, v in doc.get("destinations", {}).items()}
    doc["samples"] = {**DEFAULT_SAMPLES, **doc.get("samples", {})}
    return doc


def load(artifacts) -> Settings:
    doc = document(artifacts)
    destinations = {
        name: Destination(name, entry["server"], entry["database"]) for name, entry in doc["destinations"].items()
    }
    return Settings(destinations, int(doc["samples"]["max_values"]), int(doc["samples"]["max_chars"]))


def _save(tx, doc: dict, *, by: str, change: dict) -> ArtifactRow:
    row = tx.artifacts.put_version(ArtifactType.CONFIG, CONFIG_KEY, doc, created_by=by)
    tx.audit.decision(None, "config_change", observed=None, proposed=change, applied=True, approval=by)
    return row


def set_destination(tx, name: str, *, server: str, database: str, by: str) -> ArtifactRow:
    if not _NAME.match(name or ""):
        raise SettingsError(f"destination name {name!r} must match [A-Za-z0-9_]+")
    if not server or not database:
        raise SettingsError("a destination needs --server and --database")
    doc = document(tx.artifacts)
    doc["destinations"][name.upper()] = {"server": server, "database": database}
    return _save(tx, doc, by=by, change={"set_destination": name.upper(), "server": server, "database": database})


def unset_destination(tx, name: str, *, by: str) -> ArtifactRow:
    doc = document(tx.artifacts)
    if doc["destinations"].pop(name.upper(), None) is None:
        raise SettingsError(f"no destination named {name!r}")
    return _save(tx, doc, by=by, change={"unset_destination": name.upper()})


def set_value(tx, key: str, value: int, *, by: str) -> ArtifactRow:
    if key not in NUMERIC_KEYS:
        raise SettingsError(f"unknown setting {key!r}; settable: {sorted(NUMERIC_KEYS)}")
    if not isinstance(value, int) or value < 1:
        raise SettingsError(f"{key} must be a positive integer")
    doc = document(tx.artifacts)
    doc["samples"][key.split(".", 1)[1]] = value
    return _save(tx, doc, by=by, change={key: value})
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_settings.py -v && .venv/Scripts/python -m pytest`
Expected: 4 passed, then the full suite passes.

- [ ] **Step 6: Commit**

```bash
git add src/importer/models.py src/importer/repositories.py src/importer/memory_store.py src/importer/settings.py tests/test_settings.py
git commit -m "Add shared settings as a versioned config artifact

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Address-based schema registration and test migration

**Files:**
- Modify: `src/importer/admin.py`
- Modify: `src/importer/mappers/spec.py:21-22,113-114`
- Modify: `tests/support.py`, `tests/fixtures/formulary_spec.json`, `tests/fixtures/formulary_schemas.json`
- Modify (target renames): `tests/test_pipeline.py`, `tests/test_spec_tabular.py`, `tests/test_spec_xml.py`, `tests/test_loading_and_admin.py`, `tests/test_cli.py`, `tests/test_profiler.py`, `tests/integration/test_acceptance.py`
- Test: `tests/test_loading_and_admin.py` (new tests for the registration outcome)

**Interfaces:**
- Consumes:
  - `addresses.parse` and `addresses.collision`;
  - `settings.load`;
  - `schema_tables.validate_columns`.
- Produces:
  - `admin.SchemaRefused(ValueError)`.
  - `admin.SchemaOutcome(address: str, result: str, version_id: int | None, new_columns: list[str], dropped_at_source: list[str])` with `to_dict()`. `result` is one of `"added"`, `"unchanged"`, `"changed"`.
  - `admin.register_schema(tx, address: str, columns: list[str], *, created_by: str | None, source: str = "manual") -> SchemaOutcome`.
  - Schema artifact definitions become `{"columns": [...], "source": <source>}`.
  - `tests.support`:
    - constants `URL_TARGET = "npvcs.relational.url_template"`, `EVENT_TARGET = "rx.dbo.event"` and `FORMULARY_TARGETS` (a 3-tuple);
    - `configure(store, *names)`;
    - `setup_signature` configures destinations automatically.

- [ ] **Step 1: Rename schema ids in the tests and fixtures**

Run:
```bash
sed -i 's/"url_template"/"npvcs.relational.url_template"/g' tests/*.py tests/integration/*.py
sed -i 's/"event"/"rx.dbo.event"/g' tests/support.py tests/test_pipeline.py tests/test_loading_and_admin.py
sed -i -E 's/"(formulary_benefit_definition|formulary_tier_definition|formulary_drug_list)"/"npvcs.xml.\1"/g' tests/*.py tests/integration/*.py tests/fixtures/formulary_spec.json tests/fixtures/formulary_schemas.json
grep -rn "looks like url_template" tests
```
Expected: the grep prints the two summary assertions in `tests/test_cli.py` and `tests/test_profiler.py`. Edit them by hand:
- `tests/test_cli.py`: `assert result["proposal_summary"].startswith("looks like npvcs.relational.url_template")`
- `tests/test_profiler.py`: `assert best.summary() == "looks like npvcs.relational.url_template (0.67; extra: url; missing: fileid, issuerid, newurl, runid)"`

- [ ] **Step 2: Update `tests/support.py`**

Add after the imports:
```python
URL_TARGET = "npvcs.relational.url_template"
EVENT_TARGET = "rx.dbo.event"
FORMULARY_TARGETS = (
    "npvcs.xml.formulary_benefit_definition", "npvcs.xml.formulary_tier_definition", "npvcs.xml.formulary_drug_list",
)


def configure(store, *names: str) -> None:
    """Register destinations in the shared settings (what `importer config set destination` does)."""
    from importer import settings

    with store.transaction() as tx:
        have = settings.load(tx.artifacts)
        for name in names:
            if have.destination(name) is None:
                settings.set_destination(tx, name, server="test", database=name.upper(), by="test")


def configure_for(store, schemas: dict) -> None:
    configure(store, *sorted({key.split(".")[0] for key in schemas}))
```

At the top of `setup_signature`'s body, add the line `configure_for(store, schemas)`.

- [ ] **Step 3: Write the failing registration tests**

In `tests/test_loading_and_admin.py`:
- change the import line to `from importer.admin import SchemaRefused, register_mapper, register_schema, register_signature`;
- add `URL_TARGET` and `configure` to the `tests.support` import;
- replace `_schemas` and `test_register_schema_validates_columns` with:

```python
def _schemas(tx, schemas):
    from importer import settings
    for name in sorted({key.split(".")[0] for key in schemas}):
        if settings.load(tx.artifacts).destination(name) is None:
            settings.set_destination(tx, name, server="test", database=name.upper(), by="test")
    for schema_id, columns in schemas.items():
        register_schema(tx, schema_id, columns, created_by="test")


def test_register_schema_validates_columns_and_address():
    store = MemoryStore()
    configure(store, "npvcs")
    with store.transaction() as tx:
        with pytest.raises(ValueError, match="reserved"):
            register_schema(tx, "npvcs.dbo.s", ["_file_id"], created_by="t")
        with pytest.raises(ValueError, match="duplicate"):
            register_schema(tx, "npvcs.dbo.s", ["a", "A"], created_by="t")
        with pytest.raises(ValueError, match="must match"):
            register_schema(tx, "npvcs.dbo.s", ["bad col"], created_by="t")
        with pytest.raises(ValueError, match="DATABASE.SCHEMA.TABLE"):
            register_schema(tx, "s", ["a"], created_by="t")
        with pytest.raises(SchemaRefused, match="not a configured destination"):
            register_schema(tx, "rx.dbo.s", ["a"], created_by="t")


def test_register_schema_outcomes_added_unchanged_changed():
    store = MemoryStore()
    configure(store, "npvcs")
    with store.transaction() as tx:
        first = register_schema(tx, "NPVCS.relational.URL_Template", ["urlid", "newurl"], created_by="t", source="live")
        same = register_schema(tx, URL_TARGET, ["NEWURL", "urlid"], created_by="t")
        grown = register_schema(tx, URL_TARGET, ["urlid", "issuerid"], created_by="t")
        active = tx.artifacts.active_schema(URL_TARGET)
    assert (first.result, first.address) == ("added", URL_TARGET)
    assert same.result == "unchanged" and same.version_id == first.version_id
    assert grown.result == "changed" and grown.new_columns == ["issuerid"] and grown.dropped_at_source == ["newurl"]
    assert active.definition == {"columns": ["urlid", "newurl", "issuerid"], "source": "manual"}
    assert first.to_dict()["result"] == "added"


def test_register_schema_refuses_collisions():
    store = MemoryStore()
    configure(store, "a_b", "a")
    with store.transaction() as tx:
        register_schema(tx, "a_b.c.t", ["x"], created_by="t")
        with pytest.raises(SchemaRefused, match="same raw table as a_b.c.t"):
            register_schema(tx, "a.b_c.t", ["x"], created_by="t")
```

In `tests/test_cli.py`, change the `store` fixture body to:
```python
    s = MemoryStore()
    from importer.admin import register_schema
    from tests.support import configure
    configure(s, "npvcs")
    with s.transaction() as tx:
        register_schema(tx, "npvcs.relational.url_template", URL_SCHEMA["npvcs.relational.url_template"], created_by="test")
    return s
```

In `tests/test_profiler.py`:
- add `configure` to the `tests.support` import;
- in `test_tabular_overlap_names_extra_and_missing_columns` and `test_low_overlap_is_not_proposed`, insert `configure(store, "npvcs")` right after `store = MemoryStore()`.

- [ ] **Step 4: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_loading_and_admin.py -v`
Expected: FAIL with `ImportError: cannot import name 'SchemaRefused'`.

- [ ] **Step 5: Relax spec targets**

In `src/importer/mappers/spec.py`, replace lines 21–22:
```python
IDENT = re.compile(r"^[A-Za-z0-9_]+$")
PARENT = re.compile(r"^\$parent\(([A-Za-z0-9_]+)\)$")
```
with:
```python
IDENT = re.compile(r"^[A-Za-z0-9_]+$")
TARGET = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,2}$")
PARENT = re.compile(r"^\$parent\(([A-Za-z0-9_.]+)\)$")
```
Then in `_parse_extraction`, replace:
```python
    if not isinstance(target, str) or not IDENT.match(target):
        raise SpecError(f"target must match [A-Za-z0-9_]+, got {target!r}")
```
with:
```python
    if not isinstance(target, str) or not TARGET.match(target):
        raise SpecError(f"target must be a schema address (database.schema.table), got {target!r}")
```

- [ ] **Step 6: Rewrite `register_schema`**

Replace `src/importer/admin.py` lines 1–29 (module docstring through the end of `register_schema`) with:
```python
"""Registration helpers shared by the CLI, onboarding and tests (§8.1, §8.15, §8.16)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .addresses import collision, parse
from .fingerprint import fingerprint
from .mappers.loading import PLUGINS_DIR, file_sha256, load_mapper
from .models import ArtifactType, Declared
from .readers import parse_document
from .repositories import ArtifactRow, Tx
from .schema_tables import validate_columns
from .settings import load as load_settings


class SchemaRefused(ValueError):
    """A schema registration that breaks the namespace rules (§8.15)."""


@dataclass(frozen=True)
class SchemaOutcome:
    address: str
    result: str  # added | unchanged | changed
    version_id: int | None = None
    new_columns: list[str] = field(default_factory=list)
    dropped_at_source: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def register_schema(tx: Tx, address: str, columns: list[str], *, created_by: str | None,
                    source: str = "manual") -> SchemaOutcome:
    addr = parse(address)
    if load_settings(tx.artifacts).destination(addr.database) is None:
        raise SchemaRefused(
            f"{addr.key}: database {addr.database!r} is not a configured destination "
            f"(importer config set destination {addr.database.upper()} --server ... --database ...)"
        )
    validate_columns(list(columns))
    other = collision(addr, [a.artifact_key for a in tx.artifacts.active_schemas()])
    if other:
        raise SchemaRefused(f"{addr.key} maps to the same raw table as {other}")
    current = tx.artifacts.active_schema(addr.key)
    if current is None:
        row = tx.artifacts.put_version(ArtifactType.SCHEMA, addr.key, {"columns": list(columns), "source": source},
                                       created_by=created_by)
        return SchemaOutcome(addr.key, "added", row.version_id, list(columns))
    have = list(current.definition["columns"])
    have_lower = {c.lower() for c in have}
    incoming_lower = {c.lower() for c in columns}
    new = [c for c in columns if c.lower() not in have_lower]
    gone = [c for c in have if c.lower() not in incoming_lower]
    if not new:
        return SchemaOutcome(addr.key, "unchanged", current.version_id, [], gone)
    row = tx.artifacts.put_version(ArtifactType.SCHEMA, addr.key, {"columns": have + new, "source": source},
                                   created_by=created_by)
    return SchemaOutcome(addr.key, "changed", row.version_id, new, gone)
```

Leave `register_mapper` and `register_signature` unchanged.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all pass. If a test still fails, it most likely asserts a raw-table key or `schema_id` that the Step 1 `sed` did not reach (for example a bare `formulary_drug_list` without quotes); fix that assertion to the address form.

- [ ] **Step 8: Commit**

```bash
git add -A src/importer/admin.py src/importer/mappers/spec.py tests
git commit -m "Identify schemas by destination address; version on drift

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Address-based raw zone in SQL Server, and the DDL

**Files:**
- Modify: `src/importer/schema_tables.py`
- Modify: `src/importer/mssql_store.py` (`ART_COLS`, `_artifact_row`, `MssqlArtifacts`, `MssqlRawZone`)
- Modify: `sql/001_schema.sql`
- Test: `tests/test_schema_tables.py`, `tests/integration/test_mssql_store.py`

**Interfaces:**
- Consumes: `addresses.Address`, `addresses.parse`.
- Produces from `schema_tables`:
  - `raw_name(a: Address) -> str`, giving `[npvcs_relational].[url_template]`;
  - `stage_name(a: Address) -> str`, giving `[stage].[npvcs_relational__url_template]`;
  - `create_raw_sql(a: Address, columns)` and `create_stage_sql(a: Address, columns)`;
  - `add_column_sql(zone: str, a: Address, column: str)`;
  - `ensure_schema_sql(name: str) -> str`;
  - `REBUILD_DROP_SQL` (uses the `importer_owned` mark).
  - `ENSURE_SCHEMAS_SQL` is removed. `information_schema_sql` stays until Task 6.
- Produces from `mssql_store`: `MssqlArtifacts.active`, `MssqlArtifacts.activate`, and raw-zone methods that take addresses.

- [ ] **Step 1: Write the failing unit tests**

Replace `tests/test_schema_tables.py` with:
```python
from __future__ import annotations

from pathlib import Path

import pytest

from importer.addresses import parse
from importer.schema_tables import (
    REBUILD_DROP_SQL, add_column_sql, create_raw_sql, create_stage_sql, ensure_schema_sql, q, raw_name,
    stage_name, validate_columns,
)

DDL = (Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql").read_text(encoding="utf-8")
URL = parse("NPVCS.relational.url_template")


def test_names():
    assert raw_name(URL) == "[npvcs_relational].[url_template]"
    assert stage_name(URL) == "[stage].[npvcs_relational__url_template]"


def test_raw_table_has_lineage_canonical_columns_and_pk():
    sql = create_raw_sql(URL, ["urlid", "newurl"])
    assert sql.startswith("CREATE TABLE [npvcs_relational].[url_template] (")
    for fragment in ("[_file_id] BIGINT NOT NULL", "[_row_identity] NVARCHAR(256) NOT NULL",
                     "[_extras] NVARCHAR(MAX) NULL", "[_landed_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()",
                     "[urlid] NVARCHAR(MAX) NULL",
                     "CONSTRAINT [pk_npvcs_relational__url_template] PRIMARY KEY CLUSTERED ([_file_id], [_row_identity])"):
        assert fragment in sql


def test_stage_table_is_a_heap_without_landed_at():
    sql = create_stage_sql(URL, ["urlid"])
    assert sql.startswith("CREATE TABLE [stage].[npvcs_relational__url_template] (")
    assert "PRIMARY KEY" not in sql and "_landed_at" not in sql


def test_add_column_sql():
    assert add_column_sql("raw", URL, "newcol") == "ALTER TABLE [npvcs_relational].[url_template] ADD [newcol] NVARCHAR(MAX) NULL"
    assert add_column_sql("stage", URL, "newcol") == "ALTER TABLE [stage].[npvcs_relational__url_template] ADD [newcol] NVARCHAR(MAX) NULL"
    with pytest.raises(ValueError):
        add_column_sql("dbo", URL, "x")


def test_ensure_schema_marks_importer_owned():
    sql = ensure_schema_sql("npvcs_relational")
    assert "CREATE SCHEMA [npvcs_relational]" in sql and "importer_owned" in sql
    with pytest.raises(ValueError):
        ensure_schema_sql("x];DROP TABLE y;--")


def test_rebuild_drops_by_mark_and_keeps_identity():
    assert "importer_owned" in REBUILD_DROP_SQL
    assert "database_identity" not in REBUILD_DROP_SQL


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
    "'schema','mapper','rule','config'", "artifact_key      NVARCHAR(128) NOT NULL", "content_sha256",
    "mapper_version_id BIGINT NULL REFERENCES dbo.artifact_versions(version_id)", "physical_part",
    "xml_namespace", "observed_schema", "[class]", "attempts          INT NOT NULL DEFAULT 1",
    "last_error", "request_context", "signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id)",
])
def test_control_plane_ddl_has_m2b_columns(fragment):
    assert fragment in DDL


def test_ddl_creates_referenced_tables_first():
    assert DDL.index("CREATE TABLE dbo.artifact_versions") < DDL.index("CREATE TABLE dbo.signature_registry") < DDL.index("CREATE TABLE dbo.file_ledger")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_schema_tables.py -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_schema_sql'`.

- [ ] **Step 3: Rewrite `schema_tables.py`**

Replace the whole of `src/importer/schema_tables.py`, keeping `information_schema_sql` at the end until Task 6:
```python
"""Raw/stage DDL generation from schema definitions (design §8.10, §8.11, §8.15).

Stable code generates every physical table from versioned schema data; the agent
never writes DDL. Names come from the destination address; identifiers are
validated and bracket-quoted. Schemas the importer creates carry the extended
property importer_owned = 1, which is what `db rebuild` drops by.
"""

from __future__ import annotations

import re

from .addresses import Address

IDENT = re.compile(r"^[A-Za-z0-9_]+$")
OWNED_PROPERTY = "importer_owned"
LINEAGE_COLUMNS = ["_file_id", "_row_identity", "_signature_id", "_artifact_version_id", "_extras"]
_LINEAGE_TYPES = {
    "_file_id": "BIGINT NOT NULL",
    "_row_identity": "NVARCHAR(256) NOT NULL",
    "_signature_id": "BIGINT NOT NULL",
    "_artifact_version_id": "BIGINT NOT NULL",
    "_extras": "NVARCHAR(MAX) NULL",
}

_OWNED_SCHEMAS = f"""(s.name IN (N'raw', N'stage') OR EXISTS (
    SELECT 1 FROM sys.extended_properties AS ep
    WHERE ep.class = 3 AND ep.major_id = s.schema_id AND ep.name = N'{OWNED_PROPERTY}'))"""

REBUILD_DROP_SQL = f"""
DECLARE @sql NVARCHAR(MAX) = N'';
SELECT @sql += N'DROP TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N';'
FROM sys.tables AS t JOIN sys.schemas AS s ON s.schema_id = t.schema_id
WHERE {_OWNED_SCHEMAS};
EXEC sp_executesql @sql;
SET @sql = N'';
SELECT @sql += N'DROP SCHEMA ' + QUOTENAME(s.name) + N';'
FROM sys.schemas AS s
WHERE {_OWNED_SCHEMAS};
EXEC sp_executesql @sql;
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


def raw_name(a: Address) -> str:
    return f"{q(a.raw_schema)}.{q(a.table)}"


def stage_name(a: Address) -> str:
    return f"[stage].{q(a.stage_table)}"


def ensure_schema_sql(name: str) -> str:
    validate_identifier(name, "schema")
    return (
        f"IF SCHEMA_ID(N'{name}') IS NULL BEGIN "
        f"EXEC(N'CREATE SCHEMA {q(name)}'); "
        f"EXEC sys.sp_addextendedproperty @name = N'{OWNED_PROPERTY}', @value = 1, "
        f"@level0type = N'SCHEMA', @level0name = N'{name}'; END"
    )


def _columns_sql(columns: list[str]) -> list[str]:
    validate_columns(columns)
    return [f"{q(c)} NVARCHAR(MAX) NULL" for c in columns]


def create_raw_sql(a: Address, columns: list[str]) -> str:
    parts = [f"{q(n)} {_LINEAGE_TYPES[n]}" for n in LINEAGE_COLUMNS]
    parts.append("[_landed_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
    parts += _columns_sql(columns)
    parts.append(f"CONSTRAINT {q(a.pk_name)} PRIMARY KEY CLUSTERED ([_file_id], [_row_identity])")
    return f"CREATE TABLE {raw_name(a)} (\n    " + ",\n    ".join(parts) + "\n)"


def create_stage_sql(a: Address, columns: list[str]) -> str:
    parts = [f"{q(n)} {_LINEAGE_TYPES[n]}" for n in LINEAGE_COLUMNS] + _columns_sql(columns)
    return f"CREATE TABLE {stage_name(a)} (\n    " + ",\n    ".join(parts) + "\n)"


def add_column_sql(zone: str, a: Address, column: str) -> str:
    if zone not in ("raw", "stage"):
        raise ValueError(f"zone must be raw or stage, got {zone!r}")
    table = raw_name(a) if zone == "raw" else stage_name(a)
    return f"ALTER TABLE {table} ADD {q(column)} NVARCHAR(MAX) NULL"


def information_schema_sql(database: str) -> str:
    """Removed in Task 6 (schema import moves to per-destination connections)."""
    return (
        f"SELECT COLUMN_NAME FROM {q(database)}.INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION"
    )
```

- [ ] **Step 4: Update `sql/001_schema.sql`**

Make these edits:
1. **Header comment.** Replace lines 4–5:
   ```
      Raw landing-zone tables are PER SCHEMA (raw.<schema_id>, stage.<schema_id>)
      and are generated by the importer from versioned schema definitions.
   ```
   with:
   ```
      Raw landing-zone tables are named by destination address (§8.15):
      [<db>_<schema>].[<table>] and [stage].[<db>_<schema>__<table>], in schemas the
      importer creates and marks importer_owned. They are generated from versioned
      schema definitions.
   ```
2. **`artifact_versions`.** Change the check to `CHECK (artifact_type IN ('schema','mapper','rule','config'))`. Change the comments:
   - `artifact_key`: `-- schema: address | mapper: sig.<signature_id> or a name | rule: schema address | config: 'importer'`
   - `definition`: `-- JSON: {columns, source} | mapping spec | rule-as-data | settings`
   - The table's header comment: `/* 3. ARTIFACT VERSIONS — schema, mapper, repair-rule & config versions; never edited in place. §2,§5,§8.9-8.11,§8.17 */`
3. **`signature_registry.proposal`.** New comment: `-- JSON: {candidates: [...], review: {state, draft_version_id, path, rationale, preview, notes, history}} (§8.18)`
4. **`file_ledger.source_path`.** New comment: `-- locator, verbatim: \\server\share\..., \\<acct>.file.core.windows.net\..., https://<acct>.blob.core.windows.net/... (§8.21)`
5. **`reject_repository.schema_id`.** New comment: `-- target schema address`
6. **`decision_log.action_type`.** Extend the comment with `| config_change | schema_import | draft_mapping | reject_mapping | mcp:<tool>`.

- [ ] **Step 5: Update the SQL Server store**

In `src/importer/mssql_store.py`:
- Change the `schema_tables` import to:
  ```python
  from .addresses import parse
  from .schema_tables import (
      LINEAGE_COLUMNS,
      add_column_sql,
      create_raw_sql,
      create_stage_sql,
      ensure_schema_sql,
      q,
      raw_name,
      stage_name,
      validate_columns,
  )
  ```
- Change `ART_COLS` to `"version_id, artifact_type, artifact_key, version_no, definition, is_active, created_by, content_sha256, approved_by"`, and add `approved_by=m["approved_by"]` to `_artifact_row`.
- Add to `MssqlArtifacts`:
  ```python
      def active(self, artifact_type, key):
          m = self.c.execute(text(
              f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE artifact_type = :t AND artifact_key = :k AND is_active = 1"
          ), {"t": artifact_type.value, "k": key}).mappings().first()
          return None if m is None else _artifact_row(m)

      def activate(self, version_id, *, approved_by):
          row = self.get(version_id)
          self.c.execute(text(
              "UPDATE dbo.artifact_versions SET is_active = CASE WHEN version_id = :vid THEN 1 ELSE 0 END "
              "WHERE artifact_type = :t AND artifact_key = :k"
          ), {"vid": version_id, "t": row.artifact_type.value, "k": row.artifact_key})
          self.c.execute(text("UPDATE dbo.artifact_versions SET approved_by = :by WHERE version_id = :vid"),
                         {"by": approved_by, "vid": version_id})
          return self.get(version_id)
  ```
- Replace the methods of `MssqlRawZone` from `_canonical` through `count_landed`. Keep `_table_columns` and the `stage_batch` body after the `sql =` line as they are:
  ```python
      def _canonical(self, target: str) -> list[str]:
          a = parse(target)
          if a.key not in self._columns:
              existing = self._table_columns("stage", a.stage_table)
              if existing is None:
                  raise RuntimeError(f"{stage_name(a)} does not exist; ensure_tables was never called for {a.key}")
              self._columns[a.key] = [c for c in existing if not c.startswith("_")]
          return self._columns[a.key]

      def ensure_tables(self, schema_id, columns):
          a = parse(schema_id)
          validate_columns(columns)
          self.c.exec_driver_sql(ensure_schema_sql(a.raw_schema))
          self.c.exec_driver_sql(ensure_schema_sql("stage"))
          for zone, schema, table, create in (("raw", a.raw_schema, a.table, create_raw_sql),
                                              ("stage", "stage", a.stage_table, create_stage_sql)):
              existing = self._table_columns(schema, table)
              if existing is None:
                  self.c.exec_driver_sql(create(a, columns))
                  continue
              have = {c.lower() for c in existing}
              for column in columns:
                  if column.lower() not in have:
                      self.c.exec_driver_sql(add_column_sql(zone, a, column))
          self._columns.pop(a.key, None)

      def clear_stage(self, file_id, target):
          self.c.execute(text(f"DELETE FROM {stage_name(parse(target))} WHERE _file_id = :f"), {"f": file_id})
  ```
- In `stage_batch`, change the `sql =` assignment to:
  ```python
          sql = (f"INSERT INTO {stage_name(parse(target))} ({', '.join(q(n) for n in names)}) "
                 f"VALUES ({', '.join('?' for _ in names)})")
  ```
- Replace `promote` and `count_landed` with:
  ```python
      def promote(self, file_id, target):
          a = parse(target)
          names = LINEAGE_COLUMNS + self._canonical(target)
          column_list = ", ".join(q(n) for n in names)
          inserted = self.c.execute(text(
              f"INSERT INTO {raw_name(a)} ({column_list}) "
              f"SELECT {', '.join('s.' + q(n) for n in names)} FROM {stage_name(a)} AS s "
              f"WHERE s._file_id = :f AND NOT EXISTS (SELECT 1 FROM {raw_name(a)} AS r "
              f"WHERE r._file_id = s._file_id AND r._row_identity = s._row_identity)"
          ), {"f": file_id}).rowcount
          self.c.execute(text(f"DELETE FROM {stage_name(a)} WHERE _file_id = :f"), {"f": file_id})
          return inserted

      def count_landed(self, file_id, target):
          a = parse(target)
          if self._table_columns(a.raw_schema, a.table) is None:
              return 0
          return self.c.execute(text(f"SELECT COUNT(*) FROM {raw_name(a)} WHERE _file_id = :f"), {"f": file_id}).scalar_one()
  ```
- Remove the now-unused `ENSURE_SCHEMAS_SQL` and `validate_identifier` imports.

- [ ] **Step 6: Update the SQL Server integration tests**

In `tests/integration/test_mssql_store.py`:
- In `test_registry_artifacts_and_audit`, after the `v2` assertions, add:
  ```python
          draft = tx.artifacts.put_version(ArtifactType.MAPPER, f"sig.{uid}", {"kind": "tabular"}, created_by="it", activate=False)
          assert tx.artifacts.active(ArtifactType.MAPPER, f"sig.{uid}") is None
          assert tx.artifacts.activate(draft.version_id, approved_by="it").approved_by == "it"
          assert tx.artifacts.active(ArtifactType.MAPPER, f"sig.{uid}").version_id == draft.version_id
  ```
- In `test_raw_zone_stage_promote_and_bisection`, change the first line to `target = f"import_agent_dev.it.t_{_uid()}"`.

- [ ] **Step 7: Run unit tests, rebuild DEV, run the store integration tests**

Run:
```bash
.venv/Scripts/python -m pytest
.venv/Scripts/importer db rebuild --confirm import_agent_dev
.venv/Scripts/python -m pytest -m integration tests/integration/test_mssql_store.py -v
```
Expected: the unit suite passes, then `rebuilt the importer schema in import_agent_dev`, then 5 passed. The rebuild still uses the M2a guard (`.env` `IMPORTER_ENV=DEV`) until Task 5. Do not run `tests/integration/test_acceptance.py` yet; it is migrated in Task 13.

- [ ] **Step 8: Mirror the DDL on Confluence**

Use the Atlassian MCP:
1. `getConfluencePage` for page `3775135746` (HTML).
2. Replace the page's DDL code block with the full new `sql/001_schema.sql`.
3. Add one line under the page's change notes: "October 3, 2026 — M2b: `config` artifact type; address-based raw naming; proposal / locator / reject comments (§8.15–§8.21)."
4. `updateConfluencePage`, keeping all other content.

- [ ] **Step 9: Commit**

```bash
git add src/importer/schema_tables.py src/importer/mssql_store.py sql/001_schema.sql tests/test_schema_tables.py tests/integration/test_mssql_store.py
git commit -m "Name raw and stage tables by destination address; add config artifact type

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Environment marker, DEV guard, target banner and the config CLI

**Files:**
- Create: `src/importer/environment.py`
- Modify: `sql/001_schema.sql` (add `database_identity` at the top)
- Modify: `src/importer/config.py` (remove `importer_env`)
- Modify: `src/importer/cli.py` (`db init`, `db rebuild` guard, banner, `config show|set|unset`)
- Test: `tests/test_environment.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `settings.document`, `settings.set_destination`, `settings.unset_destination`, `settings.set_value`.
- Produces from `environment`:
  - constants `IDENTITY_TABLE_SQL` and `ENVIRONMENTS = ("DEV", "PROD")`;
  - exception `GuardError(RuntimeError)`;
  - dataclass `Identity(server: str, database: str, environment: str | None)`;
  - functions:
    - `read(conn) -> Identity`;
    - `dev_guard(environment: str | None, database: str | None) -> str | None` (returns the refusal reason, or `None` when allowed);
    - `banner(identity) -> str`;
    - `init(conn, environment: str, *, by: str) -> None`;
    - `target_of(store) -> Identity | None` (`None` for stores without an engine).
- Produces CLI commands:
  - `importer db init --environment DEV|PROD`
  - `importer config show`
  - `importer config set destination NAME --server S --database D`
  - `importer config set samples.max_values N` (also `samples.max_chars`)
  - `importer config unset destination NAME`

- [ ] **Step 1: Write the failing tests**

`tests/test_environment.py`:
```python
from __future__ import annotations

from pathlib import Path

import pytest

from importer.environment import IDENTITY_TABLE_SQL, Identity, banner, dev_guard

DDL = (Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql").read_text(encoding="utf-8")


@pytest.mark.parametrize("environment,database,allowed", [
    ("DEV", "import_agent_dev", True),
    ("DEV", "IMPORT_AGENT_DEV", True),
    ("DEV", "import_agent", False),
    ("PROD", "import_agent_dev", False),
    (None, "import_agent_dev", False),
    (None, "import_agent", False),
])
def test_dev_guard_truth_table(environment, database, allowed):
    assert (dev_guard(environment, database) is None) is allowed


def test_banner():
    assert banner(Identity("2018NPI", "import_agent_dev", "DEV")) == "importer @ 2018NPI/import_agent_dev [DEV]"
    assert banner(Identity("s", "d", None)) == "importer @ s/d [UNSET]"


def test_identity_table_is_in_the_create_script_and_never_rebuilt():
    assert IDENTITY_TABLE_SQL in DDL
    from importer.schema_tables import REBUILD_DROP_SQL
    assert "database_identity" not in REBUILD_DROP_SQL
```

In `tests/test_cli.py`, replace the two `test_rebuild_*` tests with:
```python
class _FakeEngine:
    def connect(self):
        from contextlib import nullcontext
        return nullcontext(object())


def test_rebuild_refuses_without_dev_identity(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("s", "import_agent_dev", "PROD"))
    assert cli.main(["db", "rebuild", "--confirm", "import_agent_dev"]) == 1
    assert "not DEV" in capsys.readouterr().err


def test_rebuild_refuses_when_the_name_lacks_dev(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("s", "import_agent", "DEV"))
    assert cli.main(["db", "rebuild", "--confirm", "import_agent"]) == 1
    assert "does not contain 'dev'" in capsys.readouterr().err


def test_rebuild_requires_matching_database_name(monkeypatch, capsys):
    from importer.environment import Identity
    monkeypatch.setattr("importer.db.get_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("importer.environment.read", lambda conn: Identity("s", "imp_dev", "DEV"))
    assert cli.main(["db", "rebuild", "--confirm", "wrong"]) == 1
    assert "did not match" in capsys.readouterr().err


def test_config_set_show_unset(store, capsys):
    assert cli.main(["config", "set", "destination", "rx", "--server", "cssnpi", "--database", "RX"], store=store) == 0
    capsys.readouterr()
    assert cli.main(["config", "set", "samples.max_values", "3"], store=store) == 0
    capsys.readouterr()
    assert cli.main(["config", "show"], store=store) == 0
    shown = _json_out(capsys)
    assert shown["destinations"]["RX"] == {"server": "cssnpi", "database": "RX"}
    assert shown["samples"]["max_values"] == 3
    assert cli.main(["config", "unset", "destination", "RX"], store=store) == 0
    capsys.readouterr()
    cli.main(["config", "show"], store=store)
    assert "RX" not in _json_out(capsys)["destinations"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_environment.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.environment'`.

- [ ] **Step 3: Implement `environment.py`**

`src/importer/environment.py`:
```python
"""The database's own environment marker and the DEV guard (design §8.17).

Safeguards live in the database, not in local files: dev-only operations need
dbo.database_identity = 'DEV' AND a server-reported database name containing
'dev'. The identity row is written once by `importer db init` and never changed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

ENVIRONMENTS = ("DEV", "PROD")
IDENTITY_TABLE_SQL = """IF OBJECT_ID(N'dbo.database_identity') IS NULL
CREATE TABLE dbo.database_identity (
    id          TINYINT NOT NULL CONSTRAINT pk_database_identity PRIMARY KEY
                CONSTRAINT ck_identity_single CHECK (id = 1),
    environment VARCHAR(8) NOT NULL CONSTRAINT ck_identity_env CHECK (environment IN ('DEV','PROD')),
    created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    created_by  NVARCHAR(128) NOT NULL
);"""


class GuardError(RuntimeError):
    """An environment rule refused the operation."""


@dataclass(frozen=True)
class Identity:
    server: str
    database: str
    environment: str | None


def read(conn) -> Identity:
    server, database = conn.execute(text("SELECT @@SERVERNAME, DB_NAME()")).one()
    exists = conn.execute(text("SELECT OBJECT_ID(N'dbo.database_identity')")).scalar_one() is not None
    environment = None
    if exists:
        environment = conn.execute(text("SELECT environment FROM dbo.database_identity WHERE id = 1")).scalar_one_or_none()
    return Identity(server, database, environment)


def dev_guard(environment: str | None, database: str | None) -> str | None:
    if environment != "DEV":
        return f"database_identity is {environment or 'not set'}, not DEV (importer db init --environment DEV on a DEV database)"
    if "dev" not in (database or "").lower():
        return f"database name {database!r} does not contain 'dev'"
    return None


def banner(identity: Identity) -> str:
    return f"importer @ {identity.server}/{identity.database} [{identity.environment or 'UNSET'}]"


def init(conn, environment: str, *, by: str) -> None:
    env = (environment or "").upper()
    if env not in ENVIRONMENTS:
        raise GuardError(f"environment must be one of {ENVIRONMENTS}, got {environment!r}")
    conn.exec_driver_sql(IDENTITY_TABLE_SQL)
    existing = conn.execute(text("SELECT environment FROM dbo.database_identity WHERE id = 1")).scalar_one_or_none()
    if existing is not None:
        raise GuardError(f"database_identity is already {existing}; it cannot be changed")
    conn.execute(text("INSERT INTO dbo.database_identity (id, environment, created_by) VALUES (1, :e, :by)"),
                 {"e": env, "by": by})


def target_of(store) -> Identity | None:
    engine = getattr(store, "engine", None)
    if engine is None:
        return None
    with engine.connect() as conn:
        return read(conn)
```

- [ ] **Step 4: Add the identity table to the create script**

At the top of `sql/001_schema.sql`, right after the header comment block and before the `artifact_versions` table, add:
```sql
/* 0. DATABASE IDENTITY — DEV | PROD, written once by `importer db init`; never dropped by rebuild. §8.17 */
IF OBJECT_ID(N'dbo.database_identity') IS NULL
CREATE TABLE dbo.database_identity (
    id          TINYINT NOT NULL CONSTRAINT pk_database_identity PRIMARY KEY
                CONSTRAINT ck_identity_single CHECK (id = 1),
    environment VARCHAR(8) NOT NULL CONSTRAINT ck_identity_env CHECK (environment IN ('DEV','PROD')),
    created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    created_by  NVARCHAR(128) NOT NULL
);
```
The `IF OBJECT_ID … );` text must equal `IDENTITY_TABLE_SQL` character for character; the test checks it.

- [ ] **Step 5: Remove `importer_env`**

In `src/importer/config.py`:
- delete the `importer_env` function and the `from dotenv import dotenv_values` import;
- change the `home()` docstring's first line to `"""Directory holding database.env (the pointer to the control database)."""`.

- [ ] **Step 6: Update the CLI**

In `src/importer/cli.py`:
- **Rebuild guard.** Replace `cmd_db_rebuild` with:
  ```python
  def cmd_db_rebuild(args) -> int:
      from . import config, db, environment
      from .schema_tables import REBUILD_DROP_SQL

      engine = db.get_engine()
      with engine.connect() as conn:
          identity = environment.read(conn)
      reason = environment.dev_guard(identity.environment, identity.database)
      if reason:
          print(f"refusing: {reason}", file=sys.stderr)
          return 1
      typed = args.confirm if args.confirm is not None else input(
          f"This drops every importer table in {identity.database}. Type the database name to confirm: ")
      if typed.strip() != identity.database:
          print("confirmation did not match the database name; nothing changed", file=sys.stderr)
          return 1
      script = (config.home() / "sql" / "001_schema.sql").read_text(encoding="utf-8")
      with engine.begin() as conn:
          conn.exec_driver_sql(REBUILD_DROP_SQL)
          conn.exec_driver_sql(script)
      print(f"rebuilt the importer schema in {identity.database}")
      return 0


  def cmd_db_init(args) -> int:
      from . import db, environment

      try:
          with db.get_engine().begin() as conn:
              environment.init(conn, args.environment, by=getpass.getuser())
              identity = environment.read(conn)
      except environment.GuardError as exc:
          print(f"refusing: {exc}", file=sys.stderr)
          return 1
      print(environment.banner(identity))
      return 0
  ```
- **Config commands.** Add:
  ```python
  def cmd_config(args, store) -> int:
      from . import settings

      with store.transaction() as tx:
          if args.config_command == "show":
              _print(settings.document(tx.artifacts))
              return 0
          by = getpass.getuser()
          try:
              if args.config_command == "unset":
                  settings.unset_destination(tx, args.name, by=by)
              elif args.key == "destination":
                  if not args.name:
                      raise settings.SettingsError("usage: config set destination NAME --server S --database D")
                  settings.set_destination(tx, args.name, server=args.server, database=args.database, by=by)
              else:
                  settings.set_value(tx, args.key, int(args.name), by=by)
          except (settings.SettingsError, ValueError, TypeError) as exc:
              print(f"refusing: {exc}", file=sys.stderr)
              return 1
          _print(settings.document(tx.artifacts))
      return 0
  ```
- **Parser.** In `build_parser`, after the `retry` subparser, add:
  ```python
      config_parser = sub.add_parser("config", help="show or change the shared settings (stored in the database)")
      config_sub = config_parser.add_subparsers(dest="config_command", required=True)
      config_sub.add_parser("show").set_defaults(func=cmd_config)
      p = config_sub.add_parser("set")
      p.add_argument("key", help="'destination' or samples.max_values / samples.max_chars")
      p.add_argument("name", nargs="?", help="destination NAME, or the numeric value")
      p.add_argument("--server")
      p.add_argument("--database")
      p.set_defaults(func=cmd_config)
      p = config_sub.add_parser("unset")
      p.add_argument("key", choices=["destination"])
      p.add_argument("name")
      p.set_defaults(func=cmd_config)
  ```
  Then replace the `db` subparser block with:
  ```python
      database = sub.add_parser("db").add_subparsers(dest="db_command", required=True)
      p = database.add_parser("rebuild", help="DEV only: drop and re-create the importer schema")
      p.add_argument("--confirm", metavar="DATABASE_NAME")
      p = database.add_parser("init", help="mark this database DEV or PROD (once)")
      p.add_argument("--environment", required=True, choices=["DEV", "PROD"])
  ```
- **Dispatch and banner.** Replace `main`'s body with:
  ```python
      args = build_parser().parse_args(argv)
      logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
      if args.command == "db":
          return cmd_db_init(args) if args.db_command == "init" else cmd_db_rebuild(args)
      store = store if store is not None else _default_store()
      from .environment import banner, target_of
      identity = target_of(store)
      if identity is not None:
          print(banner(identity), file=sys.stderr)
      return args.func(args, store)
  ```

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest`
Expected: all pass, including 8 tests in `test_environment.py` and the 4 new CLI tests.

- [ ] **Step 8: Initialize DEV and confirm the guard works on the real database**

Run:
```bash
.venv/Scripts/importer db init --environment DEV
.venv/Scripts/importer db init --environment DEV
.venv/Scripts/importer db rebuild --confirm import_agent_dev
.venv/Scripts/importer config set destination NPVCS --server cssnpi --database NPVCS
.venv/Scripts/importer config show
```
Expected:
- The first `init` prints `importer @ 2018NPI/import_agent_dev [DEV]`.
- The second prints `refusing: database_identity is already DEV; it cannot be changed` and exits 1.
- The rebuild succeeds; the identity row survives, because rebuild never drops it.
- `config show` prints the NPVCS destination, preceded by the banner on stderr.

Then delete the now-unused local `.env` file: `rm .env`.

- [ ] **Step 9: Mirror the DDL on Confluence**

Update the Confluence Schema DDL page (page `3775135746`) as in Task 4 Step 8. Change-note line: "M2b: `dbo.database_identity` (DEV/PROD marker, survives rebuild)".

- [ ] **Step 10: Commit**

```bash
git add src/importer/environment.py src/importer/config.py src/importer/cli.py sql/001_schema.sql tests/test_environment.py tests/test_cli.py
git commit -m "Move the DEV guard into the database; add db init, config CLI and target banner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Schema import from destinations or files

**Files:**
- Create: `src/importer/schema_sources.py`
- Modify: `src/importer/db.py` (`engine_for`)
- Modify: `src/importer/schema_tables.py` (remove `information_schema_sql`)
- Modify: `src/importer/cli.py` (`schema import`)
- Test: `tests/test_schema_sources.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `admin.register_schema`, `admin.SchemaRefused` and `admin.SchemaOutcome`;
  - `addresses.parse` and `addresses.AddressError`;
  - `settings.load`.
- Produces from `schema_sources`:
  - exception `SchemaSourceError(ValueError)`;
  - `LiveSource(engine)` with `.tables() -> list[tuple[str, str]]` and `.columns(schema, table) -> list[str]`;
  - `read_column_file(path) -> list[str]`;
  - `expand(pattern: str, source) -> list[str]`;
  - `live_source(store, database_name: str) -> LiveSource`;
  - `import_schemas(store, pattern: str, *, by: str, source=None, columns: list[str] | None = None) -> dict`. The report has keys `added`, `unchanged`, `changed`, `refused`.
- Produces from `db`: `engine_for(server: str, database: str) -> Engine`.

- [ ] **Step 1: Write the failing tests**

`tests/test_schema_sources.py`:
```python
from __future__ import annotations

import json

import pytest

from importer.memory_store import MemoryStore
from importer.schema_sources import SchemaSourceError, expand, import_schemas, read_column_file
from tests.support import configure


class FakeSource:
    def __init__(self, tables: dict[tuple[str, str], list[str]]):
        self._tables = tables

    def tables(self):
        return list(self._tables)

    def columns(self, schema, table):
        for (s, t), cols in self._tables.items():
            if (s.lower(), t.lower()) == (schema.lower(), table.lower()):
                return cols
        return []


SOURCE = FakeSource({
    ("relational", "url_template"): ["urlid", "newurl"],
    ("relational", "plans"): ["planid"],
    ("xml", "formulary_drug_list"): ["rxcui"],
    ("dbo", "Bad Name"): ["a"],
})


def test_expand_wildcards():
    assert expand("NPVCS.relational.url_template", SOURCE) == ["NPVCS.relational.url_template"]
    assert sorted(expand("NPVCS.relational.*", SOURCE)) == ["NPVCS.relational.plans", "NPVCS.relational.url_template"]
    assert len(expand("NPVCS.*.*", SOURCE)) == 4
    with pytest.raises(SchemaSourceError):
        expand("NPVCS.*", SOURCE)


def test_import_reports_added_unchanged_changed_refused():
    store = MemoryStore()
    configure(store, "npvcs")
    first = import_schemas(store, "NPVCS.*.*", by="tony", source=SOURCE)
    assert sorted(o["address"] for o in first["added"]) == [
        "npvcs.relational.plans", "npvcs.relational.url_template", "npvcs.xml.formulary_drug_list"]
    assert [r["address"] for r in first["refused"]] == ["NPVCS.dbo.Bad Name"]
    again = import_schemas(store, "NPVCS.relational.*", by="tony", source=SOURCE)
    assert len(again["unchanged"]) == 2 and again["added"] == []
    grown = FakeSource({("relational", "url_template"): ["urlid", "newurl", "issuerid"]})
    changed = import_schemas(store, "NPVCS.relational.url_template", by="tony", source=grown)
    assert changed["changed"][0]["new_columns"] == ["issuerid"]
    assert [d["action_type"] for d in store.state.decisions if d["action_type"] == "schema_import"] == ["schema_import"] * 3


def test_import_from_columns_and_files(tmp_path):
    store = MemoryStore()
    configure(store, "zipcode")
    js = tmp_path / "cols.json"
    js.write_text(json.dumps(["zip", "city"]), encoding="utf-8")
    csv = tmp_path / "cols.csv"
    csv.write_text("zip\ncity\n\nstate\n", encoding="utf-8")
    assert read_column_file(js) == ["zip", "city"]
    assert read_column_file(csv) == ["zip", "city", "state"]
    report = import_schemas(store, "ZIPCODE.dbo.zip_lookup", by="t", columns=read_column_file(csv))
    assert report["added"][0]["address"] == "zipcode.dbo.zip_lookup"
    with pytest.raises(SchemaSourceError, match="one explicit"):
        import_schemas(store, "ZIPCODE.dbo.*", by="t", columns=["zip"])


def test_unconfigured_destination_is_refused_not_raised():
    store = MemoryStore()
    report = import_schemas(store, "RX.dbo.drugs", by="t", columns=["ndc"])
    assert report["refused"][0]["reason"].startswith("rx.dbo.drugs: database 'rx' is not a configured destination")
```

In `tests/test_cli.py`, add:
```python
def test_schema_import_from_file(store, tmp_path, capsys):
    cols = tmp_path / "cols.json"
    cols.write_text('["ndc", "name"]', encoding="utf-8")
    cli.main(["config", "set", "destination", "RX", "--server", "s", "--database", "RX"], store=store)
    capsys.readouterr()
    assert cli.main(["schema", "import", "RX.dbo.drugs", "--from-file", str(cols)], store=store) == 0
    assert _json_out(capsys)["added"][0]["address"] == "rx.dbo.drugs"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_schema_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.schema_sources'`.

- [ ] **Step 3: Add `engine_for` to `db.py`**

Append to `src/importer/db.py`:
```python
def engine_for(server: str, database: str, path: Path | None = None) -> "Engine":
    """An engine to a destination database, authenticating like the local `importer` entry.

    Used only for schema import (design §8.16); the caller's own Windows/Entra identity
    needs VIEW DEFINITION there. Nothing about the destination is stored locally.
    """
    doc = load_connection_document(path)
    from DataEngine.mssql import SqlConnectionObject  # lazy on purpose (module docstring)

    connection = SqlConnectionObject(
        name=f"dest_{database}",
        server=server,
        database=database,
        UN=doc.get("UN") or None,
        PW=None,
        trusted=doc.get("trusted", "no"),
    )
    return connection.engine
```

- [ ] **Step 4: Implement `schema_sources.py`**

`src/importer/schema_sources.py`:
```python
"""Schema import from a destination database or a column file (design §8.16).

Target access is needed only when a schema is captured; afterwards everything runs
from the registered copy. Live reads run inside the destination database (no
three-part names), so they work on Azure SQL Database too. The agent never
connects to a destination.
"""

from __future__ import annotations

import csv
import json
from fnmatch import fnmatchcase
from pathlib import Path

from sqlalchemy import text

from .addresses import AddressError
from .admin import SchemaRefused, register_schema
from .settings import load as load_settings

TABLES_SQL = (
    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
)
COLUMNS_SQL = (
    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = :s AND TABLE_NAME = :t ORDER BY ORDINAL_POSITION"
)


class SchemaSourceError(ValueError):
    """The import request itself is malformed."""


class LiveSource:
    def __init__(self, engine):
        self.engine = engine

    def tables(self) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            return [(s, t) for s, t in conn.execute(text(TABLES_SQL)).all()]

    def columns(self, schema: str, table: str) -> list[str]:
        with self.engine.connect() as conn:
            return list(conn.execute(text(COLUMNS_SQL), {"s": schema, "t": table}).scalars())


def live_source(store, database_name: str) -> LiveSource:
    from .db import engine_for

    with store.transaction() as tx:
        destination = load_settings(tx.artifacts).destination(database_name)
    if destination is None:
        raise SchemaSourceError(f"{database_name!r} is not a configured destination (importer config set destination ...)")
    return LiveSource(engine_for(destination.server, destination.database))


def read_column_file(path) -> list[str]:
    path = Path(path)
    body = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        columns = json.loads(body)
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise SchemaSourceError(f"{path}: expected a JSON list of column names")
        return columns
    return [row[0].strip() for row in csv.reader(body.splitlines()) if row and row[0].strip()]


def _is_pattern(part: str) -> bool:
    return any(ch in part for ch in "*?[")


def expand(pattern: str, source) -> list[str]:
    parts = pattern.split(".")
    if len(parts) != 3:
        raise SchemaSourceError(f"{pattern!r}: expected DATABASE.SCHEMA.TABLE (wildcards allowed in SCHEMA and TABLE)")
    database, schema_pattern, table_pattern = parts
    if not (_is_pattern(schema_pattern) or _is_pattern(table_pattern)):
        return [pattern]
    return [
        f"{database}.{schema}.{table}" for schema, table in source.tables()
        if fnmatchcase(schema.lower(), schema_pattern.lower()) and fnmatchcase(table.lower(), table_pattern.lower())
    ]


def import_schemas(store, pattern: str, *, by: str, source=None, columns: list[str] | None = None) -> dict:
    report: dict[str, list] = {"added": [], "unchanged": [], "changed": [], "refused": []}
    if columns is not None:
        if any(_is_pattern(p) for p in pattern.split(".")[1:]):
            raise SchemaSourceError("a column file describes one explicit DATABASE.SCHEMA.TABLE, not a pattern")
        addresses, kind = [pattern], "file"
    else:
        if source is None:
            raise SchemaSourceError("a live import needs a source")
        addresses, kind = expand(pattern, source), "live"
    for address in addresses:
        try:
            if columns is not None:
                cols = list(columns)
            else:
                _, schema, table = address.split(".")
                cols = source.columns(schema, table)
            if not cols:
                raise SchemaRefused(f"{address}: no columns (or the table does not exist)")
            with store.transaction() as tx:
                outcome = register_schema(tx, address, cols, created_by=by, source=kind)
            report[outcome.result].append(outcome.to_dict())
        except (AddressError, SchemaRefused, ValueError) as exc:
            report["refused"].append({"address": address, "reason": str(exc)})
    with store.transaction() as tx:
        tx.audit.decision(None, "schema_import", observed={"pattern": pattern, "source": kind},
                          proposed={key: len(value) for key, value in report.items()}, applied=True, approval=by)
    return report
```

- [ ] **Step 5: Replace the CLI command and remove the cross-database helper**

In `src/importer/cli.py`, replace `cmd_schema_import` with:
```python
def cmd_schema_import(args, store) -> int:
    from .schema_sources import SchemaSourceError, import_schemas, live_source, read_column_file

    try:
        if args.from_file:
            report = import_schemas(store, args.address, by=getpass.getuser(), columns=read_column_file(args.from_file))
        else:
            source = live_source(store, args.address.split(".")[0])
            report = import_schemas(store, args.address, by=getpass.getuser(), source=source)
    except SchemaSourceError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    _print(report)
    return 1 if report["refused"] and not (report["added"] or report["changed"] or report["unchanged"]) else 0
```

Then replace the `schema import` subparser lines with:
```python
    p = schema.add_parser("import", help="register destination table schemas (live, or --from-file)")
    p.add_argument("address", help="DATABASE.SCHEMA.TABLE; wildcards allowed in SCHEMA and TABLE, e.g. NPVCS.*.*")
    p.add_argument("--from-file", help="JSON list or one-name-per-line CSV of column names (one explicit table)")
    p.set_defaults(func=cmd_schema_import)
```

Delete `information_schema_sql` from `src/importer/schema_tables.py`.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest`
Expected: all pass, including 4 new tests in `test_schema_sources.py` and `test_schema_import_from_file`.

- [ ] **Step 7: Try a live import on DEV**

Use `import_agent_dev` itself as a stand-in destination. Run:
```bash
.venv/Scripts/importer config set destination IMPORT_AGENT_DEV --server cssnpi --database import_agent_dev
.venv/Scripts/importer schema import "IMPORT_AGENT_DEV.dbo.signature_registry"
```
Expected: a report with one `added` entry, `import_agent_dev.dbo.signature_registry`, listing the registry's columns. That proves the per-destination connection works. The registered schema is harmless DEV data.

- [ ] **Step 8: Commit**

```bash
git add src/importer/schema_sources.py src/importer/db.py src/importer/schema_tables.py src/importer/cli.py tests/test_schema_sources.py tests/test_cli.py
git commit -m "Import destination schemas over per-destination connections or from files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Shared file locators

**Files:**
- Create: `src/importer/locators.py`
- Modify: `src/importer/pipeline.py:12-16,44-71`
- Test: `tests/test_locators.py`

**Interfaces:**
- Produces from `locators`:
  - exception `LocatorError(ValueError)`;
  - `is_blob(loc) -> bool` and `is_unc(loc) -> bool`;
  - `normalize(path) -> str`;
  - `name_of(loc) -> str`;
  - `read_bytes(loc) -> bytes`;
  - `read_verified(loc, sha256: str) -> bytes`.
- Changes in `pipeline`:
  - `process(path, …)` accepts a locator string or a path, and stores it verbatim in `source_path`;
  - `retry` reads through `read_verified`.

- [ ] **Step 1: Write the failing tests**

`tests/test_locators.py`:
```python
from __future__ import annotations

import hashlib

import pytest

from importer import locators
from importer.memory_store import MemoryStore
from importer.pipeline import process, retry
from tests.support import fixture

BLOB = "https://acct.blob.core.windows.net/incoming/dc/formulary.xml"


def test_kinds_and_names():
    assert locators.is_blob(BLOB) and not locators.is_unc(BLOB)
    assert locators.is_unc(r"\\cssnpi\drop\vt\urls.csv") and locators.is_unc("//cssnpi/drop/urls.csv")
    assert locators.name_of(BLOB) == "formulary.xml"
    assert locators.name_of(r"\\cssnpi\drop\vt\urls.csv") == "urls.csv"


def test_shared_locators_are_kept_verbatim_and_local_paths_resolved(tmp_path):
    assert locators.normalize(BLOB) == BLOB
    assert locators.normalize(r"\\cssnpi\drop\a.csv") == r"\\cssnpi\drop\a.csv"
    local = tmp_path / "a.csv"
    assert locators.normalize(local) == str(local.resolve())


def test_blob_reads_go_through_the_blob_reader(monkeypatch):
    monkeypatch.setattr(locators, "_read_blob", lambda url: b"data:" + url.encode())
    assert locators.read_bytes(BLOB) == b"data:" + BLOB.encode()


def test_unreachable_and_changed_files_are_refused(tmp_path):
    with pytest.raises(locators.LocatorError, match="not reachable"):
        locators.read_bytes(str(tmp_path / "missing.csv"))
    path = tmp_path / "f.csv"
    path.write_bytes(b"a\n1\n")
    good = hashlib.sha256(b"a\n1\n").hexdigest()
    assert locators.read_verified(str(path), good) == b"a\n1\n"
    with pytest.raises(locators.LocatorError, match="changed since"):
        locators.read_verified(str(path), "0" * 64)


def test_process_records_a_blob_locator_and_retry_reads_it(monkeypatch):
    data = fixture("url_template_a.csv").read_bytes()
    monkeypatch.setattr(locators, "_read_blob", lambda url: data)
    store = MemoryStore()
    result = process(BLOB.replace("formulary.xml", "urls.csv"), project="VT", cls="PC", file_type="CSV",
                     source="test", store=store)
    row = store.state.ledger[result.file_id]
    assert row.source_path.endswith("/incoming/dc/urls.csv") and row.original_filename == "urls.csv"
    again = retry(result.file_id, store=store)
    assert again.file_id == result.file_id
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_locators.py -v`
Expected: FAIL with `ImportError: cannot import name 'locators'`.

- [ ] **Step 3: Implement `locators.py`**

`src/importer/locators.py`:
```python
"""Read a file from where it lives (design §8.21).

Production files are always on a shared location known to the catalog: CIFS/SMB
(\\\\server\\share\\...), an Azure Files share (\\\\<acct>.file.core.windows.net\\...)
or Azure Blob (https://<acct>.blob.core.windows.net/<container>/...). The importer
copies nothing; the ledger keeps the locator verbatim so any machine can re-read it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path, PureWindowsPath

log = logging.getLogger("importer")
_BLOB = re.compile(r"^https://[^/]+\.blob\.core\.windows\.net/[^/]+/.+", re.IGNORECASE)


class LocatorError(ValueError):
    """The file cannot be read from this machine, or it changed since discovery."""


def is_blob(loc: str) -> bool:
    return bool(_BLOB.match(loc))


def is_unc(loc: str) -> bool:
    return loc.startswith("\\\\") or loc.startswith("//")


def normalize(path) -> str:
    text = str(path)
    if is_blob(text) or is_unc(text):
        return text
    resolved = str(Path(text).resolve())
    log.warning("local path %s: other machines cannot read it; use a share or Blob URL in production", resolved)
    return resolved


def name_of(loc: str) -> str:
    if is_blob(loc):
        return loc.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return PureWindowsPath(loc).name if "\\" in loc else Path(loc).name


def read_bytes(loc: str) -> bytes:
    if is_blob(loc):
        return _read_blob(loc)
    try:
        return Path(loc).read_bytes()
    except OSError as exc:
        raise LocatorError(f"file not reachable from this machine: {loc} ({exc})") from exc


def read_verified(loc: str, sha256: str) -> bytes:
    data = read_bytes(loc)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise LocatorError(f"{loc} changed since it was discovered; deliver it as a new file")
    return data


def _read_blob(url: str) -> bytes:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient
    except ImportError as exc:
        raise LocatorError("Blob locators need azure-storage-blob and azure-identity installed") from exc
    try:
        return BlobClient.from_blob_url(url, credential=DefaultAzureCredential()).download_blob().readall()
    except Exception as exc:
        raise LocatorError(f"file not reachable from this machine: {url} ({type(exc).__name__}: {exc})") from exc
```

- [ ] **Step 4: Use locators in the pipeline**

In `src/importer/pipeline.py`:
- add `from . import locators` to the imports, and remove `from pathlib import Path` only if nothing else uses it (`plugins_dir: Path` still does, so keep it);
- replace the first lines of `process` (from `declared = …` through `return _result_from_ledger(row)` and the `log.info(...)` line) with:
  ```python
      declared = Declared.normalize(project, cls, file_type)
      locator = locators.normalize(path)
      data = locators.read_bytes(locator)
      key = hashlib.sha256(data).hexdigest()
      with store.transaction() as tx:
          row, created = tx.ledger.create_or_get(
              idempotency_key=key, source=source, original_filename=locators.name_of(locator),
              source_path=locator, declared=declared, request_context=context,
          )
      if not created:
          log.info("file_id=%s re-delivery of a known file (status=%s); nothing re-landed", row.file_id, row.status.value)
          return _result_from_ledger(row)
      log.info("file_id=%s discovered %s as %s", row.file_id, locators.name_of(locator), declared.prefix)
  ```
- replace `retry`'s first five lines with:
  ```python
      with store.transaction() as tx:
          row = tx.ledger.get(file_id)
      data = locators.read_verified(row.source_path, row.idempotency_key)
  ```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest`
Expected: all pass, including 5 new tests in `test_locators.py`.

- [ ] **Step 6: Commit**

```bash
git add src/importer/locators.py src/importer/pipeline.py tests/test_locators.py
git commit -m "Read files through shared locators (SMB, Azure Files, Blob)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Proposal object and the repository methods onboarding needs

**Files:**
- Create: `src/importer/proposals.py`
- Modify: `src/importer/repositories.py` (`RegistryRow.proposal`, Protocols)
- Modify: `src/importer/memory_store.py` (`MemoryLedger`, `MemoryRegistry`)
- Modify: `src/importer/mssql_store.py` (`MssqlLedger`, `MssqlRegistry`)
- Test: `tests/test_proposals_repos.py`, `tests/integration/test_mssql_store.py`

**Interfaces:**
- Produces from `proposals`:
  - `new_review() -> dict`;
  - `normalize(proposal) -> dict`, which accepts `None`, an M2a candidate list, or the object;
  - `with_candidates(existing, candidates: list[dict]) -> dict`.
- `RegistryRow.proposal: dict | None`.
- Registry changes:
  - `SignatureRegistry.propose(sig, candidates: list[dict]) -> int` keeps the review state on a repeated miss;
  - `SignatureRegistry.list_by_status(status: SignatureStatus) -> list[RegistryRow]`, ordered by `signature_id`;
  - `SignatureRegistry.set_proposal(signature_id: int, proposal: dict) -> None`;
  - `SignatureRegistry.activate(signature_id: int, mapper_version_id: int, approved_by: str) -> None`.
- Ledger change: `LedgerRepository.files_for_signature(signature_id: int) -> list[LedgerRow]`, ordered by `file_id`.

- [ ] **Step 1: Write the failing tests**

`tests/test_proposals_repos.py`:
```python
from __future__ import annotations

from importer.memory_store import MemoryStore
from importer.models import Declared, FileStatus, Signature, SignatureStatus
from importer.pipeline import process
from importer.proposals import new_review, normalize, with_candidates
from tests.support import fixture

SIG = Signature("VT|PC|CSV|h:abc", "h:abc", None, {"columns": ["a"]})


def test_normalize_accepts_none_list_and_object():
    assert normalize(None) == {"candidates": [], "review": new_review()}
    assert normalize([{"kind": "schema"}])["candidates"] == [{"kind": "schema"}]
    partial = normalize({"candidates": [], "review": {"state": "rejected"}})
    assert partial["review"]["state"] == "rejected" and partial["review"]["notes"] == []
    kept = with_candidates({"candidates": [1], "review": {"state": "ready_for_review"}}, [2])
    assert kept == {"candidates": [2], "review": {**new_review(), "state": "ready_for_review"}}


def test_repeated_miss_keeps_the_review_state():
    store = MemoryStore()
    with store.transaction() as tx:
        sid = tx.registry.propose(SIG, [{"kind": "schema", "score": 0.5}])
        proposal = normalize(tx.registry.get(sid).proposal)
        proposal["review"]["state"] = "ready_for_review"
        tx.registry.set_proposal(sid, proposal)
        assert tx.registry.propose(SIG, [{"kind": "schema", "score": 0.9}]) == sid
        row = tx.registry.get(sid)
    assert row.proposal["review"]["state"] == "ready_for_review"
    assert row.proposal["candidates"] == [{"kind": "schema", "score": 0.9}]


def test_list_by_status_activate_and_files_for_signature():
    store = MemoryStore()
    result = process(fixture("url_template_b.csv"), project="VT", cls="PC", file_type="CSV", source="t", store=store)
    with store.transaction() as tx:
        [proposed] = tx.registry.list_by_status(SignatureStatus.PROPOSED)
        assert proposed.proposal["review"]["state"] == "needs_mapping"
        files = tx.ledger.files_for_signature(proposed.signature_id)
        assert [f.file_id for f in files] == [result.file_id] and files[0].status is FileStatus.QUARANTINED
        tx.registry.activate(proposed.signature_id, 99, approved_by="tony")
        row = tx.registry.get(proposed.signature_id)
        assert (row.status, row.mapper_version_id, row.approved_by) == (SignatureStatus.ACTIVE, 99, "tony")
        assert tx.registry.list_by_status(SignatureStatus.PROPOSED) == []


def test_registry_rows_are_copies():
    store = MemoryStore()
    with store.transaction() as tx:
        sid = tx.registry.propose(SIG, [])
        tx.registry.get(sid).proposal["review"]["state"] = "accepted"
        assert tx.registry.get(sid).proposal["review"]["state"] == "needs_mapping"
```

In `tests/integration/test_mssql_store.py`, in `test_registry_artifacts_and_audit`, after `assert tx.registry.get(proposed).status is SignatureStatus.PROPOSED`, add:
```python
        assert tx.registry.get(proposed).proposal["review"]["state"] == "needs_mapping"
        tx.registry.set_proposal(proposed, {"candidates": [], "review": {"state": "ready_for_review"}})
        assert tx.registry.propose(sig, [{"kind": "x"}]) == proposed
        assert tx.registry.get(proposed).proposal["review"]["state"] == "ready_for_review"
        assert proposed in [r.signature_id for r in tx.registry.list_by_status(SignatureStatus.PROPOSED)]
```
Then, after `row, _ = _create(tx, f"it-{uid}")`, add:
```python
        tx.ledger.set_signature(row.file_id, active)
        assert [f.file_id for f in tx.ledger.files_for_signature(active)] == [row.file_id]
        tx.registry.activate(active, mapper.version_id, approved_by="it")
        assert tx.registry.get(active).approved_by == "it"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_proposals_repos.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.proposals'`.

- [ ] **Step 3: Implement `proposals.py`**

`src/importer/proposals.py`:
```python
"""The proposal object on a proposed signature (design §8.18).

{"candidates": [Candidate.to_dict(), ...],
 "review": {state, draft_version_id, path, rationale, preview, notes, history}}
States used: needs_mapping, ready_for_review, accepted, rejected.
"""

from __future__ import annotations

import copy


def new_review() -> dict:
    return {"state": "needs_mapping", "draft_version_id": None, "path": None, "rationale": [],
            "preview": None, "notes": [], "history": []}


def normalize(proposal) -> dict:
    if proposal is None:
        return {"candidates": [], "review": new_review()}
    if isinstance(proposal, list):  # written by M2a
        return {"candidates": copy.deepcopy(proposal), "review": new_review()}
    return {"candidates": copy.deepcopy(proposal.get("candidates", [])),
            "review": {**new_review(), **copy.deepcopy(proposal.get("review", {}))}}


def with_candidates(existing, candidates: list[dict]) -> dict:
    proposal = normalize(existing)
    proposal["candidates"] = copy.deepcopy(list(candidates))
    return proposal
```

- [ ] **Step 4: Update the Protocols**

In `src/importer/repositories.py`:
- change `RegistryRow.proposal: list | None = None` to `proposal: dict | None = None`;
- add to `LedgerRepository`:
  ```python
      def files_for_signature(self, signature_id: int) -> list[LedgerRow]: ...
  ```
- in `SignatureRegistry`, replace the `propose` line and add three methods:
  ```python
      def propose(self, sig: Signature, candidates: list[dict]) -> int: ...
      def list_by_status(self, status: SignatureStatus) -> list[RegistryRow]: ...
      def set_proposal(self, signature_id: int, proposal: dict) -> None: ...
      def activate(self, signature_id: int, mapper_version_id: int, approved_by: str) -> None: ...
  ```

- [ ] **Step 5: Implement in the memory store**

In `src/importer/memory_store.py`:
- add `from .proposals import with_candidates` to the imports;
- add to `MemoryLedger`:
  ```python
      def files_for_signature(self, signature_id):
          return [replace(r) for r in sorted(self.s.ledger.values(), key=lambda r: r.file_id)
                  if r.signature_id == signature_id]
  ```
- in `MemoryRegistry`, replace `get`, `find_active_by_physical_part` and `propose`, and add the three new methods:
  ```python
      def _row(self, signature_id) -> RegistryRow:
          try:
              return self.s.registry[signature_id]
          except KeyError:
              raise NotFound(signature_id) from None

      def get(self, signature_id):
          return copy.deepcopy(self._row(signature_id))

      def find_active_by_physical_part(self, physical_part, xml_namespace):
          return [
              copy.deepcopy(r) for r in self.s.registry.values()
              if r.physical_part == physical_part and r.status is SignatureStatus.ACTIVE and r.xml_namespace == xml_namespace
          ]

      def propose(self, sig: Signature, candidates):
          existing = self._by_fp(sig.fingerprint)
          if existing is not None:
              if existing.status is SignatureStatus.PROPOSED:
                  existing.observed_schema = sig.observed
                  existing.proposal = with_candidates(existing.proposal, candidates)
              return existing.signature_id
          sid = self.s.next_id("sig")
          self.s.registry[sid] = RegistryRow(
              signature_id=sid, fingerprint=sig.fingerprint, physical_part=sig.physical_part,
              xml_namespace=sig.xml_namespace, status=SignatureStatus.PROPOSED,
              observed_schema=sig.observed, proposal=with_candidates(None, candidates),
          )
          return sid

      def list_by_status(self, status):
          return [copy.deepcopy(r) for r in sorted(self.s.registry.values(), key=lambda r: r.signature_id)
                  if r.status is status]

      def set_proposal(self, signature_id, proposal):
          self._row(signature_id).proposal = copy.deepcopy(proposal)

      def activate(self, signature_id, mapper_version_id, approved_by):
          row = self._row(signature_id)
          row.status = SignatureStatus.ACTIVE
          row.mapper_version_id = mapper_version_id
          row.approved_by = approved_by
  ```

- [ ] **Step 6: Implement in the SQL Server store**

In `src/importer/mssql_store.py`:
- add `from .proposals import with_candidates`;
- add to `MssqlLedger`:
  ```python
      def files_for_signature(self, signature_id):
          rows = self.c.execute(text(
              f"SELECT {LEDGER_COLS} FROM dbo.file_ledger WHERE signature_id = :s ORDER BY file_id"
          ), {"s": signature_id}).mappings().all()
          return [_ledger_row(m) for m in rows]
  ```
- in `MssqlRegistry`, replace `propose` and add the three new methods:
  ```python
      def propose(self, sig: Signature, candidates):
          existing = self._by_fp(sig.fingerprint)
          if existing is not None:
              if existing.status is SignatureStatus.PROPOSED:
                  self.c.execute(text(
                      "UPDATE dbo.signature_registry SET observed_schema = :obs, proposal = :prop WHERE signature_id = :id"
                  ), {"obs": _dump(sig.observed), "prop": _dump(with_candidates(existing.proposal, candidates)),
                      "id": existing.signature_id})
              return existing.signature_id
          return self.c.execute(text(
              "INSERT INTO dbo.signature_registry (fingerprint, physical_part, xml_namespace, status, observed_schema, "
              "proposal, provenance) OUTPUT INSERTED.signature_id "
              "VALUES (:fp, :part, :ns, 'proposed', :obs, :prop, 'profile_match')"
          ), {"fp": sig.fingerprint, "part": sig.physical_part, "ns": sig.xml_namespace,
              "obs": _dump(sig.observed), "prop": _dump(with_candidates(None, candidates))}).scalar_one()

      def list_by_status(self, status):
          rows = self.c.execute(text(
              f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE status = :s ORDER BY signature_id"
          ), {"s": status.value}).mappings().all()
          return [_registry_row(m) for m in rows]

      def set_proposal(self, signature_id, proposal):
          self.c.execute(text("UPDATE dbo.signature_registry SET proposal = :p WHERE signature_id = :id"),
                         {"p": _dump(proposal), "id": signature_id})

      def activate(self, signature_id, mapper_version_id, approved_by):
          self.c.execute(text(
              "UPDATE dbo.signature_registry SET status = 'active', mapper_version_id = :mv, approved_by = :by, "
              "approved_at = SYSUTCDATETIME() WHERE signature_id = :id"
          ), {"mv": mapper_version_id, "by": approved_by, "id": signature_id})
  ```

- [ ] **Step 7: Run unit and store integration tests**

Run:
```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python -m pytest -m integration tests/integration/test_mssql_store.py -v
```
Expected: all unit tests pass (including 4 new ones), then 5 integration tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/importer/proposals.py src/importer/repositories.py src/importer/memory_store.py src/importer/mssql_store.py tests/test_proposals_repos.py tests/integration/test_mssql_store.py
git commit -m "Give proposals a review state; add registry and ledger queries for onboarding

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Onboarding core — what's waiting, what it looks like, previews

**Files:**
- Create: `src/importer/onboarding.py` (read side)
- Test: `tests/test_onboarding.py`

**Interfaces:**
- Consumes:
  - `proposals.normalize`;
  - `locators.read_verified`;
  - `profiler.profile` and `profiler.Candidate`;
  - `settings.load`;
  - `mappers.spec.parse_spec`, `mappers.spec.DeclarativeMapper` and `mappers.preview.preview`;
  - `addresses.parse`;
  - the repositories from Task 8.
- Produces from `onboarding`:
  - exception `OnboardingError(ValueError)`;
  - `pending(store) -> list[dict]`;
  - `file_profile(store, signature_id) -> dict`;
  - `samples(store, signature_id, columns: list[str]) -> dict[str, list[str]]`;
  - `search_schemas(store, columns=None, text=None, limit=10) -> list[dict]`;
  - `get_schema(store, address) -> dict`;
  - `prior_mappings(store, address) -> list[dict]`;
  - `preview_spec(store, signature_id, spec) -> dict` (either a preview or `{"errors": [...]}`).
- Internal helpers used by Task 10: `_proposed`, `_trigger`, `_suggest`, `_preview`, `_preview_inputs`, `_targets`, `_now`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboarding.py`:
```python
from __future__ import annotations

import pytest

from importer import onboarding, settings
from importer.admin import register_schema
from importer.memory_store import MemoryStore
from importer.models import Declared
from importer.pipeline import process
from tests.support import URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, URL_TARGET, configure, fixture, load_json, setup_signature


def _url_store() -> MemoryStore:
    store = MemoryStore()
    configure(store, "npvcs")
    with store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="t")
    return store


def _park(store, path, project="VT", file_type="CSV"):
    return process(path, project=project, cls="PC", file_type=file_type, source="t", store=store)


def test_pending_lists_a_fallback_proposal():
    store = _url_store()
    parked = _park(store, fixture("url_template_b.csv"))
    [item] = onboarding.pending(store)
    assert item["state"] == "needs_mapping" and item["suggested_path"] == "fallback"
    assert item["candidates"][0].startswith(f"looks like {URL_TARGET}")
    assert item["waiting_files"] == 1 and item["trigger_file"]["file_id"] == parked.file_id


def test_exact_path_drafts_an_auto_spec():
    store = _url_store()
    _park(store, fixture("url_template_a.csv"))
    [item] = onboarding.pending(store)
    assert item["suggested_path"] == "exact"
    draft = onboarding.file_profile(store, item["signature_id"])["suggested_draft"]
    assert draft == {"path": "exact", "spec": {"kind": "tabular", "extractions": [
        {"target": URL_TARGET, "records": "*", "auto": True, "unmapped": "extras"}]}}


def test_reuse_path_and_xml_profile(tmp_path):
    store = MemoryStore()
    mapper_id = setup_signature(store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
                                load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary")
    me = tmp_path / "me.xml"
    me.write_bytes(fixture("formulary_dc.xml").read_bytes() + b"\n<!-- ME -->\n")
    _park(store, me, project="ME", file_type="XML")
    [item] = onboarding.pending(store)
    assert item["suggested_path"] == "reuse"
    prof = onboarding.file_profile(store, item["signature_id"])
    assert prof["suggested_draft"]["mapper_version_id"] == mapper_id
    assert prof["profile"]["kind"] == "xml" and prof["profile"]["root"] == "prescriptionDrugTemplateVO"


def test_unknown_xml_needs_the_model(tmp_path):
    store = MemoryStore()
    path = tmp_path / "odd.xml"
    path.write_bytes(b"<root><item><code>A</code></item><item><code>B</code></item></root>")
    _park(store, path, file_type="XML")
    [item] = onboarding.pending(store)
    assert item["suggested_path"] == "model"
    assert onboarding.file_profile(store, item["signature_id"])["suggested_draft"] is None
    assert onboarding.samples(store, item["signature_id"], ["/root/item/code"]) == {"/root/item/code": ["A", "B"]}


def test_samples_are_capped_and_truncated():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert len(onboarding.samples(store, sid, ["url"])["url"]) == 2
    with store.transaction() as tx:
        settings.set_value(tx, "samples.max_values", 1, by="t")
        settings.set_value(tx, "samples.max_chars", 10, by="t")
    assert onboarding.samples(store, sid, ["url"]) == {"url": ["https://ex"]}


def test_search_get_schema_and_prior_mappings():
    store = _url_store()
    setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    best = onboarding.search_schemas(store, columns=["URL ID", "url", "url type"])[0]
    assert (best["address"], best["score"]) == (URL_TARGET, 0.67)
    assert onboarding.search_schemas(store, text="url")[0]["address"] == URL_TARGET
    assert onboarding.get_schema(store, "NPVCS.relational.url_template")["columns"] == URL_SCHEMA[URL_TARGET]
    with pytest.raises(onboarding.OnboardingError):
        onboarding.get_schema(store, "npvcs.relational.nope")
    [prior] = onboarding.prior_mappings(store, URL_TARGET)
    assert prior["spec"] == URL_SPEC_A and prior["fingerprint"].startswith("VT|PC|CSV|h:")


def test_preview_spec_counts_rows_and_reports_errors():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    result = onboarding.preview_spec(store, sid, URL_SPEC_B)
    assert result["counts"] == {URL_TARGET: 2} and result["rejects"] == {}
    assert len(result["sample"][URL_TARGET]) == 2 and result["sample"][URL_TARGET][0]["newurl"].startswith("https://")
    assert "plugin" in onboarding.preview_spec(store, sid, {"kind": "plugin", "ref": "x:Y"})["errors"][0]
    bad = {"kind": "tabular", "extractions": [{"target": "npvcs.relational.nope", "records": "*", "auto": True}]}
    assert "no active schema" in onboarding.preview_spec(store, sid, bad)["errors"][0]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_onboarding.py -v`
Expected: FAIL with `ImportError: cannot import name 'onboarding'`.

- [ ] **Step 3: Implement the read side of `onboarding.py`**

`src/importer/onboarding.py`:
```python
"""Confirm-and-bind onboarding (design §8.18).

The deterministic core behind the MCP server: what is waiting, what a file looks
like, the cheapest draft (reuse / exact / fallback), previews, and (Task 10)
submit, reject and accept. Writes only versioned data; a draft mapper is an
inactive version that routing never uses until a person accepts it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import locators
from .addresses import parse
from .mappers.base import RunContext
from .mappers.preview import preview
from .mappers.spec import DeclarativeMapper, SpecError, parse_spec
from .models import FileStatus, SignatureStatus
from .profiler import Candidate, profile
from .proposals import normalize
from .readers import parse_document
from .readers.base import ReaderError, XmlDocument, normalize_name
from .readers.xml_reader import local
from .repositories import LedgerRow, NotFound, RegistryRow
from .settings import load as load_settings
from .shapers import registered_shapers

PREVIEW_ROWS = 3


class OnboardingError(ValueError):
    """The request does not fit the proposal's current state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _proposed(tx, signature_id: int) -> RegistryRow:
    try:
        row = tx.registry.get(signature_id)
    except NotFound:
        raise OnboardingError(f"no signature {signature_id}") from None
    if row.status is not SignatureStatus.PROPOSED:
        raise OnboardingError(f"signature {signature_id} is {row.status.value}, not proposed")
    return row


def _trigger(tx, signature_id: int) -> LedgerRow | None:
    rows = tx.ledger.files_for_signature(signature_id)
    waiting = [r for r in rows if r.status is FileStatus.QUARANTINED]
    return (waiting or rows or [None])[0]


def _document(row: LedgerRow):
    return parse_document(locators.read_verified(row.source_path, row.idempotency_key), row.file_type)


def _observed_tables(observed: dict | None) -> dict[str, list[str]] | None:
    if not observed or "root" in observed:
        return None
    if "sheets" in observed:
        return dict(observed["sheets"])
    return {"*": list(observed.get("columns") or observed.get("keys") or [])}


def _targets(definition: dict) -> list[str]:
    if definition.get("kind") not in ("xml", "tabular"):
        return []
    return [e["target"] for e in definition.get("extractions", []) if isinstance(e, dict) and "target" in e]


def _suggest(tx, row: RegistryRow) -> dict | None:
    candidates = normalize(row.proposal)["candidates"]
    reuse = next((c for c in candidates if c.get("kind") == "reuse_mapper"), None)
    if reuse:
        mapper = tx.artifacts.get(reuse["mapper_version_id"])
        return {"path": "reuse", "spec": mapper.definition, "mapper_version_id": mapper.version_id}
    tables = _observed_tables(row.observed_schema)
    if not tables:
        return None
    best: dict[str, dict] = {}
    for c in candidates:
        if c.get("kind") == "schema" and c.get("table") in tables:
            current = best.get(c["table"])
            if current is None or (c["score"], c["jaccard"]) > (current["score"], current["jaccard"]):
                best[c["table"]] = c
    if not best:
        return None
    exact = set(best) == set(tables) and all(not c["extra"] for c in best.values())
    spec = {"kind": "tabular", "extractions": [
        {"target": c["schema_id"], "records": table, "auto": True, "unmapped": "extras"} for table, c in best.items()
    ]}
    return {"path": "exact" if exact else "fallback", "spec": spec}


def _file_info(row: LedgerRow | None) -> dict | None:
    if row is None:
        return None
    return {"file_id": row.file_id, "name": row.original_filename, "file_type": row.file_type, "locator": row.source_path}


def pending(store) -> list[dict]:
    out: list[dict] = []
    with store.transaction() as tx:
        for row in tx.registry.list_by_status(SignatureStatus.PROPOSED):
            proposal = normalize(row.proposal)
            waiting = [f for f in tx.ledger.files_for_signature(row.signature_id) if f.status is FileStatus.QUARANTINED]
            suggestion = _suggest(tx, row)
            out.append({
                "signature_id": row.signature_id,
                "fingerprint": row.fingerprint,
                "state": proposal["review"]["state"],
                "suggested_path": suggestion["path"] if suggestion else "model",
                "candidates": [Candidate(**c).summary() for c in proposal["candidates"][:3]],
                "waiting_files": len(waiting),
                "trigger_file": _file_info(_trigger(tx, row.signature_id)),
            })
    return out


def file_profile(store, signature_id: int) -> dict:
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        trigger = _trigger(tx, signature_id)
        suggestion = _suggest(tx, row)
    proposal = normalize(row.proposal)
    result = {
        "signature_id": signature_id, "fingerprint": row.fingerprint, "observed": row.observed_schema,
        "candidates": proposal["candidates"], "suggested_draft": suggestion, "review": proposal["review"],
        "trigger_file": _file_info(trigger), "profile": None,
    }
    if trigger is not None:
        try:
            result["profile"] = profile(_document(trigger))
        except (locators.LocatorError, ReaderError) as exc:
            result["profile_error"] = str(exc)
    return result


def samples(store, signature_id: int, columns: list[str]) -> dict[str, list[str]]:
    with store.transaction() as tx:
        _proposed(tx, signature_id)
        trigger = _trigger(tx, signature_id)
        cfg = load_settings(tx.artifacts)
    if trigger is None:
        raise OnboardingError(f"no file is waiting under signature {signature_id}")
    doc = _document(trigger)
    wanted: dict[str, list[str]] = {c: [] for c in columns}

    def add(column: str, value) -> None:
        bucket = wanted[column]
        if value is None or value == "" or len(bucket) >= cfg.max_values:
            return
        text = str(value)[: cfg.max_chars]
        if text not in bucket:
            bucket.append(text)

    if isinstance(doc, XmlDocument):
        for el in doc.root.iter():
            if not isinstance(el.tag, str):
                continue
            path = "/" + "/".join(local(node) for node in reversed([el, *el.iterancestors()]))
            if path in wanted and el.text and el.text.strip():
                add(path, el.text.strip())
    else:
        for table in doc.tables:
            for record in table.records:
                for column in columns:
                    if column in record.values:
                        add(column, record.values[column])
    return wanted


def search_schemas(store, columns: list[str] | None = None, text: str | None = None, limit: int = 10) -> list[dict]:
    with store.transaction() as tx:
        schemas = sorted((a.artifact_key, a.definition["columns"]) for a in tx.artifacts.active_schemas())
    if columns:
        observed = {normalize_name(c) for c in columns} - {""}
        ranked = []
        for address, cols in schemas:
            canonical = {normalize_name(c) for c in cols}
            common = observed & canonical
            if not common:
                continue
            ranked.append({
                "address": address, "score": round(len(common) / len(observed), 2),
                "jaccard": round(len(common) / len(observed | canonical), 2),
                "matched": sorted(common), "schema_columns_not_in_file": len(canonical - observed),
            })
        ranked.sort(key=lambda r: (-r["score"], -r["jaccard"], r["address"]))
        return ranked[:limit]
    needle = (text or "").lower()
    return [{"address": address, "columns": len(cols)} for address, cols in schemas if needle in address][:limit]


def get_schema(store, address: str) -> dict:
    key = parse(address).key
    with store.transaction() as tx:
        row = tx.artifacts.active_schema(key)
    if row is None:
        raise OnboardingError(f"no registered schema {key}")
    return {"address": key, "version_no": row.version_no, "columns": row.definition["columns"],
            "source": row.definition.get("source")}


def prior_mappings(store, address: str) -> list[dict]:
    key = parse(address).key
    out: list[dict] = []
    with store.transaction() as tx:
        for sig in tx.registry.list_by_status(SignatureStatus.ACTIVE):
            if sig.mapper_version_id is None:
                continue
            mapper = tx.artifacts.get(sig.mapper_version_id)
            if key in _targets(mapper.definition):
                out.append({"fingerprint": sig.fingerprint, "mapper_version_id": mapper.version_id, "spec": mapper.definition})
    return out


def _clip(value, max_chars: int):
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, dict):
        return {k: _clip(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, max_chars) for v in value]
    return value


def _preview(trigger: LedgerRow, signature_id: int, spec, schemas: dict[str, list[str]], max_chars: int) -> dict:
    if not isinstance(spec, dict):
        return {"errors": ["spec must be a JSON object"]}
    if spec.get("kind") == "plugin":
        return {"errors": ["plugin mappers cannot be submitted through onboarding (design §8.22); use a declarative spec"]}
    try:
        mapper = DeclarativeMapper(parse_spec(spec, known_schemas=set(schemas), known_shapers=registered_shapers()))
        ctx = RunContext(trigger.file_id, signature_id, 0, trigger.request_context or {},
                         {t: schemas[t] for t in mapper.targets})
        result = preview(mapper, _document(trigger), ctx, n=PREVIEW_ROWS)
    except (SpecError, locators.LocatorError, ReaderError) as exc:
        return {"errors": [str(exc)]}
    return {
        "file_id": trigger.file_id, "counts": result.counts, "extras_keys": result.extras_keys,
        "rejects": result.rejects,
        "sample": {t: [_clip(row, max_chars) for row in rows] for t, rows in result.sample.items()},
    }


def _preview_inputs(tx, signature_id: int):
    trigger = _trigger(tx, signature_id)
    schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
    return trigger, schemas, load_settings(tx.artifacts).max_chars


def preview_spec(store, signature_id: int, spec) -> dict:
    with store.transaction() as tx:
        _proposed(tx, signature_id)
        trigger, schemas, max_chars = _preview_inputs(tx, signature_id)
    if trigger is None:
        raise OnboardingError(f"no file is waiting under signature {signature_id}")
    return _preview(trigger, signature_id, spec, schemas, max_chars)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_onboarding.py -v && .venv/Scripts/python -m pytest`
Expected: 7 passed, then the full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/importer/onboarding.py tests/test_onboarding.py
git commit -m "Add the onboarding read side: pending, profile, drafts, samples, search, preview

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Onboarding core — submit, reject, accept and bind

**Files:**
- Modify: `src/importer/onboarding.py` (append)
- Modify: `src/importer/cli.py` (`pending`)
- Test: `tests/test_onboarding_accept.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 9's helpers; `ArtifactRepository.activate`; `SignatureRegistry.activate`; `pipeline.retry`.
- Produces from `onboarding`:
  - `PATHS = ("reuse", "exact", "fallback", "model")`;
  - `submit(store, signature_id, spec, rationale, *, by, note=None, path="model") -> dict`, returning `{"draft_version_id", "preview", "review_table"}` or `{"errors": [...]}`;
  - `reject(store, signature_id, reason, *, by) -> dict`;
  - `bind_summary(store, signature_id, draft_version_id) -> str`;
  - `accept(store, signature_id, draft_version_id, *, by, retry_fn=None) -> dict`, returning `{"signature_id", "mapper_version_id", "reused_existing", "files": [FileResult.to_dict() | {"file_id", "error"}]}`;
  - `review_table(fingerprint, spec, rationale, preview) -> str`.
- Produces CLI command `importer pending`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboarding_accept.py`:
```python
from __future__ import annotations

import pytest

from importer import onboarding
from importer.admin import register_schema
from importer.memory_store import MemoryStore
from importer.models import ArtifactType, Declared, FileStatus, SignatureStatus
from importer.pipeline import process
from tests.support import URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, URL_TARGET, configure, fixture, setup_signature

RATIONALE = [{"column": "url", "target": "newurl", "how": "model", "evidence": "values are URLs", "confidence": 0.9}]


def _url_store() -> MemoryStore:
    store = MemoryStore()
    configure(store, "npvcs")
    with store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="t")
    return store


def _park(store, path, project="VT"):
    return process(path, project=project, cls="PC", file_type="CSV", source="t", store=store)


def _b_copy(tmp_path, name, extra_line):
    path = tmp_path / name
    path.write_text(fixture("url_template_b.csv").read_text() + extra_line, encoding="utf-8")
    return path


def test_submit_then_accept_lands_every_waiting_file(tmp_path):
    store = _url_store()
    first = _park(store, fixture("url_template_b.csv"))
    second = _park(store, _b_copy(tmp_path, "b2.csv", "B9,https://example.net/x,SBC\n"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert onboarding.pending(store)[0]["waiting_files"] == 2
    draft = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    assert "| url | newurl | model | values are URLs | 0.9 |" in draft["review_table"]
    assert "Bind" in onboarding.bind_summary(store, sid, draft["draft_version_id"])
    result = onboarding.accept(store, sid, draft["draft_version_id"], by="tony")
    assert [f["status"] for f in result["files"]] == ["completed", "completed"]
    assert [f["file_id"] for f in result["files"]] == [first.file_id, second.file_id]
    assert result["reused_existing"] is False and result["mapper_version_id"] == draft["draft_version_id"]
    assert len(store.state.raw[URL_TARGET]) == 5
    sig = store.state.registry[sid]
    assert (sig.status, sig.approved_by, sig.proposal["review"]["state"]) == (SignatureStatus.ACTIVE, "tony", "accepted")
    mapper = store.state.artifacts[draft["draft_version_id"]]
    assert mapper.is_active and mapper.approved_by == "tony" and mapper.artifact_key == f"sig.{sid}"
    actions = [d["action_type"] for d in store.state.decisions]
    assert actions.index("draft_mapping") < actions.index("bind_signature")


def test_accept_requires_the_latest_draft():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    with pytest.raises(onboarding.OnboardingError, match="no draft ready"):
        onboarding.accept(store, sid, 1, by="tony")
    old = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")["draft_version_id"]
    new = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony", note="same again")["draft_version_id"]
    with pytest.raises(onboarding.OnboardingError, match="not the latest"):
        onboarding.accept(store, sid, old, by="tony")
    review = store.state.registry[sid].proposal["review"]
    assert review["notes"][0]["text"] == "same again" and review["draft_version_id"] == new


def test_invalid_submissions_store_nothing():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert "errors" in onboarding.submit(store, sid, {"kind": "plugin", "ref": "x:Y"}, [], by="tony")
    with pytest.raises(onboarding.OnboardingError, match="path"):
        onboarding.submit(store, sid, URL_SPEC_B, [], by="tony", path="guess")
    assert not [a for a in store.state.artifacts.values() if a.artifact_type is ArtifactType.MAPPER]
    assert store.state.registry[sid].proposal["review"]["state"] == "needs_mapping"


def test_reject_and_reopen():
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    assert onboarding.reject(store, sid, "not a URL template", by="tony") == {"signature_id": sid, "state": "rejected"}
    assert onboarding.pending(store)[0]["state"] == "rejected"
    onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    assert onboarding.pending(store)[0]["state"] == "ready_for_review"
    assert "reject_mapping" in [d["action_type"] for d in store.state.decisions]


def test_identical_spec_binds_to_the_existing_mapper():
    store = MemoryStore()
    mapper_id = setup_signature(store, fixture("url_template_a.csv"), Declared.normalize("VT", "PC", "CSV"),
                                URL_SCHEMA, URL_SPEC_A, "urls_a")
    _park(store, fixture("url_template_a.csv"), project="ME")
    item = onboarding.pending(store)[0]
    suggestion = onboarding.file_profile(store, item["signature_id"])["suggested_draft"]
    assert suggestion["path"] == "reuse"
    draft = onboarding.submit(store, item["signature_id"], suggestion["spec"], [], by="tony", path="reuse")
    result = onboarding.accept(store, item["signature_id"], draft["draft_version_id"], by="tony")
    assert result["mapper_version_id"] == mapper_id and result["reused_existing"] is True
    assert store.state.artifacts[draft["draft_version_id"]].is_active is False
    assert result["files"][0]["status"] == "completed"


def test_repeated_miss_keeps_the_draft(tmp_path):
    store = _url_store()
    _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")
    _park(store, _b_copy(tmp_path, "b3.csv", "B7,https://example.net/y,SBC\n"))
    [item] = onboarding.pending(store)
    assert item["state"] == "ready_for_review" and item["waiting_files"] == 2


def test_a_retry_failure_is_reported_and_does_not_undo_the_bind():
    store = _url_store()
    parked = _park(store, fixture("url_template_b.csv"))
    sid = onboarding.pending(store)[0]["signature_id"]
    draft = onboarding.submit(store, sid, URL_SPEC_B, RATIONALE, by="tony")

    def broken(file_id, *, store):
        raise RuntimeError("share offline")

    result = onboarding.accept(store, sid, draft["draft_version_id"], by="tony", retry_fn=broken)
    assert result["files"] == [{"file_id": parked.file_id, "error": "RuntimeError: share offline"}]
    assert store.state.registry[sid].status is SignatureStatus.ACTIVE
    assert store.state.ledger[parked.file_id].status is FileStatus.QUARANTINED
```

In `tests/test_cli.py`, add:
```python
def test_pending(store, capsys):
    cli.main(["process", str(fixture("url_template_b.csv")), "--project", "VT", "--class", "PC",
              "--type", "CSV", "--source", "cli"], store=store)
    capsys.readouterr()
    assert cli.main(["pending"], store=store) == 0
    [item] = _json_out(capsys)
    assert item["suggested_path"] == "fallback" and item["waiting_files"] == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_onboarding_accept.py -v`
Expected: FAIL with `AttributeError: module 'importer.onboarding' has no attribute 'submit'`.

- [ ] **Step 3: Implement submit, reject and accept**

Append to `src/importer/onboarding.py`, and add `import json` and `from .models import ArtifactType` to its imports:
```python
PATHS = ("reuse", "exact", "fallback", "model")


def review_table(fingerprint: str, spec: dict, rationale: list[dict], result: dict) -> str:
    lines = [f"**{fingerprint}** -> {', '.join(_targets(spec)) or '(no targets)'}", "",
             "| file column | target | how | evidence | confidence |", "|---|---|---|---|---|"]
    for item in rationale or []:
        lines.append("| " + " | ".join(str(item.get(k, "")) for k in ("column", "target", "how", "evidence", "confidence")) + " |")
    counts = ", ".join(f"{t}: {n} rows" for t, n in result.get("counts", {}).items()) or "no rows"
    extras = "; ".join(f"{t}: {', '.join(keys)}" for t, keys in result.get("extras_keys", {}).items()) or "none"
    rejects = ", ".join(f"{code}: {n}" for code, n in result.get("rejects", {}).items()) or "none"
    lines += ["", f"Preview: {counts}. _extras: {extras}. Rejects: {rejects}."]
    return "\n".join(lines)


def submit(store, signature_id: int, spec, rationale: list[dict], *, by: str, note: str | None = None,
           path: str = "model") -> dict:
    if path not in PATHS:
        raise OnboardingError(f"path must be one of {PATHS}, got {path!r}")
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        trigger, schemas, max_chars = _preview_inputs(tx, signature_id)
    if trigger is None:
        raise OnboardingError(f"no file is waiting under signature {signature_id}")
    result = _preview(trigger, signature_id, spec, schemas, max_chars)
    if "errors" in result:
        return result
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        proposal = normalize(row.proposal)
        review = proposal["review"]
        if review["state"] == "accepted":
            raise OnboardingError(f"signature {signature_id} is already accepted")
        draft = tx.artifacts.put_version(ArtifactType.MAPPER, f"sig.{signature_id}", spec, created_by=by, activate=False)
        review.update(state="ready_for_review", draft_version_id=draft.version_id, path=path,
                      rationale=list(rationale or []), preview=result)
        if note:
            review["notes"].append({"at": _now(), "by": by, "text": note})
        review["history"].append({"at": _now(), "by": by, "event": "draft", "draft_version_id": draft.version_id})
        tx.registry.set_proposal(signature_id, proposal)
        tx.audit.decision(trigger.file_id, "draft_mapping", observed={"signature": row.fingerprint},
                          proposed={"draft_version_id": draft.version_id, "path": path, "rationale": review["rationale"]},
                          applied=False, approval=None)
    return {"draft_version_id": draft.version_id, "preview": result,
            "review_table": review_table(row.fingerprint, spec, rationale, result)}


def reject(store, signature_id: int, reason: str, *, by: str) -> dict:
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        proposal = normalize(row.proposal)
        review = proposal["review"]
        if review["state"] == "accepted":
            raise OnboardingError(f"signature {signature_id} is already accepted")
        review["state"] = "rejected"
        review["notes"].append({"at": _now(), "by": by, "text": reason})
        review["history"].append({"at": _now(), "by": by, "event": "reject", "draft_version_id": review["draft_version_id"]})
        tx.registry.set_proposal(signature_id, proposal)
        trigger = _trigger(tx, signature_id)
        tx.audit.decision(trigger.file_id if trigger else None, "reject_mapping", observed={"signature": row.fingerprint},
                          proposed={"reason": reason}, applied=False, approval=by)
    return {"signature_id": signature_id, "state": "rejected"}


def _canonical(definition: dict) -> str:
    return json.dumps(definition, sort_keys=True, separators=(",", ":"))


def _identical_bound(tx, draft) -> int | None:
    wanted = _canonical(draft.definition)
    for sig in tx.registry.list_by_status(SignatureStatus.ACTIVE):
        if sig.mapper_version_id is None or sig.mapper_version_id == draft.version_id:
            continue
        if _canonical(tx.artifacts.get(sig.mapper_version_id).definition) == wanted:
            return sig.mapper_version_id
    return None


def bind_summary(store, signature_id: int, draft_version_id: int) -> str:
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        draft = tx.artifacts.get(draft_version_id)
        waiting = [f for f in tx.ledger.files_for_signature(signature_id) if f.status is FileStatus.QUARANTINED]
    targets = ", ".join(_targets(draft.definition)) or "its mapper"
    return f"Bind {row.fingerprint} to {targets} and retry {len(waiting)} waiting file(s)?"


def _default_retry():
    from .pipeline import retry
    return retry


def accept(store, signature_id: int, draft_version_id: int, *, by: str, retry_fn=None) -> dict:
    retry_fn = retry_fn or _default_retry()
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        proposal = normalize(row.proposal)
        review = proposal["review"]
        if review["state"] != "ready_for_review":
            raise OnboardingError(f"signature {signature_id} has no draft ready for review (state {review['state']})")
        if review["draft_version_id"] != draft_version_id:
            raise OnboardingError(
                f"draft {draft_version_id} is not the latest draft ({review['draft_version_id']}); review the latest draft"
            )
        draft = tx.artifacts.get(draft_version_id)
        bound = _identical_bound(tx, draft)
        if bound is None:
            tx.artifacts.activate(draft_version_id, approved_by=by)
            bound = draft_version_id
        tx.registry.activate(signature_id, bound, approved_by=by)
        review["state"] = "accepted"
        review["history"].append({"at": _now(), "by": by, "event": "accept", "draft_version_id": draft_version_id,
                                  "mapper_version_id": bound})
        tx.registry.set_proposal(signature_id, proposal)
        waiting = [f.file_id for f in tx.ledger.files_for_signature(signature_id) if f.status is FileStatus.QUARANTINED]
        tx.audit.decision(waiting[0] if waiting else None, "bind_signature",
                          observed={"signature": row.fingerprint, "draft_version_id": draft_version_id},
                          proposed={"mapper_version_id": bound, "waiting_files": waiting}, applied=True, approval=by)
    files: list[dict] = []
    for file_id in waiting:
        try:
            files.append(retry_fn(file_id, store=store).to_dict())
        except Exception as exc:  # reported per file; the bind stands
            files.append({"file_id": file_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"signature_id": signature_id, "mapper_version_id": bound, "reused_existing": bound != draft_version_id,
            "files": files}
```

- [ ] **Step 4: Add `importer pending`**

In `src/importer/cli.py`, add:
```python
def cmd_pending(args, store) -> int:
    from .onboarding import pending

    _print(pending(store))
    return 0
```
and register it in `build_parser`, after `retry`:
```python
    sub.add_parser("pending", help="list unknown signatures waiting for a mapping").set_defaults(func=cmd_pending)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_onboarding_accept.py tests/test_cli.py -v && .venv/Scripts/python -m pytest`
Expected: 7 + CLI tests pass, then the full suite passes.

- [ ] **Step 6: Commit**

```bash
git add src/importer/onboarding.py src/importer/cli.py tests/test_onboarding_accept.py tests/test_cli.py
git commit -m "Add submit, reject and accept: bind a signature and retry every waiting file

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The MCP server, its `onboard` prompt and the spec reference

**Files:**
- Create: `src/importer/mcp_server.py`
- Create: `src/importer/mcp_reference.md`
- Create: `.mcp.json`, `.claude/settings.json`
- Modify: `pyproject.toml` (package data), `src/importer/cli.py` (`mcp`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes:
  - from Tasks 9–10: `onboarding.pending`, `file_profile`, `samples`, `search_schemas`, `get_schema`, `prior_mappings`, `preview_spec`, `submit`, `reject`, `bind_summary`, `accept` and `OnboardingError`;
  - from Task 6: `schema_sources.import_schemas`, `live_source` and `SchemaSourceError`.
- Produces:
  - `mcp_server.create_server(store, *, user=None, target="", retry_fn=None, live_source_factory=live_source) -> MCPServer`;
  - `mcp_server.ONBOARD_PROMPT`;
  - `mcp_server.spec_reference() -> str`;
  - CLI command `importer mcp` (stdio).
- Tool names, used by Task 14 and the docs:
  - `list_pending`, `get_file_profile`, `search_schemas`, `get_schema`, `get_prior_mappings`, `get_samples`;
  - `get_spec_reference`, `preview_mapping`, `submit_mapping`, `reject_mapping`;
  - `accept_mapping`, `register_schema`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_server.py`:
```python
from __future__ import annotations

import json

import pytest
from mcp import Client
from mcp.types import ElicitResult

from importer.admin import register_schema
from importer.mcp_server import create_server, spec_reference
from importer.memory_store import MemoryStore
from importer.models import SignatureStatus
from importer.pipeline import process
from tests.support import URL_SCHEMA, URL_SPEC_B, URL_TARGET, configure, fixture

TOOLS = {
    "list_pending", "get_file_profile", "search_schemas", "get_schema", "get_prior_mappings", "get_samples",
    "get_spec_reference", "preview_mapping", "submit_mapping", "reject_mapping", "accept_mapping", "register_schema",
}
RATIONALE = [{"column": "url", "target": "newurl", "how": "model", "evidence": "values are URLs", "confidence": 0.9}]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _parked() -> MemoryStore:
    store = MemoryStore()
    configure(store, "npvcs")
    with store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="t")
    process(fixture("url_template_b.csv"), project="VT", cls="PC", file_type="CSV", source="t", store=store)
    return store


def _json(result) -> dict:
    return json.loads(result.content[0].text)


async def _approve(context, params):
    return ElicitResult(action="accept", content={"confirm": True})


async def _decline(context, params):
    return ElicitResult(action="decline")


@pytest.mark.anyio
async def test_tools_annotations_and_the_onboard_prompt():
    async with Client(create_server(MemoryStore(), user="tony"), raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == TOOLS
        accept = tools["accept_mapping"].annotations.model_dump(by_alias=True)
        listing = tools["list_pending"].annotations.model_dump(by_alias=True)
        assert accept["destructiveHint"] is True and accept["readOnlyHint"] is False
        assert listing["readOnlyHint"] is True
        assert [p.name for p in (await client.list_prompts()).prompts] == ["onboard"]
        prompt = await client.get_prompt("onboard")
        assert "accept_mapping" in prompt.messages[0].content.text
        assert "$parent(" in spec_reference() and '"kind": "plugin"' in spec_reference()


@pytest.mark.anyio
async def test_end_to_end_with_elicitation_and_audit():
    store = _parked()
    async with Client(create_server(store, user="tony", target="importer @ test [DEV]"), raise_exceptions=True,
                      elicitation_callback=_approve) as client:
        listing = _json(await client.call_tool("list_pending", {}))
        assert listing["target"] == "importer @ test [DEV]"
        sid = listing["pending"][0]["signature_id"]
        samples = _json(await client.call_tool("get_samples", {"signature_id": sid, "columns": ["url"]}))
        assert len(samples["url"]) == 2
        draft = _json(await client.call_tool("submit_mapping", {"signature_id": sid, "spec": URL_SPEC_B, "rationale": RATIONALE}))
        result = _json(await client.call_tool("accept_mapping", {"signature_id": sid, "draft_version_id": draft["draft_version_id"]}))
    assert result["files"][0]["status"] == "completed"
    logged = [d for d in store.state.decisions if d["action_type"].startswith("mcp:")]
    assert {d["action_type"] for d in logged} >= {"mcp:list_pending", "mcp:get_samples", "mcp:submit_mapping", "mcp:accept_mapping"}
    assert next(d for d in logged if d["action_type"] == "mcp:accept_mapping")["approval"] == "tony"
    assert all("example.net" not in json.dumps(d, default=str) for d in store.state.decisions)  # samples never logged


@pytest.mark.anyio
async def test_declined_elicitation_binds_nothing():
    store = _parked()
    async with Client(create_server(store, user="tony"), raise_exceptions=True, elicitation_callback=_decline) as client:
        sid = _json(await client.call_tool("list_pending", {}))["pending"][0]["signature_id"]
        draft = _json(await client.call_tool("submit_mapping", {"signature_id": sid, "spec": URL_SPEC_B, "rationale": RATIONALE}))
        result = _json(await client.call_tool("accept_mapping", {"signature_id": sid, "draft_version_id": draft["draft_version_id"]}))
    assert result == {"accepted": False, "reason": "the user declined"}
    assert store.state.registry[sid].status is SignatureStatus.PROPOSED


@pytest.mark.anyio
async def test_errors_come_back_as_messages_and_register_schema_from_columns():
    store = _parked()
    async with Client(create_server(store, user="tony"), raise_exceptions=True, elicitation_callback=_approve) as client:
        missing = _json(await client.call_tool("get_schema", {"address": "npvcs.relational.nope"}))
        assert missing == {"error": "no registered schema npvcs.relational.nope"}
        report = _json(await client.call_tool("register_schema", {"address_or_pattern": "NPVCS.dbo.extra",
                                                                  "columns": ["a", "b"]}))
    assert report["added"][0]["address"] == "npvcs.dbo.extra"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.mcp_server'`.

- [ ] **Step 3: Write the spec reference**

`src/importer/mcp_reference.md`:
````markdown
# Mapping spec reference (for onboarding)

A mapping spec is JSON data that stable importer code runs. You never write code;
`"kind": "plugin"` is refused. Every `target` must be a registered schema address
(`database.schema.table`, see `search_schemas` / `get_schema`). Values land exactly
as written: never clean, trim, convert or guess. A file column you cannot place with
confidence stays unmapped and lands in `_extras` (nothing is lost).

## Tabular files (CSV/TSV/pipe, Excel, JSON, NDJSON): `"kind": "tabular"`

```json
{"kind": "tabular",
 "extractions": [
   {"target": "npvcs.relational.url_template",
    "records": "*",
    "auto": true,
    "fields": {"newurl": "url", "file_id": "$file_id", "run_id": "$context.run_id"},
    "unmapped": "extras"}]}
```

- `records`: the Excel sheet name, or `"*"` (the only table of CSV/JSON/NDJSON).
- Column names are normalized: lowercase letters and digits only ("URL ID" -> `urlid`).
- `auto: true` maps every file column whose normalized name equals a schema column.
- `fields`: `{schema_column: source}`. The source is a normalized file column or a special source. `fields` overrides `auto`.
- `unmapped`: `"extras"` (default; leftover columns go to `_extras`) or `"ignore"`.
- `unpivot`: `{"over": "*" | ["col", ...], "name_column": "...", "value_column": "..."}` gives one row per listed column. The row identity gets the suffix `#<column>`.
- `shapers` (Excel layouts), applied before extraction:
  - `{"name": "excel.anchor_header", "options": {"sheet": "...", "anchor": "<normalized header cell>"}}` flattens multi-row headers starting at the anchor cell.
  - `{"name": "excel.crosstab_unpivot", "options": {"sheet": "...", "id_columns": [...]}}` melts a cross-tab into `benefit_name` / `value`.

## XML files: `"kind": "xml"`

```json
{"kind": "xml",
 "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
 "value_child": "x:cellValue",
 "extractions": [
   {"target": "npvcs.xml.formulary_drug_list",
    "records": "/x:prescriptionDrugTemplateVO/x:drugList/x:drugListVO/x:drugVOList/x:drugVO",
    "fields": {"rxcui": "x:rxcui", "drug_list_id": "../../x:drugListID", "fileid": "$file_id"},
    "unmapped": "extras"}]}
```

- `namespaces`: prefixes for every XPath. `value_child` handles templates whose values sit in a child element (e.g. CMS `<cellValue>`).
- `records`: an absolute XPath. Each match becomes one row.
- `fields`: XPath relative to the record. `../` reaches ancestors, and absolute paths read header values.
  - A path matching nothing lands NULL.
  - A path matching several nodes lands the first value, and the full list goes to `_extras`.
- `{"*": "x:*"}` maps every child element to the column of the same name (unknown names go to `_extras`).
- `unpivot`: `{"over": "<XPath>", "name_column": "...", "value_column": "..."}`.
- Several extractions in one spec feed several tables. `$parent(<target>)` links a child row to its parent row's identity.

## Special sources

`$file_id`, `$row_identity`, `$context.<key>` (requestor context such as `serffbindername`, `run_id`), and `$parent(<target>)` (XML only).

## Onboarding rules

1. Prefer a draft from `get_file_profile` (`reuse` / `exact` / `fallback`) when it fits.
2. Mappings already bound to the schema (`get_prior_mappings`) are the strongest evidence.
3. Use `get_samples` only for columns whose names do not settle the mapping.
4. Always `preview_mapping` before `submit_mapping`, and give a rationale for every non-exact column.
5. Never call `accept_mapping` until the user explicitly approves the latest draft.
````

- [ ] **Step 4: Implement the server**

`src/importer/mcp_server.py`:
```python
"""MCP server: the user's own Claude client is the onboarding agent (design §8.19).

Tools expose metadata only — headers, schemas, earlier mappings and capped samples;
row data never crosses MCP. accept_mapping and register_schema are sign-off tools:
annotated destructive so clients ask first, and, when the client supports it, they
also ask the user directly (elicitation). The approver is the OS user, never a
model-supplied value. Every call writes a decision_log row; sample values never do.
"""

from __future__ import annotations

import getpass
import json
from importlib.resources import files

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import onboarding
from .addresses import AddressError
from .admin import SchemaRefused
from .locators import LocatorError
from .schema_sources import SchemaSourceError, import_schemas, live_source

EXPECTED = (onboarding.OnboardingError, AddressError, SchemaRefused, SchemaSourceError, LocatorError)

ONBOARD_PROMPT = """You are helping onboard files the importer could not route. Work through them with the user.

1. Call list_pending. Take one signature at a time, oldest first.
2. Call get_file_profile. If suggested_draft is reuse or exact, call preview_mapping with its spec and present it.
3. Otherwise gather evidence: get_prior_mappings for each candidate schema (strongest evidence), search_schemas
   when the candidates are weak, and get_samples only for columns whose names alone do not settle the mapping.
   Read get_spec_reference, then draft a declarative spec. Never use "kind": "plugin".
4. Never invent a target column. If unsure, leave a file column unmapped so it lands in _extras, and say so.
5. Call preview_mapping and fix any problems, then submit_mapping with a rationale entry
   {column, target, how, evidence, confidence} for every non-exact column.
6. Show the user the review table and the preview summary. Ask them to accept, correct, or reject.
7. For corrections, submit_mapping again with the user's words as note. For a rejection, call reject_mapping.
8. Call accept_mapping only after the user explicitly approves the latest draft, then report each file's result.
9. Never edit code or files. Use only the importer tools.
"""


class Confirm(BaseModel):
    confirm: bool


def _annotations(*, read_only: bool, destructive: bool) -> ToolAnnotations:
    if "read_only_hint" in ToolAnnotations.model_fields:
        return ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive,
                               idempotent_hint=read_only, open_world_hint=False)
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=read_only, openWorldHint=False)


READ_ONLY = _annotations(read_only=True, destructive=False)
WRITES = _annotations(read_only=False, destructive=False)
SIGN_OFF = _annotations(read_only=False, destructive=True)


def spec_reference() -> str:
    return files("importer").joinpath("mcp_reference.md").read_text(encoding="utf-8")


def _out(data) -> str:
    return json.dumps(data, indent=2, default=str)


def _supports_elicitation(ctx: Context) -> bool:
    params = getattr(ctx.session, "client_params", None)
    capabilities = getattr(params, "capabilities", None)
    return getattr(capabilities, "elicitation", None) is not None


async def _confirmed(ctx: Context, message: str) -> bool:
    if not _supports_elicitation(ctx):
        return True  # the client's own tool-approval prompt is the gate (design §8.19)
    result = await ctx.elicit(message=message, schema=Confirm)
    return result.action == "accept" and bool(getattr(result.data, "confirm", False))


def create_server(store, *, user: str | None = None, target: str = "", retry_fn=None,
                  live_source_factory=live_source) -> MCPServer:
    user = user or getpass.getuser()
    mcp = MCPServer("importer")

    def log(tool: str, args: dict, *, signoff: bool = False) -> None:
        with store.transaction() as tx:
            tx.audit.decision(None, f"mcp:{tool}", observed=args, proposed=None, applied=False,
                              approval=user if signoff else None)

    def run(tool: str, args: dict, work, *, signoff: bool = False) -> str:
        log(tool, args, signoff=signoff)
        try:
            return _out(work())
        except EXPECTED as exc:
            return _out({"error": str(exc)})

    @mcp.tool(annotations=READ_ONLY)
    def list_pending() -> str:
        """Files waiting for a mapping: signature, review state, suggested path, top candidates, waiting files."""
        return run("list_pending", {}, lambda: {"target": target, "pending": onboarding.pending(store)})

    @mcp.tool(annotations=READ_ONLY)
    def get_file_profile(signature_id: int) -> str:
        """A waiting file's structure (columns, or XML element paths with counts), candidates and suggested draft."""
        return run("get_file_profile", {"signature_id": signature_id},
                   lambda: onboarding.file_profile(store, signature_id))

    @mcp.tool(annotations=READ_ONLY)
    def search_schemas(columns: list[str] | None = None, text: str | None = None, limit: int = 10) -> str:
        """Registered schemas ranked by overlap with the given columns, or filtered by address text."""
        return run("search_schemas", {"columns": columns, "text": text, "limit": limit},
                   lambda: onboarding.search_schemas(store, columns=columns, text=text, limit=limit))

    @mcp.tool(annotations=READ_ONLY)
    def get_schema(address: str) -> str:
        """A registered schema's columns (address = database.schema.table)."""
        return run("get_schema", {"address": address}, lambda: onboarding.get_schema(store, address))

    @mcp.tool(annotations=READ_ONLY)
    def get_prior_mappings(address: str) -> str:
        """Specs already bound to signatures that feed this schema; the strongest evidence for a new mapping."""
        return run("get_prior_mappings", {"address": address}, lambda: onboarding.prior_mappings(store, address))

    @mcp.tool(annotations=READ_ONLY)
    def get_samples(signature_id: int, columns: list[str]) -> str:
        """A few distinct sample values per column of the waiting file (capped by the shared settings)."""
        return run("get_samples", {"signature_id": signature_id, "columns": columns},
                   lambda: onboarding.samples(store, signature_id, columns))

    @mcp.tool(annotations=READ_ONLY)
    def get_spec_reference() -> str:
        """The mapping spec format and the onboarding rules."""
        log("get_spec_reference", {})
        return spec_reference()

    @mcp.tool(annotations=READ_ONLY)
    def preview_mapping(signature_id: int, spec: dict) -> str:
        """Dry-run a spec against the waiting file: per-target counts, _extras keys, rejects, a few sample rows."""
        return run("preview_mapping", {"signature_id": signature_id, "spec": spec},
                   lambda: onboarding.preview_spec(store, signature_id, spec))

    @mcp.tool(annotations=WRITES)
    def submit_mapping(signature_id: int, spec: dict, rationale: list[dict], note: str | None = None,
                       path: str = "model") -> str:
        """Store a draft for the user's review (validated and previewed first). Returns the review table."""
        args = {"signature_id": signature_id, "spec": spec, "rationale": rationale, "note": note, "path": path}
        return run("submit_mapping", args,
                   lambda: onboarding.submit(store, signature_id, spec, rationale, by=user, note=note, path=path))

    @mcp.tool(annotations=WRITES)
    def reject_mapping(signature_id: int, reason: str) -> str:
        """Record that the user rejected this proposal; its files stay quarantined."""
        return run("reject_mapping", {"signature_id": signature_id, "reason": reason},
                   lambda: onboarding.reject(store, signature_id, reason, by=user))

    @mcp.tool(annotations=SIGN_OFF)
    async def accept_mapping(signature_id: int, draft_version_id: int, ctx: Context) -> str:
        """SIGN-OFF. Bind the signature to the reviewed draft and retry every waiting file.
        Call only after the user explicitly approves the latest draft."""
        args = {"signature_id": signature_id, "draft_version_id": draft_version_id}
        try:
            message = onboarding.bind_summary(store, signature_id, draft_version_id)
        except EXPECTED as exc:
            return _out({"error": str(exc)})
        if not await _confirmed(ctx, message):
            log("accept_mapping", {**args, "declined": True}, signoff=True)
            return _out({"accepted": False, "reason": "the user declined"})
        return run("accept_mapping", args, signoff=True,
                   work=lambda: onboarding.accept(store, signature_id, draft_version_id, by=user, retry_fn=retry_fn))

    @mcp.tool(annotations=SIGN_OFF)
    async def register_schema(address_or_pattern: str, ctx: Context, columns: list[str] | None = None) -> str:
        """SIGN-OFF. Register destination schema(s): from the live destination (wildcards allowed,
        e.g. NPVCS.*.*), or from an explicit column list for one table."""
        source = "the given column list" if columns is not None else "the live destination"
        if not await _confirmed(ctx, f"Register destination schema(s) {address_or_pattern} from {source}?"):
            log("register_schema", {"address_or_pattern": address_or_pattern, "declined": True}, signoff=True)
            return _out({"registered": False, "reason": "the user declined"})

        def work():
            if columns is not None:
                return import_schemas(store, address_or_pattern, by=user, columns=columns)
            return import_schemas(store, address_or_pattern, by=user,
                                  source=live_source_factory(store, address_or_pattern.split(".")[0]))

        return run("register_schema", {"address_or_pattern": address_or_pattern, "columns": columns}, work, signoff=True)

    @mcp.prompt()
    def onboard() -> str:
        """Map files waiting for review, with the user's approval."""
        return ONBOARD_PROMPT

    return mcp
```

- [ ] **Step 5: Package data, CLI command and Claude Code registration**

- **Package data.** In `pyproject.toml`, add:
  ```toml
  [tool.setuptools.package-data]
  importer = ["mcp_reference.md"]
  ```
- **CLI command.** In `src/importer/cli.py`, add:
  ```python
  def cmd_mcp(args, store) -> int:
      from .environment import banner, target_of
      from .mcp_server import create_server

      identity = target_of(store)
      create_server(store, target=banner(identity) if identity else "").run(transport="stdio")
      return 0
  ```
  and register it in `build_parser`:
  ```python
      sub.add_parser("mcp", help="run the onboarding MCP server over stdio (for Claude Code / Desktop)").set_defaults(func=cmd_mcp)
  ```
  (`main` prints the banner to stderr, which is safe for stdio servers. Logging also goes to stderr.)
- **`.mcp.json`** at the repo root:
  ```json
  {
    "mcpServers": {
      "importer": {
        "command": ".venv/Scripts/python",
        "args": ["-m", "importer.cli", "mcp"]
      }
    }
  }
  ```
- **`.claude/settings.json`** at the repo root. It auto-approves the read and draft tools only; the two sign-off tools always ask:
  ```json
  {
    "permissions": {
      "allow": [
        "mcp__importer__list_pending",
        "mcp__importer__get_file_profile",
        "mcp__importer__search_schemas",
        "mcp__importer__get_schema",
        "mcp__importer__get_prior_mappings",
        "mcp__importer__get_samples",
        "mcp__importer__get_spec_reference",
        "mcp__importer__preview_mapping",
        "mcp__importer__submit_mapping",
        "mcp__importer__reject_mapping"
      ],
      "ask": [
        "mcp__importer__accept_mapping",
        "mcp__importer__register_schema"
      ]
    }
  }
  ```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pip install -e ".[dev]" && .venv/Scripts/python -m pytest tests/test_mcp_server.py -v && .venv/Scripts/python -m pytest`
Expected: 4 passed, then the full suite passes.

If `test_tools_annotations_and_the_onboard_prompt` fails on `model_dump(by_alias=True)` keys, print the dump and compare the field names with Task 0's output. Use whichever key spelling the dump shows, and keep the assertion values the same.

- [ ] **Step 7: Smoke-test the stdio server**

Run: `.venv/Scripts/python -m importer.cli mcp < /dev/null`
Expected: the banner `importer @ 2018NPI/import_agent_dev [DEV]` on stderr, then a clean exit when stdin closes, with no traceback.

- [ ] **Step 8: Commit**

```bash
git add src/importer/mcp_server.py src/importer/mcp_reference.md src/importer/cli.py pyproject.toml .mcp.json .claude/settings.json tests/test_mcp_server.py
git commit -m "Add the importer MCP server: onboarding tools, onboard prompt, sign-off gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: DEV integration — migrate M2a acceptance and add M2b cases

**Files:**
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_acceptance.py`
- Create: `tests/integration/test_m2b.py`

**Interfaces:**
- Consumes: `environment.read` and `environment.dev_guard`, `settings`, `schema_sources`, `onboarding`, `db.engine_for` and `db.load_connection_document`.
- Produces: the integration fixtures `mssql_store` (DEV-guarded, with the NPVCS and RX destinations configured) and `dev_identity`.

- [ ] **Step 1: Guard and configure in the fixture**

Replace `tests/integration/conftest.py` with:
```python
"""Integration fixtures: real DEV SQL Server via database.env (opt-in: pytest -m integration)."""

from __future__ import annotations

import pytest

from importer.db import ConfigError, load_connection_document


@pytest.fixture(scope="session")
def mssql_store():
    try:
        doc = load_connection_document()
    except ConfigError as exc:
        pytest.skip(f"no DEV database configured: {exc}")
    from importer import environment, settings
    from importer.db import get_engine
    from importer.mssql_store import MssqlStore

    store = MssqlStore(get_engine())
    with store.engine.connect() as conn:
        identity = environment.read(conn)
    reason = environment.dev_guard(identity.environment, identity.database)
    if reason:
        pytest.skip(f"integration tests only run against a DEV database: {reason}")
    with store.transaction() as tx:
        have = settings.load(tx.artifacts)
        for name in ("NPVCS", "RX"):
            if have.destination(name) is None:
                settings.set_destination(tx, name, server=doc["server"], database=name, by="integration")
    return store


@pytest.fixture(scope="session")
def dev_identity(mssql_store):
    from importer import environment

    with mssql_store.engine.connect() as conn:
        return environment.read(conn)
```

- [ ] **Step 2: Run the migrated M2a acceptance file**

`tests/integration/test_acceptance.py` needs no code change. Task 3's `sed` already moved it to addresses, and the fixture above now configures the NPVCS and RX destinations its schemas need.

Run: `.venv/Scripts/python -m pytest -m integration tests/integration/test_acceptance.py -v`
Expected: 5 passed.

- [ ] **Step 3: Write the M2b integration tests**

`tests/integration/test_m2b.py`:
```python
"""M2b on the DEV SQL Server (design §8.20)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from importer import environment, onboarding, settings
from importer.admin import register_schema
from importer.db import engine_for, load_connection_document
from importer.models import FileStatus
from importer.pipeline import process
from importer.schema_sources import import_schemas, live_source
from tests.support import URL_SCHEMA, URL_SPEC_B, URL_TARGET, fixture

pytestmark = pytest.mark.integration


def test_identity_is_dev_and_unmarked_databases_are_refused(dev_identity):
    assert dev_identity.environment == "DEV" and environment.dev_guard("DEV", dev_identity.database) is None
    with engine_for(load_connection_document()["server"], "master").connect() as conn:
        master = environment.read(conn)
    assert environment.dev_guard(master.environment, master.database) is not None


def test_live_schema_import_from_a_stand_in_destination(mssql_store, dev_identity):
    uid = uuid.uuid4().hex[:8]
    doc = load_connection_document()
    with mssql_store.engine.begin() as conn:
        conn.exec_driver_sql("IF SCHEMA_ID(N'it_dest') IS NULL EXEC(N'CREATE SCHEMA it_dest')")
        conn.exec_driver_sql(f"CREATE TABLE it_dest.t_{uid} (a NVARCHAR(10), b INT)")
    try:
        with mssql_store.transaction() as tx:
            settings.set_destination(tx, "IMPORT_AGENT_DEV", server=doc["server"], database=doc["database"], by="it")
        report = import_schemas(mssql_store, f"IMPORT_AGENT_DEV.it_dest.t_{uid}", by="it",
                                source=live_source(mssql_store, "IMPORT_AGENT_DEV"))
        assert report["added"][0]["address"] == f"import_agent_dev.it_dest.t_{uid}"
        assert report["added"][0]["new_columns"] == ["a", "b"]
    finally:
        with mssql_store.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE it_dest.t_{uid}")


def test_onboarding_end_to_end_without_a_model(mssql_store, tmp_path):
    uid = uuid.uuid4().hex[:6].upper()
    with mssql_store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="it")
    path = tmp_path / "b.csv"
    path.write_text(fixture("url_template_b.csv").read_text() + f"B{uid},https://example.net/{uid},SBC\n", encoding="utf-8")
    project = f"IT{uid}"
    parked = process(path, project=project, cls="PC", file_type="CSV", source="it", store=mssql_store)
    assert parked.status is FileStatus.QUARANTINED
    item = next(p for p in onboarding.pending(mssql_store) if p["fingerprint"].startswith(f"{project}|"))
    draft = onboarding.submit(mssql_store, item["signature_id"], URL_SPEC_B, [], by="it")
    result = onboarding.accept(mssql_store, item["signature_id"], draft["draft_version_id"], by="it")
    assert [f["status"] for f in result["files"]] == ["completed"]
    with mssql_store.transaction() as tx:
        assert tx.raw.count_landed(parked.file_id, URL_TARGET) == 3


def test_locator_round_trip_over_unc(mssql_store, tmp_path):
    local = tmp_path / "a.csv"
    local.write_text(fixture("url_template_a.csv").read_text() + f"U{uuid.uuid4().hex[:6]},https://e.org/u,SBC\n",
                     encoding="utf-8")
    drive, rest = os.path.splitdrive(str(local.resolve()))
    unc = f"\\\\localhost\\{drive.rstrip(':')}${rest}"
    if not Path(unc).exists():
        pytest.skip(f"administrative share not reachable: {unc}")
    parked = process(unc, project=f"UNC{uuid.uuid4().hex[:4].upper()}", cls="PC", file_type="CSV", source="it",
                     store=mssql_store)
    with mssql_store.transaction() as tx:
        assert tx.ledger.get(parked.file_id).source_path == unc
```

- [ ] **Step 4: Run all integration tests**

Run:
```bash
.venv/Scripts/importer db rebuild --confirm import_agent_dev
.venv/Scripts/python -m pytest -m integration -v
```
Expected: all pass (the UNC test may report `skipped` if `\\localhost\C$` is not reachable from this account; that is acceptable).

- [ ] **Step 5: Commit**

```bash
git add tests/integration
git commit -m "Run M2a acceptance on addresses; add M2b DEV integration tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Documentation in the repo

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `src/importer/agent/harness.py` (docstring)

- [ ] **Step 1: Point the harness stub at the decision**

In `src/importer/agent/harness.py`, replace the docstring's first two paragraphs ("Agent harness — the slow path …" through "… never executed as live code.") with:
```python
"""Agent harness — reserved (design §1, §4, §5; M5).

M2b does NOT use this module: onboarding's model is the user's own Claude client
working through the importer's MCP server (mcp_server.py; Design Decisions §8.19).
This stub stays for a possible later unattended runner (Claude Agent SDK) that
would call the same onboarding functions. Its outputs would be recorded as data
(proposed signatures, versioned rules, decision-log entries), never live code.
```
Keep the remaining docstring paragraphs and the two stub functions unchanged.

- [ ] **Step 2: Update `CLAUDE.md`**

Make these changes:
- **Repo layout.** Add these lines in the `src/importer/` block:
  ```
    addresses.py, settings.py, environment.py   destination addresses; shared settings + DEV guard (in the DB)
    schema_sources.py        live / file schema import (per-destination connections)
    locators.py              read files from SMB / Azure Files / Blob locators
    proposals.py, onboarding.py   proposal review states; reuse/exact/fallback drafts; submit/accept/bind
    mcp_server.py            `importer mcp`: onboarding tools + `onboard` prompt for the user's Claude client
  ```
  Change the `agent/harness.py` line to `reserved (M5); M2b uses mcp_server.py instead`.
- **Running.** Replace the paragraph that starts "`database.env` (gitignored) holds…" with:
  ```
  `database.env` (gitignored) is the only local file: the DataEngine `importer` entry pointing at the control
  database (Windows `"trusted": "yes"` or Entra `UN` only — never a `PW`). Every other setting lives in the
  database (`importer config show|set|unset`). A database is marked once with `importer db init --environment
  DEV|PROD`; `importer db rebuild` needs that mark to be DEV **and** the database name to contain "dev".
  Onboarding: open Claude Code in the repo (`.mcp.json` registers `importer mcp`) and run
  `/mcp__importer__onboard`. Never auto-approve `accept_mapping` or `register_schema`.
  ```
- **Current status.** Replace the "Current status & next milestone" section's body with:
  ```
  **M2a and M2b are implemented.** M2b: destination-address namespace, shared settings + DEV guard in the
  database, schema import (live/file), shared file locators, confirm-and-bind onboarding, and the `importer mcp`
  server through which the user's own Claude client drafts mappings (no API key). Design Decisions §8.15–8.22;
  spec and plan in `docs/superpowers/`. Next: the milestone for complex nested files and plugins (§8.22), then
  M3+ per the Blueprint.
  ```

- [ ] **Step 3: Update `README.md`**

Make these changes:
- Replace every mention of `.env` / `IMPORTER_ENV` with the `database.env` + `importer db init` wording from Step 2.
- Replace the CLI command list with: `process`, `preview`, `retry`, `pending`, `config show|set|unset`, `schema import`, `mapper add`, `signature register`, `db init`, `db rebuild`, `mcp`.
- Add a section **"Onboarding unknown files with Claude"**:
  ```markdown
  ## Onboarding unknown files with Claude

  1. Unknown files are quarantined with a "looks like ..." proposal (`importer pending` lists them).
  2. Open Claude Code in this repo. `.mcp.json` starts `importer mcp`; approve the server once.
  3. Run `/mcp__importer__onboard`. Claude profiles each file, drafts a mapping (or uses the deterministic
     reuse/exact draft), previews it, and shows you a review table.
  4. Correct it in plain words, or approve. Approval is the `accept_mapping` prompt; it binds the signature and
     lands every waiting file. Never auto-approve `accept_mapping` or `register_schema`.

  Before the first run, register the destinations and their schemas:
  `importer config set destination NPVCS --server <server> --database NPVCS` (once per destination), then
  `importer schema import "NPVCS.*.*"` (or `--from-file cols.json` for one table).
  ```

- [ ] **Step 4: Run the suite**

Run: `.venv/Scripts/python -m pytest`
Expected: all pass. This is a docs-only change; the run confirms nothing broke.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md src/importer/agent/harness.py
git commit -m "Document M2b: settings in the database, schema import, Claude onboarding via MCP

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Live acceptance with the user (M2b cases 1–6)

This task is run **with the user**, in Claude Code. The executing agent prepares each step and the user performs the approvals. Record each outcome in the task notes; Task 15 copies them to Confluence.

**Files:**
- Create: `tests/fixtures/url_template_c.csv`

- [ ] **Step 1: Configure the real destinations (case 1, part 1)**

Ask the user for each destination's server, then run, once per destination:
```bash
.venv/Scripts/importer config set destination NPVCS --server <server> --database NPVCS
.venv/Scripts/importer config set destination JSON_IDEX --server <server> --database JSON_IDEX
.venv/Scripts/importer config set destination RX --server <server> --database RX
.venv/Scripts/importer config set destination NPIDATA_IMPORT --server <server> --database NPIDATA_IMPORT
.venv/Scripts/importer config set destination ZIPCODE --server <server> --database ZIPCODE
```
Expected: each command prints the settings document; `config show` lists all five.

- [ ] **Step 2: Pre-register the destination schemas (case 1, part 2)**

Run: `.venv/Scripts/importer schema import "NPVCS.*.*"` (then the same for the other four).
Expected: reports with `added` entries.

Show the user the `refused` list, which holds names outside `[A-Za-z0-9_]`, and record how many there are (spec §13 risk). If a destination is unreachable, use `--from-file` for the tables the acceptance cases need.

- [ ] **Step 3: Exact and reuse paths without a model (cases 2–3)**

These are proven by `tests/integration/test_m2b.py::test_onboarding_end_to_end_without_a_model` plus the unit tests. Also show them live through MCP, with the user:
1. Process `tests/fixtures/url_template_a.csv` under a new project (`--project ACC1`). Expected: quarantined.
2. In Claude Code, run `/mcp__importer__onboard`. Expected: Claude reports an `exact` draft, previews it, and asks.
3. The user approves at the `accept_mapping` prompt. Expected: the file lands.
4. Process a new-state copy of `formulary_dc.xml` (`--project ACC2 --type XML`, with an XML comment appended so it isn't a byte-identical re-delivery). Expected: a `reuse` draft; approve; it lands.

- [ ] **Step 4: The model path (case 4)**

1. Create `tests/fixtures/url_template_c.csv`: the URL variant **with an extra column**, so its signature differs from the registered B variant.
   ```
   URL ID,URL,URL Type,Notes
   C1,https://example.com/sbc/1,SBC,first
   C2,https://example.com/pay,PAY,
   ```
2. Hand-register the B variant as the "earlier mapping". Run:
   ```bash
   .venv/Scripts/importer mapper add spec_b.json --key urls_b
   .venv/Scripts/importer signature register tests/fixtures/url_template_b.csv --project VT --class PC --type CSV --mapper-version <id>
   ```
   `spec_b.json` is `tests.support.URL_SPEC_B` written to a temp file.
3. Process `url_template_c.csv` and a copy with one extra row, both under `--project VT`. Expected: both quarantined under one signature (`waiting_files: 2`).
4. In Claude Code, run `/mcp__importer__onboard`. Expected:
   - Claude calls `get_prior_mappings`, finds `url -> newurl`, and proposes it with that evidence;
   - `notes` goes to `_extras`;
   - the review table is shown.
5. The user approves at the `accept_mapping` prompt. Expected: both files `completed`.
6. Verify in SQL (the user runs it, or via `importer`):
   ```sql
   SELECT action_type, approval FROM dbo.decision_log WHERE action_type IN ('draft_mapping','bind_signature','mcp:accept_mapping') ORDER BY decision_id DESC;
   ```
   Expected: a `draft_mapping` row, and a `bind_signature` row with the user's Windows login.

- [ ] **Step 5: The first real file (case 5)**

The user picks a real file from a live feed and its shared locator: a UNC path or Blob URL.
1. Run `importer process "<locator>" --project … --class … --type … --source acceptance`. Expected: quarantined with a "looks like …" proposal.
2. In Claude Code, run `/mcp__importer__onboard`, review with the user, and approve. Expected: the file completes, and `percent_loaded` is reported.

Record the signature, the target(s), the `percent_loaded`, and anything the review surfaced.

- [ ] **Step 6: The DEV guard (case 6)**

This is proven by `tests/test_environment.py` (the truth table) and `tests/integration/test_m2b.py::test_identity_is_dev_and_unmarked_databases_are_refused`.

Show it live: `.venv/Scripts/importer db rebuild --confirm import_agent_dev` succeeds on DEV. Ask the user whether to also demonstrate a refusal against a non-DEV database. If they agree, temporarily point `database.env`'s `importer` entry at a database without `database_identity` (refusal happens before any change), run `importer db rebuild --confirm x`, and expect `refusing: database_identity is not set, not DEV …`. Restore `database.env` afterwards.

- [ ] **Step 7: Commit the fixture**

```bash
git add tests/fixtures/url_template_c.csv
git commit -m "Add the URL-template variant used by the live model-path acceptance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Confluence — results and refinements

**Files:** none in the repo.

- [ ] **Step 1: Design Decisions (page 3774676996)**

Fetch the page (HTML) and keep everything; changes are additive. Strike through superseded text per the page's rule.
1. Under §8.20, add a paragraph: **Result (<date>):** the acceptance outcomes recorded in Task 14, plus the unit and integration counts from the final runs.
2. Add a subsection **8.23 Refinements & execution findings (M2b)** listing refinements 1–5 from this plan's header, plus anything found during execution (for example the actual MCP SDK version and any API substitutions from Task 0).
3. Change the §8 heading to `8. M2 fast-path design (M2a and M2b IMPLEMENTED)`.

- [ ] **Step 2: Project page (3773956110) and Blueprint (3774775300)**

- **Project page:**
  - tick "New-schema onboarding flow with human sign-off gate", annotated `M2b: confirm-and-bind via MCP; sign-off = accept_mapping`;
  - add a dated status note: M2b done, with the acceptance summary;
  - next: the complex nested files & plugins milestone.
- **Blueprint, §C M2 row:** `State: M2a and M2b DONE`, with the date.

- [ ] **Step 3: Confirm the Schema DDL page matches `sql/001_schema.sql`**

Fetch page 3775135746 and compare its DDL block with the file. If they differ, update the page.

---

### Task 16: Hand-mapping manual

**Files:**
- Create: `docs/mapping-manual.md`

The manual is for a person completing a signature **without** Claude: writing a spec by hand, registering it, checking it, binding it and retrying the waiting files. The plugin chapter is deferred to the §8.22 milestone.

- [ ] **Step 1: Write the manual**

`docs/mapping-manual.md`. It includes the full text of `src/importer/mcp_reference.md`, copied in as chapter 3 (under the heading "3. Spec reference"), so both stay identical. Use this content:
````markdown
# Hand-mapping manual

How to complete an unknown file's signature by hand: write a mapping spec, register it, check it, bind it,
and land the waiting files. (With Claude, `/mcp__importer__onboard` does steps 2–5 for you; this manual is the
fallback and the reference for what Claude produces.)

## 1. Find what is waiting

    importer pending

Each entry shows the signature, a "looks like ..." candidate list, the suggested path (`reuse`, `exact`,
`fallback` or `model`), how many files are waiting, and the triggering file's locator.

- `reuse`: another project's identical template is registered. Use its mapper version as-is (step 4, skip 2–3).
- `exact` / `fallback`: an `"auto": true` spec onto the best schema is enough (exact), or is a lossless start
  (fallback: unmatched columns land in `_extras`).
- `model`: write the spec yourself (XML/JSON feeding several tables, or columns that need explicit mapping).

## 2. Write the spec

Start from the target schema's columns:

    importer config show                  # destinations
    importer schema import ... (if the target table is not registered yet)

Write `spec.json` using chapter 3. Rules: every `target` is a registered address (`database.schema.table`);
values land exactly as written; anything you cannot place goes to `_extras` (`"unmapped": "extras"`).

Example — the URL template variant whose link column is called `url`:

```json
{"kind": "tabular",
 "extractions": [{"target": "npvcs.relational.url_template", "records": "*", "auto": true,
                  "fields": {"newurl": "url", "file_id": "$file_id", "run_id": "$context.run_id"}}]}
```

## 3. Spec reference

(The full text of `src/importer/mcp_reference.md` goes here, verbatim, without its first heading line.)

## 4. Register and preview

    importer mapper add spec.json --key <name>
    importer preview "<locator of the waiting file>" --mapper-version <id> --project <P> --class <C> --type <T>

Check per-target row counts, the `_extras` keys (columns you did not map), and rejects. Repeat 2–4 until it looks
right; every `mapper add` creates a new version, nothing is edited in place.

## 5. Bind and land

    importer signature register "<locator of the waiting file>" --project <P> --class <C> --type <T> --mapper-version <id>
    importer retry <file_id>              # for each waiting file (see `importer pending` before binding)

`signature register` activates the signature under your Windows login. From then on, files with this signature
land automatically.

## 6. When something goes wrong

- `retry` says the file changed since discovery: deliver it as a new file.
- `retry` says the file is not reachable: you need read access to its share/container.
- A spec error names the problem (unknown target, invalid XPath, unknown shaper): fix the spec, `mapper add`
  again (new version), preview, then bind.
- Files with deeply nested structures that a spec cannot express stay on the legacy parsers until the
  complex-files milestone (Design Decisions §8.22).
````

Then replace the line `(The full text of src/importer/mcp_reference.md goes here, …)` with that file's content (every line after its first `# ` heading), so chapter 3 is complete in the committed manual.

- [ ] **Step 2: Check the manual's commands exist**

Run: `.venv/Scripts/importer --help && .venv/Scripts/importer pending --help && .venv/Scripts/importer signature register --help`
Expected: each command is listed and documented, with no "invalid choice" errors.

- [ ] **Step 3: Mirror on Confluence**

Create a child page **"Hand-mapping manual"** under the project page (3773956110) with the manual's content (markdown is acceptable), and add a link to it in the project page's Links section.

- [ ] **Step 4: Commit**

```bash
git add docs/mapping-manual.md
git commit -m "Add the hand-mapping manual

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review checklist (run after the last task)

- [ ] `.venv/Scripts/python -m pytest` passes, and the two invariant suites are untouched (`git diff main -- tests/test_metrics_monotonic.py tests/test_reclaim_idempotent.py src/importer/metrics.py src/importer/reclaim.py src/importer/rules/base.py` prints nothing).
- [ ] `.venv/Scripts/python -m pytest -m integration` passes on DEV.
- [ ] `git grep -n "IMPORTER_ENV\|importer_env\|information_schema_sql"` prints nothing.
- [ ] `git grep -n "claude_agent_sdk\|anthropic"` prints nothing under `src/`.
- [ ] Spec §2 "In M2b" items each map to a task: namespace (1, 3, 4), settings and safeguards (2, 5), schema import (6), onboarding (8–10), locators (7), MCP (11), CLI (5, 6, 10, 11), manual (16), DDL and docs (4, 5, 13, 15).
