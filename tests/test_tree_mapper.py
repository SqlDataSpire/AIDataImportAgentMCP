from __future__ import annotations

import pytest

from importer.mappers.spec import DeclarativeMapper, SpecError, parse_spec
from importer.mappers.tree import TreeMapper, parse_tree_spec
from importer.models import LandRow, RejectRow
from importer.readers import parse_document
from tests.support import ctx, fixture, load_json

REQUEST = {"binder_name": "ABCD-DC25-1", "run_id": "42"}


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
    assert mapper.targets == ("app_db.xml.formulary_benefit_definition", "app_db.xml.formulary_tier_definition",
                              "app_db.xml.formulary_drug_list")



BENEFIT_COUNTS = {
    "app_db.xml.package": 2, "app_db.xml.benefits": 3, "app_db.xml.planattributes": 2, "app_db.xml.costsharevariance": 3,
    "app_db.xml.sbc": 3, "app_db.xml.hsa": 3, "app_db.xml.url": 3, "app_db.xml.avcalculator": 3,
    "app_db.xml.mooplist": 5, "app_db.xml.plandeductiblelist": 3, "app_db.xml.servicevisitlist": 5,
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
    package = next(r for r in _benefit_rows() if r.target == "app_db.xml.package")
    assert package.row_identity == "/packagesList/packages[1]/header"
    assert package.fields == {
        "fileid": "7", "binder_name": "ABCD-DC25-1", "run_id": "42", "packageid": "/packagesList/packages[1]",
        "packagename": "B2-1", "templateversion": "v13.0", "issuerid": "11111", "statepostalcode": "DC",
        "dentalplanonlyind": "No", "marketcoverage": "Individual",
    }
    assert package.extras is None


def test_benefit_children_carry_their_parent_keys():
    rows = _benefit_rows()
    variances = [r for r in rows if r.target == "app_db.xml.costsharevariance"]
    assert variances[0].fields["costsharevarianceid"] == variances[0].row_identity
    assert variances[0].fields["planid"] == "11111DC0010001-01" and variances[0].extras is None
    moops = [r for r in rows if r.target == "app_db.xml.mooplist"]
    assert moops[4].fields["costsharevarianceid"] == variances[2].row_identity
    assert moops[4].extras == {"packageid": "/packagesList/packages[2]"}  # carried key the table lacks: lossless


def test_repeated_unmatched_leaves_all_land_in_extras():
    defn = {"kind": "tree", "select": [{"forEach": "p", "table": "d.s.p"}]}
    [row] = _run(defn, "<r><p><v>1</v><v>2</v><v>3</v></p></r>", {"d.s.p": ["x"]})
    assert row.fields == {} and row.extras == {"v": ["1", "2", "3"]}


def test_repeated_auto_leaves_land_the_first_and_keep_all_in_extras():
    defn = {"kind": "tree", "select": [{"forEach": "p", "table": "d.s.p", "leaves": "auto"}]}
    [row] = _run(defn, "<r><p><v>1</v><v>2</v><v>3</v></p></r>", {"d.s.p": ["v"]})
    assert row.fields == {"v": "1"} and row.extras == {"v": ["1", "2", "3"]}


def test_a_leaf_named_like_a_carried_extra_is_kept_under_a_relative_key():
    defn = {"kind": "tree", "column": [{"name": "k", "path": "k"}], "select": [{"forEach": "p", "table": "d.s.p"}]}
    [row] = _run(defn, "<r><k>K</k><p><k>child</k></p></r>", {"d.s.p": ["x"]})
    assert row.extras == {"k": "K", "./k": "child"}


def test_for_each_on_comments_or_processing_instructions_skips_them():
    xml = '<r xmlns="urn:t"><a><!-- c --><?pi x?><v>1</v></a></r>'
    nodes = {"kind": "tree", "namespaces": {"x": "urn:t"},
             "select": [{"forEach": "x:a/node()", "table": "d.s.a", "column": [{"name": "n", "path": "$name"}]}]}
    assert [r.fields["n"] for r in _run(nodes, xml, {"d.s.a": ["n"]})] == ["v"]
    comments = {"kind": "tree", "namespaces": {"x": "urn:t"}, "select": [{"forEach": "x:a/comment()", "table": "d.s.a"}]}
    assert _run(comments, xml, {"d.s.a": ["n"]}) == []
    unpivot = {"kind": "tree", "namespaces": {"x": "urn:t"}, "select": [
        {"forEach": "x:a", "table": "d.s.a", "unpivot": {"over": "node()", "name_column": "n", "value_column": "v"}}]}
    assert [r.fields["n"] for r in _run(unpivot, xml, {"d.s.a": ["n", "v"]})] == ["v"]


def test_row_identities_match_element_identity_exactly():
    from importer.readers.xml_reader import element_identity

    xml = ('<r xmlns="urn:t"><g><a/><b/><a/><!-- c --><c><a/></c></g><g><a/></g>'
           '<h xmlns="urn:o"><a/></h><a xmlns="urn:o"/><a/></r>')
    defn = {"kind": "tree", "namespaces": {"x": "urn:t", "o": "urn:o"}, "select": [
        {"forEach": ".//*", "table": "d.s.all", "column": [{"name": "me", "path": "$identity"}]}]}
    doc = parse_document(xml.encode(), "XML")
    rows = list(TreeMapper(parse_tree_spec(defn)).extract(doc, ctx({"d.s.all": ["me"]})))
    expected = [element_identity(el, doc.root) for el in doc.root.iter() if isinstance(el.tag, str) and el is not doc.root]
    assert [r.row_identity for r in rows] == expected and len(rows) == 12


def test_many_siblings_are_walked_in_linear_time():
    import time

    xml = "<r><l>" + "<d><v>1</v></d>" * 20000 + "</l></r>"
    defn = {"kind": "tree", "select": [{"forEach": "l/d", "table": "d.s.d", "leaves": "auto"}]}
    start = time.perf_counter()
    rows = _run(defn, xml, {"d.s.d": ["v"]})
    assert len(rows) == 20000 and rows[-1].row_identity == "/l/d[20000]"
    assert time.perf_counter() - start < 5
