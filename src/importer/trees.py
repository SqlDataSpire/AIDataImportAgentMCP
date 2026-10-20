"""Nesting trees of XML documents (design §8.24).

A tree is a document's element structure: namespace-free paths with counts, repeat
ranges and attribute names. It never holds values (§8.23 #13), so it is metadata that
may cross MCP. One tree is stored per signature (in observed_schema) and is what a
"kind": "tree" spec annotates.
"""

from __future__ import annotations

import re
from collections import Counter

from lxml import etree

from .readers.base import XmlDocument

MAX_PATHS = 5000
MAX_DRIFT = 50
UNCHECKED = ""  # resolve(): the expression cannot be checked structurally

_NAME_STEP = re.compile(r"^(?:[A-Za-z_][\w.-]*:)?([A-Za-z_][\w.-]*)$")
_ATTR_STEP = re.compile(r"^@(?:[A-Za-z_][\w.-]*:)?([A-Za-z_][\w.-]*)$")


class _Stats:
    __slots__ = ("name", "ns", "count", "parents", "min", "max", "attributes", "children")

    def __init__(self, name: str, ns: str | None):
        self.name = name
        self.ns = ns
        self.count = 0
        self.parents = 0  # parent occurrences holding at least one of this element
        self.min: int | None = None
        self.max = 0
        self.attributes: dict[str, None] = {}
        self.children: dict[str, None] = {}  # child paths, first-seen order


def build_tree(doc: XmlDocument) -> dict:
    stats: dict[str, _Stats] = {}
    namespaces: Counter = Counter()
    truncated = False

    def visit(el, path: str) -> bool:
        nonlocal truncated
        qname = etree.QName(el)
        st = stats.get(path)
        if st is None:
            if len(stats) >= MAX_PATHS:
                truncated = True
                return False
            st = stats[path] = _Stats(qname.localname, qname.namespace)
        st.count += 1
        if qname.namespace:
            namespaces[qname.namespace] += 1
        for name in el.attrib:
            st.attributes.setdefault(etree.QName(name).localname, None)
        per: Counter = Counter()
        for child in el:
            if not isinstance(child.tag, str):
                continue
            child_path = f"{path}/{etree.QName(child).localname}"
            if visit(child, child_path):
                per[child_path] += 1
                st.children.setdefault(child_path, None)
        for child_path, n in per.items():
            c = stats[child_path]
            c.parents += 1
            c.min = n if c.min is None else min(c.min, n)
            c.max = max(c.max, n)
        return True

    root_path = "/" + doc.local_name
    visit(doc.root, root_path)
    vias: set[str] = set()

    def wrapper(child: _Stats, parent: _Stats) -> bool:
        return (not child.children and child.max == 1 and child.count == parent.count
                and child.parents == parent.count)

    def render(path: str, parent_count: int) -> dict:
        st = stats[path]
        node: dict = {"name": st.name, "path": path, "count": st.count}
        if st.ns != doc.namespace:
            node["ns"] = st.ns
        if st.max > 1:
            low = 0 if st.parents < parent_count else (st.min or 0)
            node["repeats"] = {"min": low, "max": st.max}
        if st.attributes:
            node["attributes"] = list(st.attributes)
        kids = list(st.children)
        if not kids:
            node["leaf"] = True
        elif len(kids) == 1 and wrapper(stats[kids[0]], st):
            node["leaf"] = True
            node["via"] = stats[kids[0]].name
            vias.add(node["via"])
        else:
            node["children"] = [render(k, st.count) for k in kids]
        return node

    rendered = render(root_path, 1)
    return {
        "root": doc.local_name, "namespace": doc.namespace, "namespaces": dict(namespaces),
        "value_child": next(iter(vias)) if len(vias) == 1 else None,
        "nodes": len(stats), "truncated": truncated, "tree": rendered,
    }


def _walk(node: dict):
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


def _index(tree: dict) -> dict[str, dict]:
    return {node["path"]: node for node in _walk(tree["tree"])}


def tree_paths(tree: dict) -> set[str]:
    out: set[str] = set()
    for node in _walk(tree["tree"]):
        out.add(node["path"])
        if "via" in node:
            out.add(f"{node['path']}/{node['via']}")
    return out


def subtree(tree: dict, path: str | None = None, depth: int = 4) -> dict:
    if depth < 1:
        raise ValueError("depth must be at least 1")
    node = tree["tree"] if path is None else _index(tree).get(path.rstrip("/"))
    if node is None:
        raise KeyError(f"no element path {path!r} in the tree")

    def cut(n: dict, d: int) -> dict:
        out = {k: v for k, v in n.items() if k != "children"}
        kids = n.get("children")
        if kids:
            if d <= 1:
                out["children_omitted"] = len(kids)
            else:
                out["children"] = [cut(c, d - 1) for c in kids]
        return out

    return {**{k: v for k, v in tree.items() if k != "tree"}, "tree": cut(node, depth)}


def drift(stored: dict, current: dict) -> dict | None:
    old, new = tree_paths(stored), tree_paths(current)
    added, gone = sorted(new - old), sorted(old - new)
    if not added and not gone:
        return None
    return {"new": added[:MAX_DRIFT], "missing": gone[:MAX_DRIFT], "new_count": len(added), "missing_count": len(gone)}


def _namespace_of(tree: dict, index: dict[str, dict], path: str) -> str | None:
    """The namespace of the element at ``path`` (a folded value wrapper shares its parent's)."""
    node = index.get(path) or index.get(path.rsplit("/", 1)[0]) or {}
    return node.get("ns", tree.get("namespace"))


def resolve(tree: dict, context: str, expr: str, namespaces: dict | None = None) -> str | None:
    """The element (or ``.../@attr``) path ``expr`` reaches from ``context``; UNCHECKED when the
    expression is not plain child / parent / attribute steps; None when it matches nothing.
    With ``namespaces`` (the spec's prefix map) a name step must also be in its element's namespace:
    an unprefixed step only matches un-namespaced elements, as in XPath 1.0."""
    if not context or not expr or "//" in expr:
        return UNCHECKED
    index = _index(tree)
    paths = tree_paths(tree)
    steps = expr.split("/")
    current = context
    if expr.startswith("/"):
        current = ""
        steps = steps[1:]
    for i, step in enumerate(steps):
        if step in (".", ""):
            continue
        if step == "..":
            current = current.rsplit("/", 1)[0]
            continue
        attr = _ATTR_STEP.match(step)
        if attr and i == len(steps) - 1:
            node = index.get(current)
            if node is None:
                return UNCHECKED if tree.get("truncated") else None
            return f"{current}/@{attr.group(1)}" if attr.group(1) in node.get("attributes", []) else None
        name = _NAME_STEP.match(step)
        if not name:
            return UNCHECKED
        current = f"{current}/{name.group(1)}"
        if current not in paths:
            return UNCHECKED if tree.get("truncated") else None
        if namespaces is not None:
            prefix = step.split(":", 1)[0] if ":" in step else None
            if (namespaces.get(prefix) if prefix else None) != _namespace_of(tree, index, current):
                return None
    return current


def tree_warnings(tree: dict, definition: dict) -> list[str]:
    """Spec paths that cannot occur in the stored tree (structural check; warnings, not errors)."""
    out: list[str] = []
    namespaces = definition.get("namespaces") or {}
    if not namespaces and tree.get("namespaces"):
        uri = tree.get("namespace") or next(iter(tree["namespaces"]))
        out.append(f"the document's elements are in namespace(s) {', '.join(tree['namespaces'])}; declare them in "
                   f"\"namespaces\" (e.g. {{\"x\": \"{uri}\"}}) and prefix every step, or no path will match")
        return out
    index = _index(tree)

    def nothing(context: str, expr: str) -> str:
        hit = resolve(tree, context, expr)
        if not hit:
            return f"matches nothing under {context}"
        # The path exists; only a step's namespace is wrong.
        uri = _namespace_of(tree, index, hit.split("/@", 1)[0])
        fix = f"namespace {uri}; use a prefix bound to it" if uri else "no namespace; leave its step unprefixed"
        return f"matches nothing under {context}: the element is in {fix}"

    def walk(node: dict, context: str, label: str) -> None:
        for column in node.get("column", []):
            path = column.get("path", "")
            if context and not path.startswith("$") and resolve(tree, context, path, namespaces) is None:
                out.append(f"{label}: column {column.get('name')!r} path {path!r} {nothing(context, path)}")
        for child in node.get("select", []):
            for_each = child.get("forEach", "")
            name = child.get("table") or for_each
            target = resolve(tree, context, for_each, namespaces) if context else UNCHECKED
            if target is None:
                out.append(f"{name}: forEach {for_each!r} {nothing(context, for_each)}")
                continue
            walk(child, target, name)

    walk(definition, "/" + tree["root"], "root")
    return out
