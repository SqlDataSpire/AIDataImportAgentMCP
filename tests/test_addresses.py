from __future__ import annotations

import pytest

from importer.addresses import AddressError, collision, parse


def test_parse_lowercases_and_derives_physical_names():
    a = parse("APP_DB.relational.URL_Template")
    assert a.key == "app_db.relational.url_template"
    assert (a.raw_schema, a.table) == ("app_db_relational", "url_template")
    assert a.stage_table == "app_db_relational__url_template"
    assert a.pk_name == "pk_app_db_relational__url_template"


@pytest.mark.parametrize("text", ["app_db.url_template", "a.b.c.d", "", "app_db..t", "app-db.dbo.t", "app_db.dbo.my table"])
def test_malformed_addresses_are_refused(text):
    with pytest.raises(AddressError):
        parse(text)


def test_reserved_names():
    with pytest.raises(AddressError, match="reserved"):
        parse("db.datareader.t")          # raw schema db_datareader
    assert parse("sys.x.t").raw_schema == "sys_x"   # not itself reserved


def test_identifier_length_limit():
    with pytest.raises(AddressError, match="128"):
        parse(f"app_db.dbo.{'t' * 125}")   # stage name app_db_dbo__ttt... > 128


def test_collision_detects_same_raw_or_stage_table():
    assert collision(parse("a_b.c.t"), ["a.b_c.t"]) == "a.b_c.t"
    assert collision(parse("x.y.a__b"), ["x.y__a.b"]) == "x.y__a.b"   # same stage table
    assert collision(parse("app_db.dbo.t"), ["app_db.dbo.t", "event_db.dbo.t"]) is None
