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
    assert first["table"] == "app_db.xml.formulary_benefit_definition"
    assert first["path"] == ("x:formularyList/x:formularyVO / x:formularyCostSharingTypeList/x:formularyCostSharingTypeVO"
                             " / x:costSharingTypeList/x:costSharingTypeVO")
    assert first["carries"][:3] == ["fileid", "binder_name", "run_id"] and "drug_tier_id" in first["carries"]
    assert copy.deepcopy(defn) == load_json("formulary_tree_spec.json")  # not mutated


@pytest.mark.parametrize("node", [
    {"forEach": "y:a", "table": "d.s.t"},
    {"forEach": "x:a", "table": "d.s.t", "column": [{"name": "k", "path": "y:k"}]},
])
def test_an_undeclared_namespace_prefix_is_a_spec_error(node):
    with pytest.raises(SpecError, match="invalid XPath"):
        parse_tree_spec({"kind": "tree", "namespaces": {"x": "urn:t"}, "select": [node]})
