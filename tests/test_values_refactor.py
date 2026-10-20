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
