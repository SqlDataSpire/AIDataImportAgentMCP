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
