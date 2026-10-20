from __future__ import annotations

import json

import pytest

from importer.memory_store import MemoryStore
from importer.schema_sources import SchemaSourceError, expand, import_schemas, read_column_file
from tests.support import configure


class FakeSource:
    def __init__(self, tables: dict[tuple[str, str], list[str]]):
        self._tables = tables

    def tables(self):
        return list(self._tables)

    def columns(self, schema, table):
        for (s, t), cols in self._tables.items():
            if (s.lower(), t.lower()) == (schema.lower(), table.lower()):
                return cols
        return []


SOURCE = FakeSource({
    ("relational", "url_template"): ["urlid", "newurl"],
    ("relational", "plans"): ["planid"],
    ("xml", "formulary_drug_list"): ["rxcui"],
    ("dbo", "Bad Name"): ["a"],
})


def test_expand_wildcards():
    assert expand("APP_DB.relational.url_template", SOURCE) == ["APP_DB.relational.url_template"]
    assert sorted(expand("APP_DB.relational.*", SOURCE)) == ["APP_DB.relational.plans", "APP_DB.relational.url_template"]
    assert len(expand("APP_DB.*.*", SOURCE)) == 4
    with pytest.raises(SchemaSourceError):
        expand("APP_DB.*", SOURCE)


def test_import_reports_added_unchanged_changed_refused():
    store = MemoryStore()
    configure(store, "app_db")
    first = import_schemas(store, "APP_DB.*.*", by="test_user", source=SOURCE)
    assert sorted(o["address"] for o in first["added"]) == [
        "app_db.relational.plans", "app_db.relational.url_template", "app_db.xml.formulary_drug_list"]
    assert [r["address"] for r in first["refused"]] == ["APP_DB.dbo.Bad Name"]
    again = import_schemas(store, "APP_DB.relational.*", by="test_user", source=SOURCE)
    assert len(again["unchanged"]) == 2 and again["added"] == []
    grown = FakeSource({("relational", "url_template"): ["urlid", "newurl", "issuerid"]})
    changed = import_schemas(store, "APP_DB.relational.url_template", by="test_user", source=grown)
    assert changed["changed"][0]["new_columns"] == ["issuerid"]
    assert [d["action_type"] for d in store.state.decisions if d["action_type"] == "schema_import"] == ["schema_import"] * 3


def test_import_from_columns_and_files(tmp_path):
    store = MemoryStore()
    configure(store, "geo_db")
    js = tmp_path / "cols.json"
    js.write_text(json.dumps(["zip", "city"]), encoding="utf-8")
    csv = tmp_path / "cols.csv"
    csv.write_text("zip\ncity\n\nstate\n", encoding="utf-8")
    assert read_column_file(js) == ["zip", "city"]
    assert read_column_file(csv) == ["zip", "city", "state"]
    report = import_schemas(store, "GEO_DB.dbo.zip_lookup", by="t", columns=read_column_file(csv))
    assert report["added"][0]["address"] == "geo_db.dbo.zip_lookup"
    with pytest.raises(SchemaSourceError, match="one explicit"):
        import_schemas(store, "GEO_DB.dbo.*", by="t", columns=["zip"])


def test_unconfigured_destination_is_refused_not_raised():
    store = MemoryStore()
    report = import_schemas(store, "EVENT_DB.dbo.drugs", by="t", columns=["ndc"])
    assert report["refused"][0]["reason"].startswith("event_db.dbo.drugs: database 'event_db' is not a configured destination")
