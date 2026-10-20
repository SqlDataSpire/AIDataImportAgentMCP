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
from ..readers.xml_reader import element_identity, local
from .base import Mapper, RunContext
from .values import XmlValues, put as _put

IDENT = re.compile(r"^[A-Za-z0-9_]+$")
TARGET = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,2}$")
PARENT = re.compile(r"^\$parent\(([A-Za-z0-9_.]+)\)$")
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
    if not isinstance(target, str) or not TARGET.match(target):
        raise SpecError(f"target must be a schema address (database.schema.table), got {target!r}")
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


class DeclarativeMapper(XmlValues, Mapper):
    """Runs a parsed Spec (tier 1, and tier 2 when the spec lists shapers)."""

    def __init__(self, spec: Spec, version: int = 1):
        self.spec = spec
        self.version = version
        self._compiled: dict[str, etree.XPath] = {}

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.target for e in self.spec.extractions))

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
