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


NS_DOC = '<r xmlns="urn:t"><a id="1"><v>1</v></a><a id="2"><v>2</v></a></r>'


def _ns_spec(namespaces, for_each, column_path="x:v"):
    spec = {"kind": "tree", "select": [{"forEach": for_each, "table": "d.s.a",
                                        "column": [{"name": "v", "path": column_path}]}]}
    if namespaces is not None:
        spec["namespaces"] = namespaces
    return spec


@pytest.mark.parametrize("namespaces, for_each, column_path, needle", [
    ({"x": "urn:wrong"}, "x:a", "x:v", "urn:t"),  # prefix bound to the wrong URI
    ({"x": "urn:t"}, "x:a", "v", "'v'"),          # unprefixed column step in a namespaced document
    ({"x": "urn:t"}, "a", "x:v", "urn:t"),        # unprefixed forEach
])
def test_tree_warnings_flag_namespace_mismatches(namespaces, for_each, column_path, needle):
    warnings = tree_warnings(build_tree(_doc(NS_DOC)), _ns_spec(namespaces, for_each, column_path))
    assert warnings and any(needle in w for w in warnings)


def test_tree_warnings_accept_correct_prefixes():
    assert tree_warnings(build_tree(_doc(NS_DOC)), _ns_spec({"x": "urn:t"}, "x:a")) == []
    benefits = build_tree(_doc(fixture("benefits_sample.xml").read_bytes()))
    assert tree_warnings(benefits, load_json("benefits_tree_spec.json")) == []


def test_tree_warnings_flag_a_namespace_below_an_unnamespaced_root():
    tree = build_tree(_doc('<r><a xmlns="urn:x"><b>1</b></a></r>'))
    spec = {"kind": "tree", "select": [{"forEach": "a", "table": "d.s.a", "column": [{"name": "b", "path": "b"}]}]}
    warnings = tree_warnings(tree, spec)
    assert warnings and "urn:x" in warnings[0]
