"""The "tree" spec kind (design §8.24): an annotated nesting tree that one generic walker runs.

The vocabulary follows SQL on FHIR v2 ViewDefinition (forEach, column {name, path},
nested select), but sibling selects never form a cross product: every table node is its
own output and is walked independently, so row counts are bounded by element counts.
A node's columns are carried down to every table node beneath it. XML only (M2c-1).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from lxml import etree

from ..models import LandRow, RejectRow
from ..readers.base import XmlDocument, normalize_name
from ..readers.xml_reader import MAX_IDENTITY, local, ordinal_identity
from .base import Mapper, RunContext
from .spec import CONTEXT, IDENT, TARGET, SpecError, Unpivot
from .values import XmlValues, put

ROOT_KEYS = {"kind", "namespaces", "value_child", "table", "column", "leaves", "unmapped", "unpivot", "document", "select"}
NODE_KEYS = {"forEach", "table", "column", "leaves", "unmapped", "unpivot", "document", "select"}
TABLE_ONLY = ("leaves", "unmapped", "unpivot", "document")
SPECIALS = ("$file_id", "$identity", "$row_identity", "$name")


@dataclass(frozen=True)
class Column:
    name: str
    path: str


@dataclass(frozen=True)
class Node:
    for_each: str | None
    table: str | None
    columns: tuple[Column, ...]
    leaves: str | None = None
    unmapped: str = "extras"
    unpivot: Unpivot | None = None
    document: str | None = None
    select: tuple["Node", ...] = ()


@dataclass(frozen=True)
class TreeSpec:
    root: Node
    namespaces: dict
    value_child: str | None


def _has_table(node: Node) -> bool:
    return node.table is not None or any(_has_table(child) for child in node.select)


def parse_tree_spec(defn: dict, *, known_schemas: set[str] | None = None) -> TreeSpec:
    if not isinstance(defn, dict) or defn.get("kind") != "tree":
        raise SpecError('a tree spec is a JSON object with "kind": "tree"')
    namespaces = defn.get("namespaces", {})
    if not isinstance(namespaces, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in namespaces.items()):
        raise SpecError("namespaces must map prefix -> URI strings")
    value_child = defn.get("value_child")
    if value_child is not None and not isinstance(value_child, str):
        raise SpecError("value_child must be a string")
    root = _node(defn, ROOT_KEYS, known_schemas, root=True, where="root")
    if not _has_table(root):
        raise SpecError("a tree spec needs at least one table node")
    spec = TreeSpec(root, dict(namespaces), value_child)
    _compile_check(spec)
    return spec


def _node(raw: object, allowed: set[str], known_schemas: set[str] | None, *, root: bool, where: str) -> Node:
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: each node must be an object")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SpecError(f"{where}: unknown keys {unknown}")
    for_each = raw.get("forEach")
    if not root and (not isinstance(for_each, str) or not for_each):
        raise SpecError(f"{where}: forEach must be a non-empty XPath string")
    label = for_each or "root"
    table = raw.get("table")
    if table is not None:
        if not isinstance(table, str) or not TARGET.match(table):
            raise SpecError(f"{label}: table must be a schema address (database.schema.table), got {table!r}")
        if known_schemas is not None and table not in known_schemas:
            raise SpecError(f"{label}: table {table!r} has no active schema definition")
    else:
        present = [k for k in TABLE_ONLY if k in raw]
        if present:
            raise SpecError(f"{label}: {', '.join(present)} only apply to a node with a table")
    columns = _columns(raw.get("column", []), label)
    leaves = raw.get("leaves")
    if leaves not in (None, "auto"):
        raise SpecError(f'{label}: leaves must be "auto"')
    unmapped = raw.get("unmapped", "extras")
    if unmapped not in ("extras", "ignore"):
        raise SpecError(f"{label}: unmapped must be 'extras' or 'ignore'")
    unpivot = _unpivot(raw["unpivot"], label) if "unpivot" in raw else None
    document = raw.get("document")
    if document is not None and (not isinstance(document, str) or not IDENT.match(document)):
        raise SpecError(f"{label}: document must be a column name matching [A-Za-z0-9_]+")
    select_raw = raw.get("select", [])
    if not isinstance(select_raw, list):
        raise SpecError(f"{label}: select must be a list")
    select = tuple(_node(s, NODE_KEYS, known_schemas, root=False, where=f"{label} > select[{i}]")
                   for i, s in enumerate(select_raw))
    node = Node(for_each, table, columns, leaves, unmapped, unpivot, document, select)
    if not root and not _has_table(node):
        raise SpecError(f"{label}: no table node at or below this node (dead branch)")
    return node


def _columns(raw: object, label: str) -> tuple[Column, ...]:
    if not isinstance(raw, list):
        raise SpecError(f"{label}: column must be a list of {{name, path}}")
    out: list[Column] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"name", "path"}:
            raise SpecError(f"{label}: each column needs exactly name and path")
        name, path = item["name"], item["path"]
        if not isinstance(name, str) or not IDENT.match(name):
            raise SpecError(f"{label}: column name {name!r} must match [A-Za-z0-9_]+")
        if name in seen:
            raise SpecError(f"{label}: column {name!r} is defined twice")
        seen.add(name)
        if not isinstance(path, str) or not path:
            raise SpecError(f"{label}.{name}: path must be a non-empty string")
        if path.startswith("$") and path not in SPECIALS and not CONTEXT.match(path):
            raise SpecError(f"{label}.{name}: unknown special source {path!r}")
        out.append(Column(name, path))
    return tuple(out)


def _unpivot(u: object, label: str) -> Unpivot:
    if not isinstance(u, dict) or set(u) != {"over", "name_column", "value_column"}:
        raise SpecError(f"{label}: unpivot needs exactly over, name_column, value_column")
    if not isinstance(u["over"], str) or not u["over"]:
        raise SpecError(f"{label}: unpivot.over must be an XPath string")
    for key in ("name_column", "value_column"):
        if not isinstance(u[key], str) or not IDENT.match(u[key]):
            raise SpecError(f"{label}: unpivot.{key} must match [A-Za-z0-9_]+")
    return Unpivot(u["over"], u["name_column"], u["value_column"])


def _compile_check(spec: TreeSpec) -> None:
    exprs: list[str] = [spec.value_child] if spec.value_child else []

    def collect(node: Node) -> None:
        if node.for_each:
            exprs.append(node.for_each)
        exprs.extend(c.path for c in node.columns if not c.path.startswith("$"))
        if node.unpivot:
            exprs.append(node.unpivot.over)
        for child in node.select:
            collect(child)

    collect(spec.root)
    probe = etree.Element("probe")
    for expr in exprs:
        try:
            etree.XPath(expr, namespaces=spec.namespaces)(probe)  # evaluating surfaces undeclared prefixes
        except etree.XPathError as exc:
            raise SpecError(f"invalid XPath {expr!r}: {exc}") from exc


def tables_in(definition: dict) -> list[str]:
    """Every table address in a tree definition (unparsed dict), depth-first, without repeats."""
    out: list[str] = []

    def walk(node: dict) -> None:
        if isinstance(node.get("table"), str):
            out.append(node["table"])
        for child in node.get("select", []) or []:
            if isinstance(child, dict):
                walk(child)

    if isinstance(definition, dict):
        walk(definition)
    return list(dict.fromkeys(out))


def outline(definition: dict) -> list[dict]:
    """Per table node: its forEach chain and the columns it carries or defines (for the review table)."""
    out: list[dict] = []

    def walk(node: dict, chain: list[str], carried: list[str]) -> None:
        here = chain + ([node["forEach"]] if node.get("forEach") else [])
        names = carried + [c["name"] for c in node.get("column", []) if c.get("name") not in carried]
        if node.get("table"):
            out.append({"table": node["table"], "path": " / ".join(here) or "(root)", "carries": names})
        for child in node.get("select", []) or []:
            walk(child, here, names)

    walk(definition, [], [])
    return out


class TreeMapper(XmlValues, Mapper):
    """Runs a TreeSpec over an XML document: depth-first, carrying columns down (§8.24)."""

    def __init__(self, spec: TreeSpec, version: int = 1):
        self.spec = spec
        self.version = version
        self._compiled: dict[str, etree.XPath] = {}

    @property
    def targets(self) -> tuple[str, ...]:
        out: list[str] = []

        def walk(node: Node) -> None:
            if node.table:
                out.append(node.table)
            for child in node.select:
                walk(child)

        walk(self.spec.root)
        return tuple(dict.fromkeys(out))

    def extract(self, doc, ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        if not isinstance(doc, XmlDocument):
            raise SpecError("a tree spec cannot map a tabular document")
        self._paths: dict = {doc.root: ""}
        self._steps: dict = {}
        self._counted: set = set()
        try:
            yield from self._walk(self.spec.root, doc.root, doc.root, {}, {}, ctx)
        finally:
            self._paths, self._steps, self._counted = {}, {}, set()

    def _identity(self, el, root) -> str:
        """element_identity(el, root), byte for byte, with sibling positions counted once per parent."""
        if el is root:
            return "/"
        path = self._path(el)
        return path if len(path) <= MAX_IDENTITY else ordinal_identity(el, root)

    def _path(self, el) -> str:
        path = self._paths.get(el)
        if path is None:
            parent = el.getparent()
            if parent not in self._counted:
                totals: dict = {}
                for child in parent:
                    if isinstance(child.tag, str):
                        totals[child.tag] = totals.get(child.tag, 0) + 1
                seen: dict = {}
                for child in parent:
                    if isinstance(child.tag, str):
                        seen[child.tag] = seen.get(child.tag, 0) + 1
                        name = local(child)
                        self._steps[child] = name if totals[child.tag] == 1 else f"{name}[{seen[child.tag]}]"
                self._counted.add(parent)
            path = self._paths[el] = f"{self._path(parent)}/{self._steps[el]}"
        return path

    def _reject(self, node: Node, el, identity: str, exc: Exception) -> RejectRow:
        return RejectRow(node.table, identity, etree.tostring(el, encoding="unicode", with_tail=False),
                         "mapping_error", f"{type(exc).__name__}: {exc}")

    def _walk(self, node: Node, el, root, carried: dict, carried_multi: dict, ctx: RunContext):
        identity = self._identity(el, root)
        used: set = set()
        try:
            scope, multi = dict(carried), dict(carried_multi)
            for column in node.columns:
                multi.pop(column.name, None)
                if column.path.startswith("$"):
                    scope[column.name] = self._special(column.path, el, identity, ctx)
                    continue
                values = self._values(self._xpath(column.path)(el), used)
                scope[column.name] = values[0] if values else None
                if len(values) > 1:
                    multi[column.name] = values
        except SpecError:
            raise
        except Exception as exc:  # this element's subtree cannot be built; record it and move on
            yield self._reject(node, el, identity, exc)
            return
        if node.table:
            try:
                rows = list(self._rows(node, el, identity, scope, multi, used, ctx))
            except SpecError:
                raise
            except Exception as exc:
                rows = [self._reject(node, el, identity, exc)]
            yield from rows
        for child in node.select:
            for match in self._xpath(child.for_each)(el):
                if isinstance(match, etree._Element) and isinstance(match.tag, str):
                    yield from self._walk(child, match, root, scope, multi, ctx)

    def _special(self, src: str, el, identity: str, ctx: RunContext) -> str | None:
        if src == "$file_id":
            return str(ctx.file_id)
        if src in ("$identity", "$row_identity"):
            return identity
        if src == "$name":
            return local(el)
        match = CONTEXT.match(src)
        if match:
            value = ctx.request_context.get(match.group(1))
            return None if value is None else str(value)
        raise SpecError(f"unsupported source {src!r}")

    def _rows(self, node: Node, el, identity: str, scope: dict, multi: dict, used: set,
              ctx: RunContext) -> Iterator[LandRow]:
        schema = ctx.schemas.get(node.table, [])
        schema_set = set(schema)
        fields: dict = {}
        extras: dict = {}
        for name, value in scope.items():
            put(fields, extras, schema_set, name, value)
        for name, values in multi.items():
            extras[name] = values
        if node.document:
            put(fields, extras, schema_set, node.document, etree.tostring(el, encoding="unicode", with_tail=False))
        if node.unpivot:
            u = node.unpivot
            seen: dict[str, int] = {}
            for child in self._xpath(u.over)(el):
                if not isinstance(child, etree._Element) or not isinstance(child.tag, str):
                    continue
                name = local(child)
                seen[name] = seen.get(name, 0) + 1
                suffix = name if seen[name] == 1 else f"{name}[{seen[name]}]"
                row_fields, row_extras = dict(fields), dict(extras)
                put(row_fields, row_extras, schema_set, u.name_column, name)
                put(row_fields, row_extras, schema_set, u.value_column, self._value(child))
                yield LandRow(node.table, f"{identity}#{suffix}", row_fields, row_extras or None)
            return
        by_norm = {normalize_name(c): c for c in schema} if node.leaves == "auto" else {}
        leaves: dict[str, list[str]] = {}
        for child in el:
            if isinstance(child.tag, str) and child not in used and self._is_value_element(child):
                leaves.setdefault(local(child), []).append(self._value(child))
        for name, values in leaves.items():
            column = by_norm.get(normalize_name(name))
            if column is not None and column not in fields:
                fields[column] = values[0]  # as a multi-match: the first value lands, the full list goes to _extras
                if len(values) > 1:
                    extras[name if name not in extras else f"./{name}"] = values
            elif node.unmapped == "extras":
                # Lossless: repeats become a list; a name already taken (a carried column, or a leaf displaced by an
                # explicit column) is kept under the relative key "./<name>", which no column name can be.
                extras[name if name not in extras else f"./{name}"] = values[0] if len(values) == 1 else values
        yield LandRow(node.table, identity, fields, extras or None)
