from __future__ import annotations

import pytest

from importer.admin import SchemaRefused, register_mapper, register_schema, register_signature
from importer.mappers.loading import MapperLoadError, load_mapper
from importer.mappers.preview import preview
from importer.memory_store import MemoryStore
from importer.models import Declared
from importer.readers import parse_document
from tests.support import EVENT_SCHEMA, URL_SCHEMA, URL_SPEC_A, URL_TARGET, configure, ctx, fixture, load_json

PLUGIN = '''
from importer.mappers.base import Mapper
from importer.models import LandRow


class Echo(Mapper):
    version = 1
    targets = ("app_db.dbo.event",)

    def extract(self, doc, ctx):
        for record in doc.table("*").records:
            yield LandRow("app_db.dbo.event", record.row_identity, {"id": record.values.get("id")})
'''


def _schemas(tx, schemas):
    from importer import settings
    for name in sorted({key.split(".")[0] for key in schemas}):
        if settings.load(tx.artifacts).destination(name) is None:
            settings.set_destination(tx, name, server="test", database=name.upper(), by="test")
    for schema_id, columns in schemas.items():
        register_schema(tx, schema_id, columns, created_by="test")


def test_load_declarative_formulary_mapper():
    store = MemoryStore()
    with store.transaction() as tx:
        _schemas(tx, load_json("formulary_schemas.json"))
        row = register_mapper(tx, "formulary", load_json("formulary_spec.json"), created_by="test")
        mapper = load_mapper(row, known_schemas=set(load_json("formulary_schemas.json")))
    assert mapper.targets == ("app_db.xml.formulary_benefit_definition", "app_db.xml.formulary_tier_definition", "app_db.xml.formulary_drug_list")
    assert mapper.version == row.version_no


def test_register_mapper_rejects_unknown_target_and_writes_nothing():
    store = MemoryStore()
    with pytest.raises(MapperLoadError) as err:
        with store.transaction() as tx:
            register_mapper(tx, "urls", URL_SPEC_A, created_by="test")
    assert err.value.reason_code == "invalid_spec"
    assert store.state.artifacts == {}


def test_unknown_kind():
    store = MemoryStore()
    with pytest.raises(MapperLoadError) as err:
        with store.transaction() as tx:
            register_mapper(tx, "x", {"kind": "magic"}, created_by="test")
    assert err.value.reason_code == "unknown_mapper_kind"


def test_plugin_hash_is_pinned_and_checked(tmp_path):
    (tmp_path / "echo.py").write_text(PLUGIN, encoding="utf-8")
    store = MemoryStore()
    with store.transaction() as tx:
        _schemas(tx, EVENT_SCHEMA)
        row = register_mapper(tx, "echo", {"kind": "plugin", "ref": "echo:Echo", "version": 1}, created_by="test", plugins_dir=tmp_path)
    assert len(row.definition["sha256"]) == 64
    mapper = load_mapper(row, known_schemas={"app_db.dbo.event"}, plugins_dir=tmp_path)
    rows = list(mapper.extract(parse_document(b"id\n1\n", "CSV"), ctx(EVENT_SCHEMA)))
    assert rows[0].fields == {"id": "1"}

    (tmp_path / "echo.py").write_text(PLUGIN + "\n# edited in place\n", encoding="utf-8")
    with pytest.raises(MapperLoadError) as err:
        load_mapper(row, known_schemas={"app_db.dbo.event"}, plugins_dir=tmp_path)
    assert err.value.reason_code == "plugin_hash_mismatch"


def test_plugin_version_must_match(tmp_path):
    (tmp_path / "echo.py").write_text(PLUGIN, encoding="utf-8")
    store = MemoryStore()
    with pytest.raises(MapperLoadError) as err:
        with store.transaction() as tx:
            _schemas(tx, EVENT_SCHEMA)
            register_mapper(tx, "echo", {"kind": "plugin", "ref": "echo:Echo", "version": 2}, created_by="test", plugins_dir=tmp_path)
    assert err.value.reason_code == "plugin_version_mismatch"


def test_register_schema_validates_columns_and_address():
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        with pytest.raises(ValueError, match="reserved"):
            register_schema(tx, "app_db.dbo.s", ["_file_id"], created_by="t")
        with pytest.raises(ValueError, match="duplicate"):
            register_schema(tx, "app_db.dbo.s", ["a", "A"], created_by="t")
        with pytest.raises(ValueError, match="must match"):
            register_schema(tx, "app_db.dbo.s", ["bad col"], created_by="t")
        with pytest.raises(ValueError, match="DATABASE.SCHEMA.TABLE"):
            register_schema(tx, "s", ["a"], created_by="t")
        with pytest.raises(SchemaRefused, match="not a configured destination"):
            register_schema(tx, "other_db.dbo.s", ["a"], created_by="t")


def test_register_schema_outcomes_added_unchanged_changed():
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        first = register_schema(tx, "APP_DB.relational.URL_Template", ["urlid", "newurl"], created_by="t", source="live")
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


def test_register_signature_activates_lookup():
    store = MemoryStore()
    declared = Declared.normalize("ME", "PC", "CSV")
    data = fixture("url_template_a.csv").read_bytes()
    with store.transaction() as tx:
        _schemas(tx, URL_SCHEMA)
        row = register_mapper(tx, "urls_a", URL_SPEC_A, created_by="test")
        sid = register_signature(tx, data, declared, row.version_id, approved_by="test")
        fp = tx.registry.get(sid).fingerprint
        assert tx.registry.lookup(fp, None).mapper_version_id == row.version_id


def test_preview_counts_and_samples():
    store = MemoryStore()
    with store.transaction() as tx:
        _schemas(tx, URL_SCHEMA)
        row = register_mapper(tx, "urls_a", URL_SPEC_A, created_by="test")
    mapper = load_mapper(row, known_schemas={"app_db.relational.url_template"})
    result = preview(mapper, parse_document(fixture("url_template_a.csv").read_bytes(), "CSV"), ctx(URL_SCHEMA), n=1)
    data = result.to_dict()
    assert data["counts"] == {"app_db.relational.url_template": 2}
    assert len(data["sample"]["app_db.relational.url_template"]) == 1
    assert data["sample"]["app_db.relational.url_template"][0]["row_identity"] == "L2"
    assert data["rejects"] == {}


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
        load_mapper(row, known_schemas={"app_db.xml.formulary_drug_list"})
    with pytest.raises(SpecError, match="kind must be one of"):
        declarative_mapper({"kind": "yaml"}, known_schemas=None)
