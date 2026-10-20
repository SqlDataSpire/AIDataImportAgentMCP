# M2c-1 Nesting Trees for XML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nested XML template is onboarded like a flat file. The importer profiles its nesting tree (names and
counts, no values). The agent writes a `"kind": "tree"` spec that mirrors the tree. One generic walker lands every
record node as its own raw table.

**Architecture:**
- **`trees.py`** builds, pages, compares and checks trees. These are pure functions over an `XmlDocument` and plain
  dicts.
- **`mappers/tree.py`** parses tree specs and runs `TreeMapper`, a depth-first walker that carries ancestor columns
  down. Sibling selects never combine. It shares the XML value rules with the existing `xml` kind through a mixin
  in `mappers/values.py`.
- **The tree is stored 1:1 with the signature** in `signature_registry.observed_schema`. The pipeline backfills or
  drift-checks it on every XML hit, and onboarding/MCP expose it.

**Tech Stack:** Python 3.14, lxml, pytest (+ hypothesis already present), official `mcp` 2.3 SDK, SQL Server via SQLAlchemy Core (integration only).

**Spec:** `docs/superpowers/specs/2026-10-08-m2c1-nesting-trees-design.md` (read it with this plan).

## Global Constraints

- **The data boundary is unchanged.** The tree holds names, counts, repeat ranges and attribute names, and nothing
  derived from values. Samples and preview rows stay masked: `masking.py`, 3 rows × 20 fields × 40 characters.
- **Fixed caps, not settings:** at most `MAX_PATHS = 5000` distinct paths per tree, and `MAX_DRIFT = 50` paths per
  list in a `tree_drift` row. `get_file_profile` shows the tree to depth 4.
- **No importer joins:** sibling `select` nodes are walked independently. Row counts are bounded by element counts.
- **Values land exactly as written:**
  - a path matching nothing gives NULL;
  - a path matching several nodes lands the first value, with the full list in `_extras`;
  - columns the schema lacks go to `_extras`.
- **Drift never fails or holds a file.** It only writes a `tree_drift` row to `decision_log`.
- **Existing behaviour stays green:** invariants #1 and #2, the `xml` and `tabular` kinds, and `tests/test_spec_xml.py`
  unchanged.
- **Destinations are never written.** Integration tests write only to the DEV control database.
- **Commands run from the repo root on Windows:** `.venv/Scripts/python -m pytest ...`
- **Commits end with:**
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **A namespaced document whose spec declares no `namespaces`.** Every `forEach` matches nothing, so the preview
   shows zero rows. `tree_warnings` must say the document is namespaced and needs a prefix. (Task 9)
2. **A `forEach` that selects attributes or text** (e.g. `x:a/@id`). The walker must skip non-elements: no rows, no
   crash. (Task 4)
3. **A truncated tree (over 5,000 paths).** Paths beyond the cap must be reported as unchecked, not as "matches
   nothing". (Task 2)
4. **A tree-spec table with zero matching elements.** The preview must list it with count 0, so the agent sees it.
   (Task 9)
5. **A table node at the root.** One row per file, with identity `/`. (Task 4)

---

## File Structure

| File | Responsibility |
|---|---|
| `src/importer/mappers/values.py` *(new)* | `put()` and the `XmlValues` mixin: XPath cache and value rules shared by the `xml` and `tree` kinds |
| `src/importer/mappers/spec.py` *(modify)* | `DeclarativeMapper` uses the mixin; behaviour unchanged |
| `src/importer/trees.py` *(new)* | `build_tree`, `tree_paths`, `subtree`, `drift`, `resolve`, `tree_warnings` |
| `src/importer/mappers/tree.py` *(new)* | `parse_tree_spec`, `TreeSpec`/`Node`/`Column`, `TreeMapper`, `tables_in`, `outline` |
| `src/importer/mappers/loading.py` *(modify)* | `declarative_mapper()` dispatch; `load_mapper` accepts `tree` |
| `src/importer/fingerprint.py` *(modify)* | XML `observed` carries the tree |
| `src/importer/profiler.py` *(modify)* | XML profile = tree; `record` candidates |
| `src/importer/repositories.py`, `memory_store.py`, `mssql_store.py` *(modify)* | `SignatureRegistry.set_observed` |
| `src/importer/pipeline.py` *(modify)* | Drift check and tree backfill on XML hits |
| `src/importer/onboarding.py` *(modify)* | Tree targets, depth-limited profile, `tree()`, `tree_warnings`, zero counts, review outline |
| `src/importer/mcp_server.py`, `mcp_reference.md`, `.claude/settings.json` *(modify)* | `get_tree` tool, prompt, reference, allow-list |
| `tests/fixtures/formulary_tree_spec.json`, `benefits_sample.xml`, `benefits_tree_spec.json`, `benefits_schemas.json` *(new)* | Synthetic fixtures |
| `tests/test_values_refactor.py`, `test_trees.py`, `test_tree_spec.py`, `test_tree_mapper.py`, `test_tree_pipeline.py`, `tests/integration/test_m2c1.py` *(new)* | Tests |
| `sql/001_schema.sql`, `CLAUDE.md`, `docs/mapping-manual.md`, `docs/architecture/README.md` *(modify)* | Comments and docs |

---

### Task 1: Branch, and shared XML value rules (`mappers/values.py`)

**Files:**
- Create: `src/importer/mappers/values.py`
- Modify: `src/importer/mappers/spec.py` (remove `_put` and the four value methods; inherit the mixin)
- Test: `tests/test_values_refactor.py`

**Interfaces:**
- Produces:
  - `put(fields: dict, extras: dict, schema_set: set[str], column: str, value: object) -> None`
  - `class XmlValues`, with methods `_xpath(expr) -> etree.XPath`, `_values(result, used: set) -> list[str]`,
    `_value(el) -> str` and `_is_value_element(el) -> bool`. It requires `self.spec.namespaces`,
    `self.spec.value_child` and `self._compiled: dict`.

- [ ] **Step 0: Create the branch**

```bash
git checkout -b m2c1
```

- [ ] **Step 1: Write the failing test**

`tests/test_values_refactor.py`:

```python
from __future__ import annotations

from importer.mappers.spec import DeclarativeMapper
from importer.mappers.values import XmlValues, put


def test_put_routes_unknown_columns_to_extras():
    fields, extras = {}, {}
    put(fields, extras, {"a"}, "a", "1")
    put(fields, extras, {"a"}, "b", "2")
    assert fields == {"a": "1"} and extras == {"b": "2"}


def test_declarative_mapper_uses_the_shared_value_rules():
    assert issubclass(DeclarativeMapper, XmlValues)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_values_refactor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.mappers.values'`

- [ ] **Step 3: Create `src/importer/mappers/values.py`**

```python
"""XML value rules shared by the xml and tree spec kinds (design §8.9, §8.24).

Values land exactly as written. An element's value is read through the spec's
value_child (e.g. the CMS <cellValue>) when present, otherwise from its text.
"""

from __future__ import annotations

from lxml import etree


def put(fields: dict, extras: dict, schema_set: set[str], column: str, value: object) -> None:
    (fields if column in schema_set else extras)[column] = value


class XmlValues:
    """Mixin. The host sets ``self.spec`` (with ``namespaces`` and ``value_child``) and ``self._compiled = {}``."""

    def _xpath(self, expr: str) -> etree.XPath:
        if expr not in self._compiled:
            self._compiled[expr] = etree.XPath(expr, namespaces=self.spec.namespaces)
        return self._compiled[expr]

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

- [ ] **Step 4: Point `spec.py` at the mixin**

In `src/importer/mappers/spec.py`:
- Add `from .values import XmlValues, put as _put`, after `from .base import Mapper, RunContext`.
- Delete the module-level `def _put(...)` (lines 177-178).
- Change `class DeclarativeMapper(Mapper):` to `class DeclarativeMapper(XmlValues, Mapper):`.
- Delete its methods `_xpath`, `_values`, `_value` and `_is_value_element` (lines 193-196 and 359-385).

Keep `self._compiled = {}` in `__init__`. Every other line stays. `tests/test_spec_xml.py` monkeypatches
`DeclarativeMapper._value`; that still works, because the subclass attribute overrides the mixin's.

- [ ] **Step 5: Run the new test and the XML spec suite**

Run: `.venv/Scripts/python -m pytest tests/test_values_refactor.py tests/test_spec_xml.py tests/test_spec_tabular.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/importer/mappers/values.py src/importer/mappers/spec.py tests/test_values_refactor.py
git commit -m "Share XML value rules in mappers/values.py (no behaviour change)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The nesting tree (`trees.py`)

**Files:**
- Create: `src/importer/trees.py`
- Test: `tests/test_trees.py`

**Interfaces:**
- Consumes: `XmlDocument` (`readers/base.py`); `parse_document` (tests).
- Produces:
  - `MAX_PATHS = 5000`, `MAX_DRIFT = 50`, `UNCHECKED = ""`
  - `build_tree(doc: XmlDocument) -> dict`, returning keys `root`, `namespace`, `namespaces`, `value_child`,
    `nodes`, `truncated` and `tree`.
  - A node is a dict with:
    - always: `name`, `path`, `count`;
    - optional: `ns`, `repeats{min,max}`, `attributes[]`;
    - either `leaf: True` (plus optional `via`) or `children[]`;
    - and, after `subtree`, possibly `children_omitted: int`.
  - `tree_paths(tree: dict) -> set[str]`
  - `subtree(tree: dict, path: str | None = None, depth: int = 4) -> dict`, which raises `KeyError` for an unknown
    path.
  - `drift(stored: dict, current: dict) -> dict | None`, returning `{"new", "missing", "new_count",
    "missing_count"}` or `None`.
  - `resolve(tree: dict, context: str, expr: str) -> str | None`: the resolved path; `UNCHECKED` when it cannot be
    checked structurally; `None` when the expression matches nothing.
  - `tree_warnings(tree: dict, definition: dict) -> list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_trees.py`:

```python
from __future__ import annotations

import pytest

from importer import trees
from importer.readers import parse_document
from importer.trees import UNCHECKED, build_tree, drift, resolve, subtree, tree_paths, tree_warnings
from tests.support import fixture, load_json

NS = "http://vo.ffe.cms.hhs.gov"
ROOT = "/prescriptionDrugTemplateVO"


def _doc(xml):
    return parse_document(xml.encode() if isinstance(xml, str) else xml, "XML")


def _formulary() -> dict:
    return build_tree(_doc(fixture("formulary_dc.xml").read_bytes()))


def _node(tree: dict, path: str) -> dict:
    stack = [tree["tree"]]
    while stack:
        node = stack.pop()
        if node["path"] == path:
            return node
        stack.extend(node.get("children", []))
    raise AssertionError(f"no node {path}")


def test_formulary_tree_shape():
    doc = _doc(fixture("formulary_dc.xml").read_bytes())
    tree = build_tree(doc)
    assert tree["root"] == "prescriptionDrugTemplateVO" and tree["namespace"] == NS
    assert tree["namespaces"] == {NS: sum(1 for el in doc.root.iter() if isinstance(el.tag, str))}
    assert tree["value_child"] == "cellValue" and tree["truncated"] is False
    drug = _node(tree, f"{ROOT}/drugList/drugListVO/drugVOList/drugVO")
    assert drug["count"] == 3 and drug["repeats"] == {"min": 3, "max": 3}
    cost = _node(tree, f"{ROOT}/formularyList/formularyVO/formularyCostSharingTypeList/formularyCostSharingTypeVO"
                       "/costSharingTypeList/costSharingTypeVO")
    assert cost["count"] == 5 and cost["repeats"] == {"min": 1, "max": 2}


def test_value_wrappers_are_folded_and_attributes_are_names_only():
    tree = _formulary()
    issuer = _node(tree, f"{ROOT}/header/issuerId")
    assert issuer == {"name": "issuerId", "path": f"{ROOT}/header/issuerId", "count": 1,
                      "attributes": ["cellLocation"], "leaf": True, "via": "cellValue"}
    assert _node(tree, f"{ROOT}/header/templateVersion")["leaf"] is True
    assert "via" not in _node(tree, f"{ROOT}/header/templateVersion")
    assert "12345" not in repr(tree) and "B2" not in repr(tree)  # never a value


def test_optional_child_has_no_repeats_and_paths_include_wrappers():
    tree = _formulary()
    quantity = _node(tree, f"{ROOT}/drugList/drugListVO/drugVOList/drugVO/quantityLimit")
    assert quantity["count"] == 1 and "repeats" not in quantity
    paths = tree_paths(tree)
    assert f"{ROOT}/header/issuerId/cellValue" in paths and f"{ROOT}/header" in paths


def test_mixed_namespaces_mark_the_odd_node():
    tree = build_tree(_doc('<r xmlns="urn:a"><b xmlns="urn:b"><c>1</c></b><b xmlns="urn:b"><c>2</c></b></r>'))
    b = _node(tree, "/r/b")
    assert b["ns"] == "urn:b" and b["repeats"] == {"min": 2, "max": 2}
    assert "ns" not in tree["tree"]


def test_repeat_min_counts_parents_without_the_child():
    tree = build_tree(_doc("<r><p><x/><x/></p><p/></r>"))
    assert _node(tree, "/r/p/x")["repeats"] == {"min": 0, "max": 2}


def test_cap_truncates(monkeypatch):
    monkeypatch.setattr(trees, "MAX_PATHS", 3)
    tree = build_tree(_doc("<r><a/><b/><c/><d/></r>"))
    assert tree["truncated"] is True and tree["nodes"] == 3


def test_subtree_pages_by_depth():
    tree = _formulary()
    top = subtree(tree, depth=2)
    assert [c["name"] for c in top["tree"]["children"]] == ["header", "formularyList", "drugList"]
    assert top["tree"]["children"][1]["children_omitted"] == 1
    branch = subtree(tree, f"{ROOT}/drugList", depth=1)
    assert branch["tree"]["path"] == f"{ROOT}/drugList" and branch["tree"]["children_omitted"] == 1
    with pytest.raises(KeyError):
        subtree(tree, "/nope")


def test_drift_reports_new_and_missing_paths():
    base = _formulary()
    assert drift(base, _formulary()) is None
    changed = build_tree(_doc(fixture("formulary_dc.xml").read_bytes().replace(
        b"</prescriptionDrugTemplateVO>", b"<extraBranch><x>1</x></extraBranch></prescriptionDrugTemplateVO>")))
    report = drift(base, changed)
    assert report["new"] == [f"{ROOT}/extraBranch", f"{ROOT}/extraBranch/x"] and report["missing"] == []
    assert report["new_count"] == 2


def test_resolve_child_parent_and_attribute_steps():
    tree = _formulary()
    assert resolve(tree, ROOT, "x:header/x:issuerId") == f"{ROOT}/header/issuerId"
    assert resolve(tree, f"{ROOT}/header", "../x:drugList") == f"{ROOT}/drugList"
    assert resolve(tree, f"{ROOT}/header", "x:issuerId/@cellLocation") == f"{ROOT}/header/issuerId/@cellLocation"
    assert resolve(tree, ROOT, "x:header/x:nope") is None
    assert resolve(tree, ROOT, "x:header/@nope") is None
    assert resolve(tree, ROOT, "x:header/x:issuerId/x:cellValue") == f"{ROOT}/header/issuerId/cellValue"
    assert resolve(tree, ROOT, ".//x:drugVO") == UNCHECKED
    assert resolve(tree, ROOT, "x:formularyList/x:formularyVO[1]") == UNCHECKED


def test_resolve_beyond_a_truncated_tree_is_unchecked(monkeypatch):
    monkeypatch.setattr(trees, "MAX_PATHS", 2)
    tree = build_tree(_doc("<r><a><b/></a></r>"))
    assert resolve(tree, "/r/a", "b") == UNCHECKED


def test_tree_warnings_flag_paths_that_cannot_occur():
    tree = _formulary()
    spec = load_json("formulary_tree_spec.json")
    assert tree_warnings(tree, spec) == []
    spec["select"][1]["select"][1]["forEach"] = "x:drugVOList/x:drug"
    spec["column"][3]["path"] = "x:header/x:templateVersionX"
    warnings = tree_warnings(tree, spec)
    assert any("x:drugVOList/x:drug" in w for w in warnings)
    assert any("templateVersionX" in w for w in warnings)


def test_tree_warnings_flag_a_missing_namespace_prefix():
    spec = {"kind": "tree", "select": [{"forEach": "formularyList/formularyVO", "table": "a.b.c",
                                        "column": [{"name": "id", "path": "formularyID"}]}]}
    [warning] = tree_warnings(_formulary(), spec)
    assert NS in warning and "namespaces" in warning
```

The last two tests use `tests/fixtures/formulary_tree_spec.json`. Create it now, with the content from the design
spec §5.2:

```json
{
  "kind": "tree",
  "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
  "value_child": "x:cellValue",
  "column": [
    {"name": "fileid", "path": "$file_id"},
    {"name": "serffbindername", "path": "$context.serffbindername"},
    {"name": "run_id", "path": "$context.run_id"},
    {"name": "templateVersion", "path": "x:header/x:templateVersion"},
    {"name": "issuer_id", "path": "x:header/x:issuerId"},
    {"name": "state_code", "path": "x:header/x:statePostalCode"}
  ],
  "select": [
    {"forEach": "x:formularyList/x:formularyVO",
     "column": [{"name": "formulary_id", "path": "x:formularyID"},
                {"name": "drug_list_id", "path": "x:drugListID"},
                {"name": "num_of_tiers", "path": "x:numberTiers"}],
     "select": [
       {"forEach": "x:formularyCostSharingTypeList/x:formularyCostSharingTypeVO",
        "column": [{"name": "drug_tier_id", "path": "x:drugTierLevel"},
                   {"name": "one_month", "path": "x:oneMonthOutNetworkRetailOfferedIndicator"},
                   {"name": "three_month_in", "path": "x:threeMonthInNetworkMailOfferedIndicator"},
                   {"name": "three_month_out", "path": "x:threeMonthOutNetworkMailOfferedIndicator"}],
        "select": [
          {"forEach": "x:costSharingTypeList/x:costSharingTypeVO",
           "table": "npvcs.xml.formulary_benefit_definition",
           "column": [{"name": "cost_coverage_type", "path": "x:networkCostType"},
                      {"name": "prescription_period", "path": "x:drugPrescriptionPeriodType"},
                      {"name": "copay", "path": "x:coPayment"},
                      {"name": "coin", "path": "x:coInsurance"}]}]}]},
    {"forEach": "x:drugList/x:drugListVO",
     "column": [{"name": "drug_list_id", "path": "x:drugListID"}],
     "select": [
       {"forEach": "x:drugTierLevelList/x:drugTierLevelVO",
        "table": "npvcs.xml.formulary_tier_definition",
        "column": [{"name": "tier_level", "path": "x:drugTierLevel"},
                   {"name": "tier_type", "path": "x:drugTierType"}]},
       {"forEach": "x:drugVOList/x:drugVO",
        "table": "npvcs.xml.formulary_drug_list",
        "column": [{"name": "rxcui", "path": "x:rXCUI"},
                   {"name": "tier_level", "path": "x:tier"},
                   {"name": "authorizationRequired", "path": "x:authorizationRequired"},
                   {"name": "stepTherapyRequired", "path": "x:stepTherapyRequired"}]}]}
  ]
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_trees.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.trees'`

- [ ] **Step 3: Implement `src/importer/trees.py`**

```python
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


def resolve(tree: dict, context: str, expr: str) -> str | None:
    """The element (or ``.../@attr``) path ``expr`` reaches from ``context``; UNCHECKED when the
    expression is not plain child / parent / attribute steps; None when it matches nothing."""
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
    return current


def tree_warnings(tree: dict, definition: dict) -> list[str]:
    """Spec paths that cannot occur in the stored tree (structural check; warnings, not errors)."""
    out: list[str] = []
    if tree.get("namespace") and not definition.get("namespaces"):
        out.append(f"the document's elements are in namespace {tree['namespace']}; declare it in \"namespaces\" "
                   f"(e.g. {{\"x\": \"{tree['namespace']}\"}}) and prefix every step, or no path will match")
        return out

    def walk(node: dict, context: str, label: str) -> None:
        for column in node.get("column", []):
            path = column.get("path", "")
            if context and not path.startswith("$") and resolve(tree, context, path) is None:
                out.append(f"{label}: column {column.get('name')!r} path {path!r} matches nothing under {context}")
        for child in node.get("select", []):
            for_each = child.get("forEach", "")
            name = child.get("table") or for_each
            target = resolve(tree, context, for_each) if context else UNCHECKED
            if target is None:
                out.append(f"{name}: forEach {for_each!r} matches nothing under {context}")
                continue
            walk(child, target, name)

    walk(definition, "/" + tree["root"], "root")
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_trees.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/importer/trees.py tests/test_trees.py tests/fixtures/formulary_tree_spec.json
git commit -m "Nesting trees: build, page, drift and structural checks (no values)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Tree spec parsing (`mappers/tree.py`, part 1)

**Files:**
- Create: `src/importer/mappers/tree.py`
- Test: `tests/test_tree_spec.py`

**Interfaces:**
- Consumes: `SpecError`, `Unpivot`, `IDENT`, `TARGET`, `CONTEXT` from `mappers/spec.py`.
- Produces:
  - `@dataclass(frozen=True) Column(name: str, path: str)`
  - `@dataclass(frozen=True) Node(for_each: str | None, table: str | None, columns: tuple[Column, ...],
    leaves: str | None = None, unmapped: str = "extras", unpivot: Unpivot | None = None, document: str | None = None,
    select: tuple[Node, ...] = ())`
  - `@dataclass(frozen=True) TreeSpec(root: Node, namespaces: dict, value_child: str | None)`
  - `parse_tree_spec(defn: dict, *, known_schemas: set[str] | None = None) -> TreeSpec`
  - `tables_in(definition: dict) -> list[str]`
  - `outline(definition: dict) -> list[dict]`: one entry `{"table", "path", "carries"}` per table node.

- [ ] **Step 1: Write the failing tests**

`tests/test_tree_spec.py`:

```python
from __future__ import annotations

import copy

import pytest

from importer.mappers.spec import SpecError
from importer.mappers.tree import outline, parse_tree_spec, tables_in
from tests.support import FORMULARY_TARGETS, load_json


def _spec(**root):
    base = {"kind": "tree", "select": [{"forEach": "a", "table": "d.s.t", "column": [{"name": "x", "path": "x"}]}]}
    base.update(root)
    return base


def test_formulary_tree_spec_parses():
    spec = parse_tree_spec(load_json("formulary_tree_spec.json"), known_schemas=set(FORMULARY_TARGETS))
    assert spec.root.for_each is None and len(spec.root.select) == 2
    assert spec.root.columns[0].path == "$file_id"


@pytest.mark.parametrize("defn, message", [
    ({"kind": "xml"}, "kind"),
    (_spec(bogus=1), "unknown keys"),
    ({"kind": "tree", "select": [{"table": "d.s.t", "column": [{"name": "x", "path": "x"}]}]}, "forEach"),
    ({"kind": "tree", "select": [{"forEach": "a", "table": "not an address"}]}, "schema address"),
    ({"kind": "tree", "select": [{"forEach": "a", "leaves": "auto", "select": [{"forEach": "b", "table": "d.s.t"}]}]},
     "only apply to a node with a table"),
    ({"kind": "tree", "select": [{"forEach": "a", "column": [{"name": "x", "path": "x"}]}]}, "no table node"),
    ({"kind": "tree", "column": [{"name": "x", "path": "x"}]}, "at least one table"),
    (_spec(column=[{"name": "bad name", "path": "x"}]), "must match"),
    (_spec(column=[{"name": "x", "path": "$nope"}]), "special"),
    (_spec(column=[{"name": "x", "path": "a"}, {"name": "x", "path": "b"}]), "twice"),
    (_spec(column=[{"name": "x"}]), "name and path"),
    ({"kind": "tree", "select": [{"forEach": "a[", "table": "d.s.t"}]}, "invalid XPath"),
    ({"kind": "tree", "select": [{"forEach": "a", "table": "d.s.t", "leaves": "all"}]}, "leaves"),
    ({"kind": "tree", "select": [{"forEach": "a", "table": "d.s.t", "unmapped": "drop"}]}, "unmapped"),
    ({"kind": "tree", "select": [{"forEach": "a", "table": "d.s.t", "document": "bad name"}]}, "document"),
    ({"kind": "tree", "select": [{"forEach": "a", "table": "d.s.t", "unpivot": {"over": "x:*"}}]}, "unpivot"),
])
def test_validation_rules(defn, message):
    with pytest.raises(SpecError, match=message):
        parse_tree_spec(defn)


def test_unknown_table_is_refused_when_schemas_are_known():
    with pytest.raises(SpecError, match="no active schema"):
        parse_tree_spec(_spec(), known_schemas={"d.s.other"})


def test_tables_and_outline():
    defn = load_json("formulary_tree_spec.json")
    assert tables_in(defn) == list(FORMULARY_TARGETS)
    first = outline(defn)[0]
    assert first["table"] == "npvcs.xml.formulary_benefit_definition"
    assert first["path"] == ("x:formularyList/x:formularyVO / x:formularyCostSharingTypeList/x:formularyCostSharingTypeVO"
                             " / x:costSharingTypeList/x:costSharingTypeVO")
    assert first["carries"][:3] == ["fileid", "serffbindername", "run_id"] and "drug_tier_id" in first["carries"]
    assert copy.deepcopy(defn) == load_json("formulary_tree_spec.json")  # not mutated
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_tree_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'importer.mappers.tree'`

- [ ] **Step 3: Implement the parsing half of `src/importer/mappers/tree.py`**

```python
"""The "tree" spec kind (design §8.24): an annotated nesting tree that one generic walker runs.

The vocabulary follows SQL on FHIR v2 ViewDefinition (forEach, column {name, path},
nested select), but sibling selects never form a cross product: every table node is its
own output and is walked independently, so row counts are bounded by element counts.
A node's columns are carried down to every table node beneath it. XML only (M2c-1).
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from .spec import CONTEXT, IDENT, TARGET, SpecError, Unpivot

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
    for expr in exprs:
        try:
            etree.XPath(expr, namespaces=spec.namespaces)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_tree_spec.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/importer/mappers/tree.py tests/test_tree_spec.py
git commit -m "Tree spec kind: parsing and validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The walker (`TreeMapper`) and the formulary golden test

**Files:**
- Modify: `src/importer/mappers/tree.py` (append the walker)
- Test: `tests/test_tree_mapper.py`

**Interfaces:**
- Consumes: `XmlValues`, `put` (Task 1); `TreeSpec`/`Node` (Task 3); `element_identity`, `local`
  (`readers/xml_reader.py`); `Mapper`, `RunContext`.
- Produces: `class TreeMapper(XmlValues, Mapper)` with `__init__(spec: TreeSpec, version: int = 1)`, the
  `targets -> tuple[str, ...]` property, and `extract(doc, ctx) -> Iterator[LandRow | RejectRow]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tree_mapper.py`:

```python
from __future__ import annotations

import pytest

from importer.mappers.spec import DeclarativeMapper, SpecError, parse_spec
from importer.mappers.tree import TreeMapper, parse_tree_spec
from importer.models import LandRow, RejectRow
from importer.readers import parse_document
from tests.support import ctx, fixture, load_json

REQUEST = {"serffbindername": "ABCD-DC25-1", "run_id": "42"}


def _run(defn: dict, xml, schemas: dict):
    doc = parse_document(xml if isinstance(xml, bytes) else xml.encode(), "XML")
    return list(TreeMapper(parse_tree_spec(defn)).extract(doc, ctx(schemas, REQUEST)))


def _by_key(rows):
    return {(r.target, r.row_identity): (r.fields, r.extras) for r in rows}


def test_formulary_tree_spec_matches_the_xml_spec_exactly():
    schemas = load_json("formulary_schemas.json")
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    old = list(DeclarativeMapper(parse_spec(load_json("formulary_spec.json"))).extract(doc, ctx(schemas, REQUEST)))
    new = list(TreeMapper(parse_tree_spec(load_json("formulary_tree_spec.json"))).extract(doc, ctx(schemas, REQUEST)))
    assert len(new) == len(old) == 10
    assert _by_key(new) == _by_key(old)


def test_carried_columns_and_nearer_node_wins():
    defn = {"kind": "tree", "column": [{"name": "k", "path": "k"}, {"name": "src", "path": "$file_id"}],
            "select": [{"forEach": "p", "column": [{"name": "k", "path": "pk"}],
                        "select": [{"forEach": "c", "table": "d.s.child", "column": [{"name": "v", "path": "v"}]}]}]}
    rows = _run(defn, "<r><k>root</k><p><pk>P1</pk><c><v>1</v></c><c><v>2</v></c></p></r>",
                {"d.s.child": ["k", "src", "v"]})
    assert [r.fields for r in rows] == [{"k": "P1", "src": "7", "v": "1"}, {"k": "P1", "src": "7", "v": "2"}]
    assert [r.row_identity for r in rows] == ["/p/c[1]", "/p/c[2]"]


def test_siblings_never_cross_join():
    defn = {"kind": "tree", "select": [
        {"forEach": "a", "table": "d.s.a", "column": [{"name": "x", "path": "."}]},
        {"forEach": "b", "table": "d.s.b", "column": [{"name": "y", "path": "."}]}]}
    rows = _run(defn, "<r>" + "<a>1</a>" * 4 + "<b>2</b>" * 5 + "</r>", {"d.s.a": ["x"], "d.s.b": ["y"]})
    assert [r.target for r in rows].count("d.s.a") == 4 and [r.target for r in rows].count("d.s.b") == 5


def test_identity_and_name_specials_link_children_to_parents():
    defn = {"kind": "tree", "select": [{"forEach": "pkg", "column": [{"name": "pkg_ref", "path": "$identity"}],
            "select": [{"forEach": "*", "table": "d.s.section",
                        "column": [{"name": "section", "path": "$name"}, {"name": "me", "path": "$row_identity"}]}]}]}
    rows = _run(defn, "<r><pkg><sbc/><hsa/></pkg><pkg><url/></pkg></r>", {"d.s.section": ["pkg_ref", "section", "me"]})
    assert [(r.fields["pkg_ref"], r.fields["section"], r.fields["me"]) for r in rows] == [
        ("/pkg[1]", "sbc", "/pkg[1]/sbc"), ("/pkg[1]", "hsa", "/pkg[1]/hsa"), ("/pkg[2]", "url", "/pkg[2]/url")]


def test_leaves_auto_matches_normalized_names_and_keeps_the_rest_in_extras():
    defn = {"kind": "tree", "namespaces": {"x": "urn:t"}, "value_child": "x:cellValue",
            "select": [{"forEach": "x:row", "table": "d.s.t", "leaves": "auto"}]}
    xml = ('<r xmlns="urn:t"><row><planId><cellValue>P-1</cellValue></planId><Metal_Level><cellValue>Gold</cellValue>'
           '</Metal_Level><odd>z</odd><nested><deep>n</deep></nested></row></r>')
    [row] = _run(defn, xml, {"d.s.t": ["planid", "metallevel"]})
    assert row.fields == {"planid": "P-1", "metallevel": "Gold"} and row.extras == {"odd": "z"}


def test_unmapped_ignore_drops_unmatched_leaves():
    defn = {"kind": "tree", "select": [{"forEach": "row", "table": "d.s.t", "leaves": "auto", "unmapped": "ignore"}]}
    [row] = _run(defn, "<r><row><a>1</a><zz>2</zz></row></r>", {"d.s.t": ["a"]})
    assert row.fields == {"a": "1"} and row.extras is None


def test_explicit_column_beats_leaves_auto():
    defn = {"kind": "tree", "select": [{"forEach": "row", "table": "d.s.t", "leaves": "auto",
                                        "column": [{"name": "a", "path": "b"}]}]}
    [row] = _run(defn, "<r><row><a>from-a</a><b>from-b</b></row></r>", {"d.s.t": ["a"]})
    assert row.fields == {"a": "from-b"} and row.extras == {"a": "from-a"}  # the displaced leaf is kept


def test_document_lands_the_subtree_as_xml_text():
    defn = {"kind": "tree", "select": [{"forEach": "odd", "table": "d.s.docs", "document": "doc"}]}
    [row] = _run(defn, "<r><odd a='1'><x>1</x></odd></r>", {"d.s.docs": ["doc"]})
    assert row.fields["doc"] == '<odd a="1"><x>1</x></odd>'


def test_unpivot_and_multi_match():
    defn = {"kind": "tree", "select": [
        {"forEach": "items", "table": "d.s.rate", "unpivot": {"over": "*", "name_column": "n", "value_column": "v"}},
        {"forEach": "d", "table": "d.s.tier", "column": [{"name": "tier", "path": "tier"}]}]}
    rows = _run(defn, "<r><items><age>21</age><age>22</age></items><d><tier>1</tier><tier>2</tier></d></r>",
                {"d.s.rate": ["n", "v"], "d.s.tier": ["tier"]})
    assert [r.row_identity for r in rows[:2]] == ["/items#age", "/items#age[2]"]
    assert rows[2].fields == {"tier": "1"} and rows[2].extras == {"tier": ["1", "2"]}


def test_root_table_node_gives_one_row_per_file():
    defn = {"kind": "tree", "table": "d.s.file", "column": [{"name": "v", "path": "version"}]}
    [row] = _run(defn, "<r><version>3</version></r>", {"d.s.file": ["v"]})
    assert row.row_identity == "/" and row.fields == {"v": "3"}


def test_for_each_on_attributes_or_text_yields_nothing():
    defn = {"kind": "tree", "select": [{"forEach": "a/@id", "table": "d.s.t", "column": [{"name": "v", "path": "."}]},
                                       {"forEach": "a/text()", "table": "d.s.t", "column": [{"name": "v", "path": "."}]}]}
    assert _run(defn, "<r><a id='1'>t</a></r>", {"d.s.t": ["v"]}) == []


def test_row_level_exception_is_a_mapping_error_and_the_walk_continues(monkeypatch):
    original = TreeMapper._value

    def flaky(self, el):
        value = original(self, el)
        if value == "boom":
            raise ValueError("bad value")
        return value

    monkeypatch.setattr(TreeMapper, "_value", flaky)
    defn = {"kind": "tree", "select": [{"forEach": "d", "table": "d.s.t", "column": [{"name": "a", "path": "a"}]}]}
    rows = _run(defn, "<r><d><a>ok</a></d><d><a>boom</a></d><d><a>ok2</a></d></r>", {"d.s.t": ["a"]})
    assert isinstance(rows[0], LandRow) and isinstance(rows[2], LandRow)
    assert isinstance(rows[1], RejectRow) and rows[1].reason_code == "mapping_error" and rows[1].row_identity == "/d[2]"


def test_tree_spec_refuses_a_tabular_document():
    mapper = TreeMapper(parse_tree_spec(load_json("formulary_tree_spec.json")))
    with pytest.raises(SpecError, match="tree spec"):
        list(mapper.extract(parse_document(b"a,b\n1,2\n", "CSV"), ctx({})))


def test_targets_in_walk_order():
    mapper = TreeMapper(parse_tree_spec(load_json("formulary_tree_spec.json")))
    assert mapper.targets == ("npvcs.xml.formulary_benefit_definition", "npvcs.xml.formulary_tier_definition",
                              "npvcs.xml.formulary_drug_list")
```

In `test_explicit_column_beats_leaves_auto`, the explicit column consumes `<b>`. The unconsumed `<a>` leaf then
finds its schema column already filled, so it goes to `_extras` under its own name: nothing is lost. The `xml`
kind drops a leaf whose name is already a field; the tree kind keeps it. The formulary golden test is unaffected,
because no unconsumed leaf there shares a field's name.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_tree_mapper.py -v`
Expected: FAIL with `ImportError: cannot import name 'TreeMapper'`

- [ ] **Step 3: Append the walker to `src/importer/mappers/tree.py`**

Add these imports at the top, next to the existing ones:

```python
from collections.abc import Iterator

from ..models import LandRow, RejectRow
from ..readers.base import XmlDocument, normalize_name
from ..readers.xml_reader import element_identity, local
from .base import Mapper, RunContext
from .values import XmlValues, put
```

Append:

```python
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
        yield from self._walk(self.spec.root, doc.root, doc.root, {}, {}, ctx)

    def _reject(self, node: Node, el, identity: str, exc: Exception) -> RejectRow:
        return RejectRow(node.table, identity, etree.tostring(el, encoding="unicode", with_tail=False),
                         "mapping_error", f"{type(exc).__name__}: {exc}")

    def _walk(self, node: Node, el, root, carried: dict, carried_multi: dict, ctx: RunContext):
        identity = element_identity(el, root)
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
                if isinstance(match, etree._Element):
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
                if not isinstance(child, etree._Element):
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
        for child in el:
            if not isinstance(child.tag, str) or child in used or not self._is_value_element(child):
                continue
            name = local(child)
            column = by_norm.get(normalize_name(name))
            if column is not None and column not in fields:
                fields[column] = self._value(child)
            elif node.unmapped == "extras" and name not in extras:
                extras[name] = self._value(child)  # lossless: a leaf displaced by an explicit column is kept
        yield LandRow(node.table, identity, fields, extras or None)
```

`document` serialization: `etree.tostring(..., with_tail=False)` leaves out the whitespace that follows the element.
For a namespaced document it includes the element's `xmlns` declaration. That is correct, because the column must
stay well-formed XML on its own. The test uses a namespace-free document, so it sees no `xmlns`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_tree_mapper.py tests/test_tree_spec.py tests/test_spec_xml.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/importer/mappers/tree.py tests/test_tree_mapper.py
git commit -m "TreeMapper: depth-first walker with carried keys; formulary matches the xml spec row for row

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Synthetic plan-benefit template and its tree spec

**Files:**
- Create: `tests/fixtures/benefits_sample.xml`, `tests/fixtures/benefits_tree_spec.json`, `tests/fixtures/benefits_schemas.json`
- Modify: `tests/support.py` (add `BENEFIT_TARGETS`)
- Test: `tests/test_tree_mapper.py` (append)

**Interfaces:**
- Consumes: `TreeMapper`, `parse_tree_spec`.
- Produces: the fixtures above, and `tests.support.BENEFIT_TARGETS: tuple[str, ...]` (11 addresses, in the order
  below).

- [ ] **Step 1: Create the fixture `tests/fixtures/benefits_sample.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- Synthetic plan-benefit template, modelled on the structure npvcs_env parseBenefits.py walks. Invented values. -->
<planBenefitTemplateVO xmlns="http://vo.ffe.cms.hhs.gov">
  <packagesList>
    <packages>
      <header>
        <templateVersion>v13.0</templateVersion>
        <issuerId cellLocation="B2-1"><cellValue>11111</cellValue></issuerId>
        <statePostalCode cellLocation="B3-1"><cellValue>DC</cellValue></statePostalCode>
        <dentalPlanOnlyInd cellLocation="B4-1"><cellValue>No</cellValue></dentalPlanOnlyInd>
        <marketCoverage cellLocation="B5-1"><cellValue>Individual</cellValue></marketCoverage>
      </header>
      <benefitsList>
        <benefits>
          <benefitTypeCode><cellValue>Primary Care Visit</cellValue></benefitTypeCode>
          <isEHB><cellValue>Yes</cellValue></isEHB>
          <isBenefitCovered><cellValue>Covered</cellValue></isBenefitCovered>
        </benefits>
        <benefits>
          <benefitTypeCode><cellValue>Specialist Visit</cellValue></benefitTypeCode>
          <isEHB><cellValue>Yes</cellValue></isEHB>
          <isBenefitCovered><cellValue>Covered</cellValue></isBenefitCovered>
        </benefits>
      </benefitsList>
      <plansList>
        <plans>
          <planAttributes>
            <standardComponentID><cellValue>11111DC0010001</cellValue></standardComponentID>
            <planMarketingName><cellValue>Sample Gold</cellValue></planMarketingName>
            <metalLevel><cellValue>Gold</cellValue></metalLevel>
          </planAttributes>
          <costShareVariancesList>
            <costShareVariance>
              <planId><cellValue>11111DC0010001-01</cellValue></planId>
              <planVariantMarketingName><cellValue>Sample Gold</cellValue></planVariantMarketingName>
              <metalLevel><cellValue>Gold</cellValue></metalLevel>
              <csrVariationType><cellValue>Standard Gold On Exchange Plan</cellValue></csrVariationType>
              <sbc>
                <havingBabyDeductible><cellValue>$500</cellValue></havingBabyDeductible>
                <havingBabyCoPayment><cellValue>$0</cellValue></havingBabyCoPayment>
              </sbc>
              <hsa><hsaEligibility><cellValue>No</cellValue></hsaEligibility></hsa>
              <url><planBrochure><cellValue>https://plans.example.org/a1</cellValue></planBrochure></url>
              <avCalculator><avCalculatorOutputNumber><cellValue>0.781</cellValue></avCalculatorOutputNumber></avCalculator>
              <moopList>
                <moop><name><cellValue>Medical EHB MOOP</cellValue></name><inNetworkTier1IndividualAmount><cellValue>$8,000</cellValue></inNetworkTier1IndividualAmount></moop>
                <moop><name><cellValue>Drug EHB MOOP</cellValue></name><inNetworkTier1IndividualAmount><cellValue>$2,000</cellValue></inNetworkTier1IndividualAmount></moop>
              </moopList>
              <planDeductibleList>
                <planDeductible><deductibleType><cellValue>Medical EHB Deductible</cellValue></deductibleType><inNetworkTier1Individual><cellValue>$1,500</cellValue></inNetworkTier1Individual></planDeductible>
              </planDeductibleList>
              <serviceVisitList>
                <serviceVisit><visitType><cellValue>Primary Care Visit</cellValue></visitType><copayInNetworkTier1><cellValue>$20</cellValue></copayInNetworkTier1></serviceVisit>
                <serviceVisit><visitType><cellValue>Specialist Visit</cellValue></visitType><copayInNetworkTier1><cellValue>$40</cellValue></copayInNetworkTier1></serviceVisit>
              </serviceVisitList>
            </costShareVariance>
            <costShareVariance>
              <planId><cellValue>11111DC0010001-02</cellValue></planId>
              <planVariantMarketingName><cellValue>Sample Gold Off Exchange</cellValue></planVariantMarketingName>
              <metalLevel><cellValue>Gold</cellValue></metalLevel>
              <csrVariationType><cellValue>Standard Gold Off Exchange Plan</cellValue></csrVariationType>
              <sbc>
                <havingBabyDeductible><cellValue>$500</cellValue></havingBabyDeductible>
                <havingBabyCoPayment><cellValue>$10</cellValue></havingBabyCoPayment>
              </sbc>
              <hsa><hsaEligibility><cellValue>No</cellValue></hsaEligibility></hsa>
              <url><planBrochure><cellValue>https://plans.example.org/a2</cellValue></planBrochure></url>
              <avCalculator><avCalculatorOutputNumber><cellValue>0.779</cellValue></avCalculatorOutputNumber></avCalculator>
              <moopList>
                <moop><name><cellValue>Medical EHB MOOP</cellValue></name><inNetworkTier1IndividualAmount><cellValue>$8,000</cellValue></inNetworkTier1IndividualAmount></moop>
                <moop><name><cellValue>Drug EHB MOOP</cellValue></name><inNetworkTier1IndividualAmount><cellValue>$2,500</cellValue></inNetworkTier1IndividualAmount></moop>
              </moopList>
              <planDeductibleList>
                <planDeductible><deductibleType><cellValue>Medical EHB Deductible</cellValue></deductibleType><inNetworkTier1Individual><cellValue>$1,750</cellValue></inNetworkTier1Individual></planDeductible>
              </planDeductibleList>
              <serviceVisitList>
                <serviceVisit><visitType><cellValue>Primary Care Visit</cellValue></visitType><copayInNetworkTier1><cellValue>$25</cellValue></copayInNetworkTier1></serviceVisit>
                <serviceVisit><visitType><cellValue>Specialist Visit</cellValue></visitType><copayInNetworkTier1><cellValue>$45</cellValue></copayInNetworkTier1></serviceVisit>
              </serviceVisitList>
            </costShareVariance>
          </costShareVariancesList>
        </plans>
      </plansList>
    </packages>
    <packages>
      <header>
        <templateVersion>v13.0</templateVersion>
        <issuerId cellLocation="B2-2"><cellValue>22222</cellValue></issuerId>
        <statePostalCode cellLocation="B3-2"><cellValue>DC</cellValue></statePostalCode>
        <dentalPlanOnlyInd cellLocation="B4-2"><cellValue>No</cellValue></dentalPlanOnlyInd>
        <marketCoverage cellLocation="B5-2"><cellValue>SHOP (Small Group)</cellValue></marketCoverage>
      </header>
      <benefitsList>
        <benefits>
          <benefitTypeCode><cellValue>Primary Care Visit</cellValue></benefitTypeCode>
          <isEHB><cellValue>Yes</cellValue></isEHB>
          <isBenefitCovered><cellValue>Covered</cellValue></isBenefitCovered>
        </benefits>
      </benefitsList>
      <plansList>
        <plans>
          <planAttributes>
            <standardComponentID><cellValue>22222DC0020001</cellValue></standardComponentID>
            <planMarketingName><cellValue>Sample Silver</cellValue></planMarketingName>
            <metalLevel><cellValue>Silver</cellValue></metalLevel>
          </planAttributes>
          <costShareVariancesList>
            <costShareVariance>
              <planId><cellValue>22222DC0020001-01</cellValue></planId>
              <planVariantMarketingName><cellValue>Sample Silver</cellValue></planVariantMarketingName>
              <metalLevel><cellValue>Silver</cellValue></metalLevel>
              <csrVariationType><cellValue>Standard Silver On Exchange Plan</cellValue></csrVariationType>
              <sbc>
                <havingBabyDeductible><cellValue>$1,000</cellValue></havingBabyDeductible>
                <havingBabyCoPayment><cellValue>$30</cellValue></havingBabyCoPayment>
              </sbc>
              <hsa><hsaEligibility><cellValue>Yes</cellValue></hsaEligibility></hsa>
              <url><planBrochure><cellValue>https://plans.example.org/b1</cellValue></planBrochure></url>
              <avCalculator><avCalculatorOutputNumber><cellValue>0.701</cellValue></avCalculatorOutputNumber></avCalculator>
              <moopList>
                <moop><name><cellValue>Medical EHB MOOP</cellValue></name><inNetworkTier1IndividualAmount><cellValue>$9,000</cellValue></inNetworkTier1IndividualAmount></moop>
              </moopList>
              <planDeductibleList>
                <planDeductible><deductibleType><cellValue>Combined Deductible</cellValue></deductibleType><inNetworkTier1Individual><cellValue>$3,000</cellValue></inNetworkTier1Individual></planDeductible>
              </planDeductibleList>
              <serviceVisitList>
                <serviceVisit><visitType><cellValue>Primary Care Visit</cellValue></visitType><copayInNetworkTier1><cellValue>$35</cellValue></copayInNetworkTier1></serviceVisit>
              </serviceVisitList>
            </costShareVariance>
          </costShareVariancesList>
        </plans>
      </plansList>
    </packages>
  </packagesList>
</planBenefitTemplateVO>
```

- [ ] **Step 2: Create `tests/fixtures/benefits_schemas.json`**

The column lists mirror the legacy tables' shape: lineage columns plus the element names, lowercased.

```json
{
  "npvcs.xml.package": ["fileid", "serffbindername", "run_id", "packageid", "packagename", "templateversion", "issuerid", "statepostalcode", "dentalplanonlyind", "marketcoverage"],
  "npvcs.xml.benefits": ["fileid", "serffbindername", "run_id", "packageid", "benefittypecode", "isehb", "isbenefitcovered"],
  "npvcs.xml.planattributes": ["fileid", "serffbindername", "run_id", "packageid", "standardcomponentid", "planmarketingname", "metallevel"],
  "npvcs.xml.costsharevariance": ["fileid", "serffbindername", "run_id", "packageid", "costsharevarianceid", "planid", "planvariantmarketingname", "metallevel", "csrvariationtype"],
  "npvcs.xml.sbc": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "havingbabydeductible", "havingbabycopayment"],
  "npvcs.xml.hsa": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "hsaeligibility"],
  "npvcs.xml.url": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "planbrochure"],
  "npvcs.xml.avcalculator": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "avcalculatoroutputnumber"],
  "npvcs.xml.mooplist": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "name", "innetworktier1individualamount"],
  "npvcs.xml.plandeductiblelist": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "deductibletype", "innetworktier1individual"],
  "npvcs.xml.servicevisitlist": ["fileid", "serffbindername", "run_id", "costsharevarianceid", "visittype", "copayinnetworktier1"]
}
```

- [ ] **Step 3: Create `tests/fixtures/benefits_tree_spec.json`**

```json
{
  "kind": "tree",
  "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
  "value_child": "x:cellValue",
  "column": [
    {"name": "fileid", "path": "$file_id"},
    {"name": "serffbindername", "path": "$context.serffbindername"},
    {"name": "run_id", "path": "$context.run_id"}
  ],
  "select": [
    {"forEach": "x:packagesList/x:packages",
     "column": [{"name": "packageid", "path": "$identity"}],
     "select": [
       {"forEach": "x:header", "table": "npvcs.xml.package", "leaves": "auto",
        "column": [{"name": "packagename", "path": "x:issuerId/@cellLocation"}]},
       {"forEach": "x:benefitsList/x:benefits", "table": "npvcs.xml.benefits", "leaves": "auto"},
       {"forEach": "x:plansList/x:plans",
        "select": [
          {"forEach": "x:planAttributes", "table": "npvcs.xml.planattributes", "leaves": "auto"},
          {"forEach": "x:costShareVariancesList/x:costShareVariance", "table": "npvcs.xml.costsharevariance",
           "leaves": "auto", "column": [{"name": "costsharevarianceid", "path": "$identity"}],
           "select": [
             {"forEach": "x:sbc", "table": "npvcs.xml.sbc", "leaves": "auto"},
             {"forEach": "x:hsa", "table": "npvcs.xml.hsa", "leaves": "auto"},
             {"forEach": "x:url", "table": "npvcs.xml.url", "leaves": "auto"},
             {"forEach": "x:avCalculator", "table": "npvcs.xml.avcalculator", "leaves": "auto"},
             {"forEach": "x:moopList/x:moop", "table": "npvcs.xml.mooplist", "leaves": "auto"},
             {"forEach": "x:planDeductibleList/x:planDeductible", "table": "npvcs.xml.plandeductiblelist", "leaves": "auto"},
             {"forEach": "x:serviceVisitList/x:serviceVisit", "table": "npvcs.xml.servicevisitlist", "leaves": "auto"}
           ]}
        ]}
     ]}
  ]
}
```

- [ ] **Step 4: Add `BENEFIT_TARGETS` to `tests/support.py`** (below `FORMULARY_TARGETS`)

```python
BENEFIT_TARGETS = (
    "npvcs.xml.package", "npvcs.xml.benefits", "npvcs.xml.planattributes", "npvcs.xml.costsharevariance",
    "npvcs.xml.sbc", "npvcs.xml.hsa", "npvcs.xml.url", "npvcs.xml.avcalculator", "npvcs.xml.mooplist",
    "npvcs.xml.plandeductiblelist", "npvcs.xml.servicevisitlist",
)
```

- [ ] **Step 5: Append the failing tests to `tests/test_tree_mapper.py`**

```python
BENEFIT_COUNTS = {
    "npvcs.xml.package": 2, "npvcs.xml.benefits": 3, "npvcs.xml.planattributes": 2, "npvcs.xml.costsharevariance": 3,
    "npvcs.xml.sbc": 3, "npvcs.xml.hsa": 3, "npvcs.xml.url": 3, "npvcs.xml.avcalculator": 3,
    "npvcs.xml.mooplist": 5, "npvcs.xml.plandeductiblelist": 3, "npvcs.xml.servicevisitlist": 5,
}


def _benefit_rows():
    return _run(load_json("benefits_tree_spec.json"), fixture("benefits_sample.xml").read_bytes(),
                load_json("benefits_schemas.json"))


def test_benefits_land_one_row_per_record_element():
    from tests.support import BENEFIT_TARGETS

    rows = _benefit_rows()
    assert all(isinstance(r, LandRow) for r in rows)
    counts = {}
    for r in rows:
        counts[r.target] = counts.get(r.target, 0) + 1
    assert counts == BENEFIT_COUNTS and sum(counts.values()) == 35
    assert TreeMapper(parse_tree_spec(load_json("benefits_tree_spec.json"))).targets == BENEFIT_TARGETS


def test_benefit_package_row_reads_the_header_and_the_cell_location():
    package = next(r for r in _benefit_rows() if r.target == "npvcs.xml.package")
    assert package.row_identity == "/packagesList/packages[1]/header"
    assert package.fields == {
        "fileid": "7", "serffbindername": "ABCD-DC25-1", "run_id": "42", "packageid": "/packagesList/packages[1]",
        "packagename": "B2-1", "templateversion": "v13.0", "issuerid": "11111", "statepostalcode": "DC",
        "dentalplanonlyind": "No", "marketcoverage": "Individual",
    }
    assert package.extras is None


def test_benefit_children_carry_their_parent_keys():
    rows = _benefit_rows()
    variances = [r for r in rows if r.target == "npvcs.xml.costsharevariance"]
    assert variances[0].fields["costsharevarianceid"] == variances[0].row_identity
    assert variances[0].fields["planid"] == "11111DC0010001-01" and variances[0].extras is None
    moops = [r for r in rows if r.target == "npvcs.xml.mooplist"]
    assert moops[4].fields["costsharevarianceid"] == variances[2].row_identity
    assert moops[4].extras == {"packageid": "/packagesList/packages[2]"}  # carried key the table lacks: lossless
```

- [ ] **Step 6: Run them to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_tree_mapper.py -v`
Expected: all PASS. These tests exercise code from Task 4, so they pass at once. If any fails, the fixture and the
spec disagree: fix the fixture or the spec, never the walker.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/benefits_sample.xml tests/fixtures/benefits_schemas.json tests/fixtures/benefits_tree_spec.json tests/support.py tests/test_tree_mapper.py
git commit -m "Synthetic plan-benefit template: 11 tables by one tree spec

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Loading dispatch (`declarative_mapper`, `load_mapper` accepts `tree`)

**Files:**
- Modify: `src/importer/mappers/loading.py`
- Test: `tests/test_loading_and_admin.py` (append)

**Interfaces:**
- Consumes: `parse_spec`, `DeclarativeMapper`, `parse_tree_spec`, `TreeMapper`, `registered_shapers`.
- Produces:
  - `DECLARATIVE_KINDS = ("xml", "tabular", "tree")`
  - `declarative_mapper(definition: dict, *, known_schemas: set[str] | None, version: int = 1) -> Mapper`, which
    raises `SpecError`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_loading_and_admin.py`)**

```python
def test_tree_specs_load_and_register():
    from importer.admin import register_mapper, register_schema
    from importer.mappers.loading import MapperLoadError, declarative_mapper, load_mapper
    from importer.mappers.spec import SpecError
    from importer.mappers.tree import TreeMapper
    from importer.memory_store import MemoryStore
    from tests.support import configure_for, load_json

    schemas = load_json("formulary_schemas.json")
    store = MemoryStore()
    configure_for(store, schemas)
    with store.transaction() as tx:
        for address, columns in schemas.items():
            register_schema(tx, address, columns, created_by="t")
        row = register_mapper(tx, "formulary_tree", load_json("formulary_tree_spec.json"), created_by="t")
    assert isinstance(load_mapper(row, known_schemas=set(schemas)), TreeMapper)
    with pytest.raises(MapperLoadError, match="invalid_spec"):
        load_mapper(row, known_schemas={"npvcs.xml.formulary_drug_list"})
    with pytest.raises(SpecError, match="kind must be one of"):
        declarative_mapper({"kind": "yaml"}, known_schemas=None)
```

If `pytest` is not already imported at the top of that file, add `import pytest`.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_loading_and_admin.py::test_tree_specs_load_and_register -v`
Expected: FAIL with `ImportError: cannot import name 'declarative_mapper'`

- [ ] **Step 3: Implement in `src/importer/mappers/loading.py`**

Add the import `from .tree import TreeMapper, parse_tree_spec`, then add this below `file_sha256`:

```python
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
```

In `load_mapper`, replace the `if kind in ("xml", "tabular"):` block with:

```python
    if kind in DECLARATIVE_KINDS:
        try:
            return declarative_mapper(definition, known_schemas=known_schemas, version=artifact.version_no)
        except SpecError as exc:
            raise MapperLoadError("invalid_spec", str(exc)) from exc
```

- [ ] **Step 4: Run the loading suite**

Run: `.venv/Scripts/python -m pytest tests/test_loading_and_admin.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/importer/mappers/loading.py tests/test_loading_and_admin.py
git commit -m "Load and register tree specs through one declarative dispatch

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The tree on the signature, the XML profile, and record candidates

**Files:**
- Modify: `src/importer/fingerprint.py`, `src/importer/profiler.py`
- Test: `tests/test_profiler.py` (replace `test_xml_profile_counts_paths`, append), `tests/test_fingerprint.py` (append)

**Interfaces:**
- Consumes: `build_tree` (Task 2).
- Produces:
  - XML `Signature.observed == {"root", "namespace", "tree": <build_tree result>}`.
  - `profile(doc)` for XML returns `{"kind": "xml", "root", "namespace", "tree"}`.
  - `match()` appends `Candidate(kind="record", table=<node path>, schema_id, score, matched, missing, extra,
    jaccard)`: the best schema per record node, at most `MAX_RECORD_CANDIDATES = 20`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_profiler.py`, replace `test_xml_profile_counts_paths` with:

```python
def test_xml_profile_is_the_nesting_tree():
    prof = profile(parse_document(fixture("formulary_dc.xml").read_bytes(), "XML"))
    assert prof["kind"] == "xml" and prof["root"] == "prescriptionDrugTemplateVO"
    assert prof["tree"]["tree"]["path"] == "/prescriptionDrugTemplateVO"
    assert "paths" not in prof
```

Append:

```python
def test_xml_record_nodes_are_matched_against_schemas():
    store = MemoryStore()
    schemas = load_json("formulary_schemas.json")
    configure(store, "npvcs")
    with store.transaction() as tx:
        for address, columns in schemas.items():
            register_schema(tx, address, columns, created_by="t")
    doc = parse_document(fixture("formulary_dc.xml").read_bytes(), "XML")
    sig = fingerprint(doc, Declared.normalize("ME", "PC", "XML"))
    with store.transaction() as tx:
        candidates = match(sig, profile(doc), tx.registry, tx.artifacts)
    records = {c.table: c for c in candidates if c.kind == "record"}
    drug = records["/prescriptionDrugTemplateVO/drugList/drugListVO/drugVOList/drugVO"]
    assert drug.schema_id == "npvcs.xml.formulary_drug_list" and drug.score == 0.6
    assert drug.matched == ["authorizationrequired", "rxcui", "steptherapyrequired"]
    assert records["/prescriptionDrugTemplateVO/formularyList/formularyVO"].schema_id == "npvcs.xml.formulary_benefit_definition"
    assert drug.summary().startswith("looks like npvcs.xml.formulary_drug_list at /prescriptionDrugTemplateVO/drugList")
```

Append to `tests/test_fingerprint.py`:

```python
def test_xml_observed_carries_the_nesting_tree():
    sig = _sig(FIXTURE.read_bytes(), "XML", project="DC")
    assert sig.observed["root"] == "prescriptionDrugTemplateVO"
    assert sig.observed["tree"]["tree"]["path"] == "/prescriptionDrugTemplateVO"
    assert sig.fingerprint == "DC|PC|XML|prescriptionDrugTemplateVO"  # the signature itself is unchanged
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_profiler.py tests/test_fingerprint.py -v`
Expected: FAIL. `KeyError: 'tree'` in the new tests.

- [ ] **Step 3: Implement**

In `src/importer/fingerprint.py`, add `from .trees import build_tree`, and change the XML branch of `physical_part`
to:

```python
    if isinstance(doc, XmlDocument):
        return doc.local_name, {"root": doc.local_name, "namespace": doc.namespace, "tree": build_tree(doc)}
```

Also extend the module docstring line `XML: the root element's local name (namespace checked separately).` with
` Its nesting tree rides along in observed (§8.24).`

In `src/importer/profiler.py`:
- Add `from .trees import build_tree`.
- Remove the import of `local` (no longer used) and the constant `MAX_PATHS`.
- Add `MAX_RECORD_CANDIDATES = 20`.
- Replace the XML branch of `profile`:

```python
    if isinstance(doc, XmlDocument):
        return {"kind": "xml", "root": doc.local_name, "namespace": doc.namespace, "tree": build_tree(doc)}
```

Update the `Candidate` comment and summary:

```python
    kind: str  # reuse_mapper | schema | record (table = the record node's path)
```

```python
    def summary(self) -> str:
        if self.kind == "reuse_mapper":
            return f"looks like {self.fingerprint} -> reuse mapper {self.mapper_version_id}"
        parts = [f"{self.score:.2f}"]
        if self.extra:
            parts.append("extra: " + ", ".join(self.extra))
        if self.missing:
            parts.append("missing: " + ", ".join(self.missing))
        where = f" at {self.table}" if self.kind == "record" else ""
        return f"looks like {self.schema_id}{where} ({'; '.join(parts)})"
```

Add the record matcher and use it in `match`:

```python
def _record_nodes(node: dict):
    kids = node.get("children", [])
    leaves = [normalize_name(c["name"]) for c in kids if c.get("leaf")]
    if kids and ("repeats" in node or len(leaves) >= 2) and leaves:
        yield node["path"], set(leaves)
    for child in kids:
        yield from _record_nodes(child)


def _record_candidates(tree: dict, schemas: list[tuple[str, list[str]]], min_score: float) -> list[Candidate]:
    out: list[Candidate] = []
    for path, observed in _record_nodes(tree["tree"]):
        best: Candidate | None = None
        for schema_id, schema_columns in schemas:
            canonical = {normalize_name(c) for c in schema_columns}
            common = observed & canonical
            score = round(len(common) / len(observed), 2)
            if score < min_score:
                continue
            c = Candidate("record", score, schema_id=schema_id, table=path, matched=sorted(common),
                          missing=sorted(canonical - observed), extra=sorted(observed - canonical),
                          jaccard=round(len(common) / len(observed | canonical), 2))
            if best is None or (c.score, c.jaccard, -len(c.schema_id)) > (best.score, best.jaccard, -len(best.schema_id)):
                best = c
        if best:
            out.append(best)
    out.sort(key=lambda c: (-c.score, -c.jaccard, c.table))
    return out[:MAX_RECORD_CANDIDATES]
```

In `match`, load `schemas` once, before the tabular branch, and reuse it in that branch:

```python
    schemas = [(a.artifact_key, a.definition["columns"]) for a in artifacts.active_schemas()]
    if prof["kind"] == "tabular":
        for table_name, columns in prof["tables"].items():
            ...  # unchanged body, minus its own `schemas = ...` line
    candidates.sort(key=lambda c: (-c.score, -c.jaccard, c.kind != "reuse_mapper", c.fingerprint or c.schema_id or ""))
    records = _record_candidates(prof["tree"], schemas, min_score) if prof["kind"] == "xml" else []
    return candidates[:limit] + records
```

- [ ] **Step 4: Run the suites**

Run: `.venv/Scripts/python -m pytest tests/test_profiler.py tests/test_fingerprint.py tests/test_onboarding.py tests/test_pipeline.py -v`
Expected: all PASS. `test_onboarding.py::test_reuse_path_and_xml_profile` still passes, because `prof["profile"]`
keeps `kind` and `root`.

- [ ] **Step 5: Commit**

```bash
git add src/importer/fingerprint.py src/importer/profiler.py tests/test_profiler.py tests/test_fingerprint.py
git commit -m "Store the nesting tree with XML signatures; record candidates for XML

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Drift check and tree backfill on XML hits

**Files:**
- Modify: `src/importer/repositories.py`, `src/importer/memory_store.py`, `src/importer/mssql_store.py`, `src/importer/pipeline.py`
- Test: `tests/test_tree_pipeline.py`

**Interfaces:**
- Consumes: `drift` (Task 2); `Signature.observed["tree"]` (Task 7).
- Produces:
  - `SignatureRegistry.set_observed(signature_id: int, observed: dict) -> None`
  - the `decision_log` action `tree_drift`, with `observed = drift(...)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tree_pipeline.py`:

```python
from __future__ import annotations

from importer.memory_store import MemoryStore
from importer.models import Declared, FileStatus
from importer.pipeline import process
from tests.support import fixture, load_json, setup_signature

ROOT = "/prescriptionDrugTemplateVO"


def _bound_store() -> tuple[MemoryStore, int]:
    store = MemoryStore()
    setup_signature(store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
                    load_json("formulary_schemas.json"), load_json("formulary_tree_spec.json"), "formulary_tree")
    [sid] = list(store.state.registry)
    return store, sid


def _copy(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _drift_rows(store):
    return [d for d in store.state.decisions if d["action_type"] == "tree_drift"]


def test_a_miss_stores_the_tree_with_the_proposed_signature(tmp_path):
    store = MemoryStore()
    process(_copy(tmp_path, "odd.xml", b"<root><item><code>A</code></item></root>"),
            project="VT", cls="PC", file_type="XML", source="t", store=store)
    [row] = store.state.registry.values()
    assert row.observed_schema["tree"]["tree"]["children"][0]["path"] == "/root/item"


def test_a_hit_with_the_same_structure_writes_no_drift(tmp_path):
    store, _ = _bound_store()
    data = fixture("formulary_dc.xml").read_bytes() + b"\n<!-- same -->\n"
    result = process(_copy(tmp_path, "f.xml", data), project="DC", cls="PC", file_type="XML", source="t", store=store)
    assert result.status is FileStatus.COMPLETED and _drift_rows(store) == []


def test_a_hit_with_a_new_branch_reports_drift_and_still_lands(tmp_path):
    store, _ = _bound_store()
    data = fixture("formulary_dc.xml").read_bytes().replace(
        b"</prescriptionDrugTemplateVO>", b"<extraBranch><x>1</x></extraBranch></prescriptionDrugTemplateVO>")
    result = process(_copy(tmp_path, "f.xml", data), project="DC", cls="PC", file_type="XML", source="t", store=store)
    assert result.status is FileStatus.COMPLETED and result.rows_landed == 10
    [row] = _drift_rows(store)
    assert row["observed"]["new"] == [f"{ROOT}/extraBranch", f"{ROOT}/extraBranch/x"]
    assert row["file_id"] == result.file_id and row["applied"] is False


def test_a_signature_without_a_tree_is_backfilled_on_its_next_hit(tmp_path):
    store, sid = _bound_store()
    store.state.registry[sid].observed_schema.pop("tree")
    data = fixture("formulary_dc.xml").read_bytes() + b"\n<!-- backfill -->\n"
    result = process(_copy(tmp_path, "f.xml", data), project="DC", cls="PC", file_type="XML", source="t", store=store)
    assert result.status is FileStatus.COMPLETED
    assert store.state.registry[sid].observed_schema["tree"]["root"] == "prescriptionDrugTemplateVO"
    assert _drift_rows(store) == []
```

`store.state.decisions` entries are dicts with the keys `file_id`, `action_type`, `observed`, `proposed`, `applied`
and `approval` (see `memory_store.py`, `decision()`).

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_tree_pipeline.py -v`
Expected:
- `test_a_hit_with_a_new_branch...` fails: no `tree_drift` row.
- `test_a_signature_without_a_tree...` fails: no tree after the hit.

The other two pass already (Task 7 stores the tree on a miss and at registration).

- [ ] **Step 3: Add `set_observed` to the registry Protocol and both stores**

`src/importer/repositories.py`, in `SignatureRegistry`:

```python
    def set_observed(self, signature_id: int, observed: dict) -> None: ...
```

`src/importer/memory_store.py`, in `MemoryRegistry` (after `set_proposal`):

```python
    def set_observed(self, signature_id, observed):
        self._row(signature_id).observed_schema = copy.deepcopy(observed)
```

`src/importer/mssql_store.py`, in `MssqlRegistry` (after `set_proposal`):

```python
    def set_observed(self, signature_id, observed):
        self.c.execute(text("UPDATE dbo.signature_registry SET observed_schema = :o WHERE signature_id = :id"),
                       {"o": _dump(observed), "id": signature_id})
```

- [ ] **Step 4: Check the tree on every XML hit, in `src/importer/pipeline.py`**

Add `from .trees import drift`. In `execute`, pass the signature through:

```python
            return self._land(doc, hit, sig)
```

Change `_land`'s signature to `def _land(self, doc, hit, sig) -> FileResult:`, and replace its first line with:

```python
        def detected(tx):
            tx.ledger.set_signature(self.file_id, hit.signature_id)
            self._check_tree(tx, sig, hit)

        self._step(FileStatus.DETECTED, detected)
```

Add the method to `_Run`, after `_quarantine`:

```python
    def _check_tree(self, tx, sig, hit) -> None:
        """XML only (§8.24): store the nesting tree the first time a signature is hit without one,
        otherwise record structural drift. Never fails or holds the file."""
        current = sig.observed.get("tree")
        if current is None:
            return
        observed = tx.registry.get(hit.signature_id).observed_schema or {}
        stored = observed.get("tree")
        if stored is None:
            tx.registry.set_observed(hit.signature_id, {**observed, **sig.observed})
            return
        changes = drift(stored, current)
        if changes:
            tx.audit.decision(self.file_id, "tree_drift", observed=changes, proposed=None, applied=False, approval=None)
```

- [ ] **Step 5: Run the pipeline-related suites**

Run: `.venv/Scripts/python -m pytest tests/test_tree_pipeline.py tests/test_pipeline.py tests/test_memory_store.py tests/test_precheck_loader.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/importer/repositories.py src/importer/memory_store.py src/importer/mssql_store.py src/importer/pipeline.py tests/test_tree_pipeline.py
git commit -m "Report nesting-tree drift on XML hits and backfill missing trees

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Onboarding and MCP (`get_tree`, tree previews, prompt, reference)

**Files:**
- Modify: `src/importer/onboarding.py`, `src/importer/mcp_server.py`, `src/importer/mcp_reference.md`, `.claude/settings.json`
- Test: `tests/test_onboarding.py` (append), `tests/test_mcp_server.py` (update `TOOLS`, append)

**Interfaces:**
- Consumes:
  - from Task 2: `subtree`, `build_tree`, `tree_warnings`;
  - from Task 3: `tables_in`, `outline`;
  - from Task 6: `declarative_mapper`.
- Produces:
  - `onboarding.PROFILE_DEPTH = 4`
  - `onboarding.tree(store, signature_id, path=None, depth=4) -> dict`
  - previews of tree specs carry `tree_warnings: list[str]`, and `counts` lists every target (0 when empty);
  - the MCP tool `get_tree(signature_id, path=None, depth=4)`.

- [ ] **Step 1: Write the failing onboarding tests (append to `tests/test_onboarding.py`)**

Add `FORMULARY_TARGETS` to the existing `from tests.support import ...` line at the top of the file, then append:

```python
FORMULARY_SCHEMAS = "formulary_schemas.json"


def _formulary_waiting(tmp_path):
    store = MemoryStore()
    configure(store, "npvcs")
    with store.transaction() as tx:
        for address, columns in load_json(FORMULARY_SCHEMAS).items():
            register_schema(tx, address, columns, created_by="t")
    path = tmp_path / "f.xml"
    path.write_bytes(fixture("formulary_dc.xml").read_bytes())
    _park(store, path, project="ME", file_type="XML")
    [item] = onboarding.pending(store)
    return store, item["signature_id"]


def test_xml_profile_shows_a_depth_limited_tree_and_record_candidates(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    prof = onboarding.file_profile(store, sid)
    top = prof["profile"]["tree"]["tree"]
    assert top["path"] == "/prescriptionDrugTemplateVO"
    formulary_vo = top["children"][1]["children"][0]  # depth 3: root > formularyList > formularyVO
    assert formulary_vo["children"][3]["name"] == "formularyCostSharingTypeList"
    assert formulary_vo["children"][3]["children_omitted"] == 1  # depth 4 is the last level shown
    assert any(c["kind"] == "record" for c in prof["candidates"])


def test_tree_pages_a_branch(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    branch = onboarding.tree(store, sid, path="/prescriptionDrugTemplateVO/drugList", depth=2)
    assert branch["tree"]["children"][0]["name"] == "drugListVO"
    with pytest.raises(onboarding.OnboardingError, match="no element path"):
        onboarding.tree(store, sid, path="/nope")


def test_tree_preview_counts_every_target_and_warns(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    spec = load_json("formulary_tree_spec.json")
    spec["select"][1]["select"][1]["forEach"] = "x:drugVOList/x:drug"
    result = onboarding.preview_spec(store, sid, spec)
    assert result["counts"]["npvcs.xml.formulary_drug_list"] == 0
    assert result["counts"]["npvcs.xml.formulary_benefit_definition"] == 5
    assert any("x:drugVOList/x:drug" in w for w in result["tree_warnings"])
    good = onboarding.preview_spec(store, sid, load_json("formulary_tree_spec.json"))
    assert good["tree_warnings"] == [] and sum(good["counts"].values()) == 10


def test_tree_submit_shows_the_outline_and_prior_mappings_find_tree_targets(tmp_path):
    store, sid = _formulary_waiting(tmp_path)
    draft = onboarding.submit(store, sid, load_json("formulary_tree_spec.json"), [], by="t")
    assert "npvcs.xml.formulary_drug_list <- x:drugList/x:drugListVO / x:drugVOList/x:drugVO" in draft["review_table"]
    onboarding.accept(store, sid, draft["draft_version_id"], by="t")
    assert onboarding.prior_mappings(store, FORMULARY_TARGETS[2])[0]["spec"]["kind"] == "tree"
```

- [ ] **Step 2: Write the failing MCP tests**

In `tests/test_mcp_server.py`, add `"get_tree"` to `TOOLS`, then append:

```python
@pytest.mark.anyio
async def test_get_tree_returns_names_and_counts_only(tmp_path):
    store = MemoryStore()
    configure(store, "npvcs")
    path = tmp_path / "f.xml"
    path.write_bytes(fixture("formulary_dc.xml").read_bytes())
    process(path, project="ME", cls="PC", file_type="XML", source="t", store=store)
    async with Client(create_server(store, user="tony"), raise_exceptions=True) as client:
        sid = _json(await client.call_tool("list_pending", {}))["pending"][0]["signature_id"]
        tree = _json(await client.call_tool("get_tree", {"signature_id": sid, "depth": 2}))
        profile = await client.call_tool("get_file_profile", {"signature_id": sid})
    assert tree["root"] == "prescriptionDrugTemplateVO"
    seen = json.dumps(tree) + profile.content[0].text
    assert not [v for v in ("DCF001", "197361", "$10.00", "Preferred Brand") if v in seen]  # no values cross MCP


def test_the_prompt_and_reference_cover_tree_specs():
    from importer.mcp_server import ONBOARD_PROMPT

    assert "get_tree" in ONBOARD_PROMPT and '"kind": "tree"' in ONBOARD_PROMPT
    assert '"kind": "tree"' in spec_reference() and "forEach" in spec_reference()
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_onboarding.py tests/test_mcp_server.py -v`
Expected: the new tests FAIL (`AttributeError: module 'importer.onboarding' has no attribute 'tree'`, the
`TOOLS` mismatch, and missing prompt text).

- [ ] **Step 4: Implement in `src/importer/onboarding.py`**

1. **Imports:** add the lines below. Remove `DeclarativeMapper, parse_spec` from the `.mappers.spec` import, keeping
   `SpecError`.

```python
from .mappers.loading import declarative_mapper
from .mappers.tree import outline, tables_in
from .trees import build_tree, subtree, tree_warnings
```

2. Add `PROFILE_DEPTH = 4` below `OnboardingError`.

3. `_targets`:

```python
def _targets(definition: dict) -> list[str]:
    kind = definition.get("kind")
    if kind == "tree":
        return tables_in(definition)
    if kind not in ("xml", "tabular"):
        return []
    return [e["target"] for e in definition.get("extractions", []) if isinstance(e, dict) and "target" in e]
```

4. In `file_profile`, replace `result["profile"] = profile(_document(trigger))` with:

```python
            prof = profile(_document(trigger))
            if prof.get("kind") == "xml":
                prof["tree"] = subtree(prof["tree"], depth=PROFILE_DEPTH)
            result["profile"] = prof
```

5. Add `tree()` after `file_profile`:

```python
def tree(store, signature_id: int, path: str | None = None, depth: int = PROFILE_DEPTH) -> dict:
    """A signature's nesting tree (names, counts, repeats, attribute names; never values), paged by path/depth."""
    with store.transaction() as tx:
        try:
            row = tx.registry.get(signature_id)
        except NotFound:
            raise OnboardingError(f"no signature {signature_id}") from None
        trigger = _trigger(tx, signature_id)
    stored = (row.observed_schema or {}).get("tree")
    if stored is None:
        if trigger is None:
            raise OnboardingError(f"signature {signature_id} has no stored tree and no file to profile")
        doc = _document(trigger)
        if not isinstance(doc, XmlDocument):
            raise OnboardingError(f"signature {signature_id} is not an XML signature")
        stored = build_tree(doc)
    try:
        return subtree(stored, path, max(1, int(depth)))
    except KeyError as exc:
        raise OnboardingError(str(exc.args[0])) from None
```

6. Replace `_preview` and `_preview_inputs`:

```python
def _preview(trigger: LedgerRow, signature_id: int, spec, schemas: dict[str, list[str]], max_chars: int,
             stored_tree: dict | None = None) -> dict:
    if not isinstance(spec, dict):
        return {"errors": ["spec must be a JSON object"]}
    if spec.get("kind") == "plugin":
        return {"errors": ["plugin mappers cannot be submitted through onboarding (design §8.22); use a declarative spec"]}
    try:
        mapper = declarative_mapper(spec, known_schemas=set(schemas))
        ctx = RunContext(trigger.file_id, signature_id, 0, trigger.request_context or {},
                         {t: schemas[t] for t in mapper.targets})
        doc = _document(trigger)
        result = preview(mapper, doc, ctx, n=SAMPLE_ROWS)
    except (SpecError, locators.LocatorError, ReaderError) as exc:
        return {"errors": [str(exc)]}
    out = {
        "file_id": trigger.file_id, "counts": result.counts, "extras_keys": result.extras_keys,
        "rejects": result.rejects,
        "sample": {t: [mask_row(row) for row in rows[:SAMPLE_ROWS]] for t, rows in result.sample.items()},
    }
    if spec.get("kind") == "tree":
        out["counts"] = {t: result.counts.get(t, 0) for t in mapper.targets}
        tree_doc = stored_tree or (build_tree(doc) if isinstance(doc, XmlDocument) else None)
        out["tree_warnings"] = tree_warnings(tree_doc, spec) if tree_doc else []
    return out


def _preview_inputs(tx, signature_id: int):
    trigger = _trigger(tx, signature_id)
    schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
    stored_tree = (tx.registry.get(signature_id).observed_schema or {}).get("tree")
    return trigger, schemas, load_settings(tx.artifacts).max_chars, stored_tree
```

Update both callers to unpack and pass four values:

```python
        trigger, schemas, max_chars, stored_tree = _preview_inputs(tx, signature_id)
    ...
    return _preview(trigger, signature_id, spec, schemas, max_chars, stored_tree)
```

In `submit`, do the same: `result = _preview(trigger, signature_id, spec, schemas, max_chars, stored_tree)`.

7. `review_table`: append the outline for tree specs, just before `return`:

```python
    if spec.get("kind") == "tree":
        lines += ["", "Tables:"] + [f"- {t['table']} <- {t['path']} (carries: {', '.join(t['carries']) or 'nothing'})"
                                    for t in outline(spec)]
        if result.get("tree_warnings"):
            lines += ["", "Tree warnings:"] + [f"- {w}" for w in result["tree_warnings"]]
```

- [ ] **Step 5: Implement in `src/importer/mcp_server.py`**

Change the `get_file_profile` docstring to:

```python
        """A waiting file's structure: columns, or for XML its nesting tree (element names, counts, repeats and
        attribute names, top 4 levels; never values), plus candidates and the suggested draft."""
```

Add the tool after `get_file_profile`:

```python
    @mcp.tool(annotations=READ_ONLY)
    def get_tree(signature_id: int, path: str | None = None, depth: int = 4) -> str:
        """An XML signature's nesting tree: element names, counts, repeat ranges, attribute names, never values.
        Pass a node's `path` to expand a branch whose children were omitted."""
        return run("get_tree", {"signature_id": signature_id, "path": path, "depth": depth},
                   lambda: onboarding.tree(store, signature_id, path=path, depth=depth))
```

In `ONBOARD_PROMPT`, replace step 3 with:

```
3. Otherwise gather evidence: get_prior_mappings for each candidate schema (strongest evidence), search_schemas
   when the candidates are weak, and get_samples only for columns whose names alone do not settle the mapping.
   Read get_spec_reference, then draft a declarative spec. Never use "kind": "plugin".
   For XML, write a "kind": "tree" spec that mirrors the nesting tree (get_file_profile shows the top levels;
   get_tree expands a branch): one table node per record node (the "record" candidates say which schema each
   node probably feeds), keys carried down with "column" on ancestor nodes, and never combine sibling datasets.
   Fix every tree_warnings entry before you submit.
```

- [ ] **Step 6: Document the tree kind in `src/importer/mcp_reference.md`**

Insert this section after the `## XML files: "kind": "xml"` section and before `## Special sources`:

````markdown
## Nested XML: `"kind": "tree"` (preferred for nested templates)

The spec mirrors the file's nesting tree (`get_tree`). The spec object is the root node, whose context is the
document root. Each node in `select` has a `forEach` (XPath relative to its parent node's element) and may have:

- `table`: a registered schema address; one row per matched element.
- `column`: `[{"name": ..., "path": ...}]`, where `path` is XPath relative to this node's element (attributes with
  `@name`) or a special source. **Columns are carried down** to every table below the node, so write header values
  and keys once, where they live. The nearer node wins on a name clash.
- `leaves: "auto"` (table nodes): every leaf child whose normalized name matches a schema column lands there.
- `unmapped` (`"extras"` default, or `"ignore"`), `unpivot` (as in `xml`), `document: "<column>"` (the whole element
  as XML text, to shred downstream).
- `select`: child nodes.

```json
{"kind": "tree",
 "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
 "value_child": "x:cellValue",
 "column": [{"name": "fileid", "path": "$file_id"},
            {"name": "templateVersion", "path": "x:header/x:templateVersion"}],
 "select": [
   {"forEach": "x:drugList/x:drugListVO",
    "column": [{"name": "drug_list_id", "path": "x:drugListID"}],
    "select": [
      {"forEach": "x:drugTierLevelList/x:drugTierLevelVO", "table": "npvcs.xml.formulary_tier_definition",
       "column": [{"name": "tier_level", "path": "x:drugTierLevel"}, {"name": "tier_type", "path": "x:drugTierType"}]},
      {"forEach": "x:drugVOList/x:drugVO", "table": "npvcs.xml.formulary_drug_list",
       "column": [{"name": "rxcui", "path": "x:rXCUI"}, {"name": "tier_level", "path": "x:tier"}]}]}]}
```

Rules:

- **Represent the file, not a report.** One table per record node. Sibling nodes never combine: the importer never
  joins (drugs × cost shares is a downstream join on the keys each table carries).
- **Specials** for `path`: `$file_id`, `$context.<key>`, `$identity` (this element's identity; carry it down to link
  children to their parent), and `$name` (this element's name, for elements whose name is the data).
- `$row_identity` is accepted as an alias of `$identity` on table nodes.
- `preview_mapping` returns `tree_warnings` (paths that cannot occur in the stored tree) and a count for every table,
  0 included. Fix the warnings before submitting.
````

In `## Special sources`, append: `Tree specs add `$identity` and `$name` (see above); they do not use `$parent`.`

- [ ] **Step 7: Allow the read-only tool**

In `.claude/settings.json`, in `permissions.allow`, add `"mcp__importer__get_tree"` after
`"mcp__importer__get_file_profile"`. Validate the file:

Run: `.venv/Scripts/python -c "import json; json.load(open('.claude/settings.json'))"`
Expected: no output (valid JSON)

- [ ] **Step 8: Run the suites**

Run: `.venv/Scripts/python -m pytest tests/test_onboarding.py tests/test_onboarding_accept.py tests/test_mcp_server.py tests/test_masking.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/importer/onboarding.py src/importer/mcp_server.py src/importer/mcp_reference.md .claude/settings.json tests/test_onboarding.py tests/test_mcp_server.py
git commit -m "Onboarding and MCP for tree specs: get_tree, tree warnings, zero counts, review outline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Full unit run and DEV integration

**Files:**
- Create: `tests/integration/test_m2c1.py`

**Interfaces:**
- Consumes: everything above; the `mssql_store` fixture (`tests/integration/conftest.py`).

- [ ] **Step 1: Run the whole unit suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all PASS (about 320 tests: 275 before, plus this milestone's)

- [ ] **Step 2: Write the integration tests**

`tests/integration/test_m2c1.py`:

```python
"""M2c-1 on DEV: tree specs end to end (design §8.24). Writes only to the control database."""

from __future__ import annotations

import json
import uuid

import pytest

from importer import onboarding, settings
from importer.admin import register_mapper, register_schema, register_signature
from importer.db import load_connection_document
from importer.models import Declared, FileStatus
from importer.pipeline import process
from tests.support import fixture, load_json

pytestmark = pytest.mark.integration

STAND_IN = "import_agent_dev.it_m2c1."  # benefits land in a stand-in namespace, never in the NPVCS addresses


def _copy(tmp_path, name: str) -> object:
    path = tmp_path / name
    path.write_bytes(fixture(name).read_bytes() + f"\n<!-- {uuid.uuid4()} -->\n".encode())
    return path


def test_formulary_tree_spec_lands_three_tables(mssql_store, tmp_path):
    project = f"T{uuid.uuid4().hex[:6].upper()}"
    path = _copy(tmp_path, "formulary_dc.xml")
    with mssql_store.transaction() as tx:
        for address, columns in load_json("formulary_schemas.json").items():
            register_schema(tx, address, columns, created_by="it")
        mapper = register_mapper(tx, f"formulary_tree_{project}", load_json("formulary_tree_spec.json"), created_by="it")
        register_signature(tx, path.read_bytes(), Declared.normalize(project, "PC", "XML"), mapper.version_id,
                           approved_by="it")
    result = process(path, project=project, cls="PC", file_type="XML", source="it", store=mssql_store)
    assert result.status is FileStatus.COMPLETED and result.rows_landed == 10 and float(result.percent_loaded) == 100.0


def test_benefits_onboarded_with_a_tree_spec(mssql_store, tmp_path):
    doc = load_connection_document()
    with mssql_store.transaction() as tx:
        settings.set_destination(tx, "IMPORT_AGENT_DEV", server=doc["server"], database=doc["database"], by="it")
        for address, columns in load_json("benefits_schemas.json").items():
            register_schema(tx, address.replace("npvcs.xml.", STAND_IN), columns, created_by="it")
    spec = json.loads(json.dumps(load_json("benefits_tree_spec.json")).replace("npvcs.xml.", STAND_IN))
    project = f"B{uuid.uuid4().hex[:6].upper()}"
    parked = process(_copy(tmp_path, "benefits_sample.xml"), project=project, cls="PC", file_type="XML",
                     source="it", store=mssql_store)
    assert parked.status is FileStatus.QUARANTINED
    item = next(p for p in onboarding.pending(mssql_store) if p["fingerprint"].startswith(f"{project}|"))
    preview = onboarding.preview_spec(mssql_store, item["signature_id"], spec)
    assert preview["tree_warnings"] == [] and sum(preview["counts"].values()) == 35
    draft = onboarding.submit(mssql_store, item["signature_id"], spec, [], by="it")
    result = onboarding.accept(mssql_store, item["signature_id"], draft["draft_version_id"], by="it")
    [landed] = result["files"]
    assert landed["status"] == "completed" and landed["rows_landed"] == 35
    with mssql_store.transaction() as tx:
        assert tx.raw.count_landed(parked.file_id, f"{STAND_IN}mooplist") == 5
```

- [ ] **Step 3: Run the integration tests on DEV**

Run: `.venv/Scripts/python -m pytest -m integration tests/integration -q`
Expected: all PASS (14 before, plus 2)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_m2c1.py
git commit -m "M2c-1 integration: formulary and benefits tree specs on DEV

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Documentation, DDL comments and Confluence

**Files:**
- Modify: `sql/001_schema.sql`, `CLAUDE.md`, `docs/mapping-manual.md`, `docs/architecture/README.md`
- Confluence (Atlassian MCP):
  - Schema DDL page 3775135746;
  - Design Decisions page 3774676996 (§8.22 pointer and a new §8.24);
  - Blueprint 3774775300;
  - project page 3773956110.

- [ ] **Step 1: DDL comments only** (`sql/001_schema.sql`)

- `observed_schema` line comment → `-- JSON: header / sheets / keys / XML root, namespace and nesting tree (§8.24)`
- In the `decision_log.action_type` comment, after `draft_mapping | reject_mapping |`, add `tree_drift |`.

Then update the Schema DDL Confluence page so it mirrors the file (the same two comment edits).

- [ ] **Step 2: `CLAUDE.md`**

- **Repo layout:**
  - add `trees.py   Nesting trees of XML documents (names and counts, never values)`;
  - change `mappers/` to `Mapper contract, declarative specs (tabular / xml / tree), loading (hash-pinned plugins),
    preview`.
- **`README.md`:** the repo-layout block gets the same two lines (`trees.py`, and `mappers/` naming the three
  declarative kinds).
- **Current status:** replace the "Next:" sentence with: `M2c-1 (nesting trees for XML, the "tree" spec kind) is
  implemented; live acceptance pending. Next: M2c-2 (NDJSON + FHIR), then M2c-3 (plugins), then M3+ per the
  Blueprint.`

- [ ] **Step 3: `docs/mapping-manual.md`**

In §3 (Spec reference), insert the same `### Nested XML: "kind": "tree"` section as `mcp_reference.md`, after
`### XML files`. Add one line to §2: `For nested XML, start from the tree: \`importer pending\` names the signature;
the MCP \`get_tree\` tool (or the profile in \`get_file_profile\`) shows the structure.`

- [ ] **Step 4: `docs/architecture/README.md`**

Change the Mapping layer row to: `| Mapping | \`mappers\`, \`shapers\`, \`readers\`, \`trees\` | Six format readers;
declarative specs (tabular / xml / tree), reviewed Excel shapers, hash-pinned plugins; nesting trees |`.

Re-check the layer rule ("each layer imports only the layers below it"). `trees` imports only `readers`. Its
importers must all sit in Mapping or above:

Run: `git grep -n "from .trees\|from ..trees\|from .tree import\|from .mappers.tree" src/importer`
Expected:
- matches only in `fingerprint.py`, `profiler.py` and `pipeline.py` (Fast path), `onboarding.py` (Onboarding) and
  `mappers/loading.py` (Mapping);
- no match in Settings & safeguards, Stores or Foundation modules.

- [ ] **Step 5: Run the full suite once more, then commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all PASS

```bash
git add sql/001_schema.sql CLAUDE.md README.md docs/mapping-manual.md docs/architecture/README.md
git commit -m "Docs for M2c-1: tree spec chapter, layout, DDL comments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Confluence**

- **Design Decisions:**
  - Add `### 8.24 Nesting trees and the tree spec kind (DECIDED — M2c-1)`, summarising spec §2 (the six decisions)
    and §5.3 (semantics).
  - In §8.22, strike through "Until then the few complex nested files keep going through the existing legacy
    parsers" and add after it: **Evolved (8.24):** nested XML is onboarded with tree specs; JSON/FHIR follows in
    M2c-2 and plugins in M2c-3.
- **Blueprint:** split M2c into M2c-1, M2c-2 and M2c-3 with their acceptance lines (spec §11, §13).
- **Project page:** tick the M2c-1 tasks, and add M2c-2 / M2c-3 as open items.

---

### Task 12: Live acceptance on DEV (needs the user)

**Files:** none (results recorded in Design Decisions §8.24 and the spec).

- [ ] **Step 1: Ask the user for two real files**

Ask for one SERFF formulary (prescription drug) template and one plan-benefit template from the catalog, each with
its project, class, `file_id` and `serffbindername`. Never open them directly; the data guard stays on.

- [ ] **Step 2: Park them**

```bash
importer process "<formulary locator>" --project <P> --class PC --type XML --context serffbindername=<...> --context run_id=<...>
importer process "<benefits locator>" --project <P> --class PC --type XML --context serffbindername=<...> --context run_id=<...>
```

If a signature is already bound for that project, the file lands directly. Then skip to Step 4 for it.

- [ ] **Step 3: Onboard through MCP**

The user runs `/mcp__importer__onboard`. The agent:
1. reads the tree;
2. drafts a tree spec;
3. previews it;
4. clears the `tree_warnings`;
5. submits it.

The user approves `accept_mapping`, and the files land.

- [ ] **Step 4: Compare counts with the legacy tables (counts only)**

For each target table, compare `SELECT COUNT(*)` in the raw table for the ledger `file_id` with
`SELECT COUNT(*)` in the legacy NPVCS table `WHERE fileid = <catalog file id>`. Read the legacy side only through the
read-only destination engine (`db.engine_for`, SELECT only). Record both numbers per table, and explain each
difference (for example, legacy joins or dropped rows).

- [ ] **Step 5: Record**

Write the results into Design Decisions §8.24 ("Acceptance") and into a `## 15. Acceptance results` section of the
spec. Then commit:

```bash
git add docs/superpowers/specs/2026-10-08-m2c1-nesting-trees-design.md
git commit -m "M2c-1 live acceptance results

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
