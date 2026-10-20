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
from .tree import TreeMapper, parse_tree_spec

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


DECLARATIVE_KINDS = ("xml", "tabular", "tree")


def declarative_mapper(definition: dict, *, known_schemas: set[str] | None, version: int = 1) -> Mapper:
    """The mapper for a declarative definition (any kind but plugin); raises SpecError."""
    kind = definition.get("kind") if isinstance(definition, dict) else None
    if kind == "tree":
        return TreeMapper(parse_tree_spec(definition, known_schemas=known_schemas), version=version)
    if kind in ("xml", "tabular"):
        spec = parse_spec(definition, known_schemas=known_schemas, known_shapers=registered_shapers())
        return DeclarativeMapper(spec, version=version)
    raise SpecError(f"kind must be one of {DECLARATIVE_KINDS}, got {kind!r}")


def load_mapper(artifact: ArtifactRow, *, known_schemas: set[str], plugins_dir: Path = PLUGINS_DIR) -> Mapper:
    if artifact.artifact_type is not ArtifactType.MAPPER:
        raise MapperLoadError("not_a_mapper", f"artifact {artifact.version_id} is a {artifact.artifact_type.value}")
    definition = artifact.definition
    kind = definition.get("kind")
    if kind in DECLARATIVE_KINDS:
        try:
            return declarative_mapper(definition, known_schemas=known_schemas, version=artifact.version_no)
        except SpecError as exc:
            raise MapperLoadError("invalid_spec", str(exc)) from exc
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
