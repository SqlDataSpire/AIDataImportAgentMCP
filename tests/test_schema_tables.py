from __future__ import annotations

from pathlib import Path

import pytest

from importer.addresses import parse
from importer.schema_tables import (
    REBUILD_DROP_SQL, add_column_sql, create_raw_sql, create_stage_sql, ensure_schema_sql, q, raw_name,
    stage_name, validate_columns,
)

DDL = (Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql").read_text(encoding="utf-8")
URL = parse("app_db.relational.url_template")


def test_names():
    assert raw_name(URL) == "[app_db_relational].[url_template]"
    assert stage_name(URL) == "[stage].[app_db_relational__url_template]"


def test_raw_table_has_lineage_canonical_columns_and_pk():
    sql = create_raw_sql(URL, ["urlid", "newurl"])
    assert sql.startswith("CREATE TABLE [app_db_relational].[url_template] (")
    for fragment in ("[_file_id] BIGINT NOT NULL", "[_row_identity] NVARCHAR(256) NOT NULL",
                     "[_extras] NVARCHAR(MAX) NULL", "[_landed_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()",
                     "[urlid] NVARCHAR(MAX) NULL",
                     "CONSTRAINT [pk_app_db_relational__url_template] PRIMARY KEY CLUSTERED ([_file_id], [_row_identity])"):
        assert fragment in sql


def test_stage_table_is_a_heap_without_landed_at():
    sql = create_stage_sql(URL, ["urlid"])
    assert sql.startswith("CREATE TABLE [stage].[app_db_relational__url_template] (")
    assert "PRIMARY KEY" not in sql and "_landed_at" not in sql


def test_add_column_sql():
    assert add_column_sql("raw", URL, "newcol") == "ALTER TABLE [app_db_relational].[url_template] ADD [newcol] NVARCHAR(MAX) NULL"
    assert add_column_sql("stage", URL, "newcol") == "ALTER TABLE [stage].[app_db_relational__url_template] ADD [newcol] NVARCHAR(MAX) NULL"
    with pytest.raises(ValueError):
        add_column_sql("dbo", URL, "x")


def test_ensure_schema_marks_importer_owned():
    sql = ensure_schema_sql("app_db_relational")
    assert "CREATE SCHEMA [app_db_relational]" in sql and "importer_owned" in sql
    with pytest.raises(ValueError):
        ensure_schema_sql("x];DROP TABLE y;--")


def test_rebuild_drops_by_mark_and_keeps_identity():
    assert "importer_owned" in REBUILD_DROP_SQL
    assert "database_identity" not in REBUILD_DROP_SQL


@pytest.mark.parametrize("name", ["bad name", "x];DROP TABLE y;--", ""])
def test_identifiers_are_validated(name):
    with pytest.raises(ValueError):
        q(name)


def test_columns_reject_reserved_and_duplicates():
    with pytest.raises(ValueError, match="reserved"):
        validate_columns(["_extras"])
    with pytest.raises(ValueError, match="duplicate"):
        validate_columns(["a", "A"])


@pytest.mark.parametrize("fragment", [
    "'schema','mapper','rule','config'", "artifact_key      NVARCHAR(128) NOT NULL", "content_sha256",
    "mapper_version_id BIGINT NULL REFERENCES dbo.artifact_versions(version_id)", "physical_part",
    "xml_namespace", "observed_schema", "[class]", "attempts          INT NOT NULL DEFAULT 1",
    "last_error", "request_context", "signature_id      BIGINT NULL REFERENCES dbo.signature_registry(signature_id)",
])
def test_control_plane_ddl_has_m2b_columns(fragment):
    assert fragment in DDL


def test_ddl_creates_referenced_tables_first():
    assert DDL.index("CREATE TABLE dbo.artifact_versions") < DDL.index("CREATE TABLE dbo.signature_registry") < DDL.index("CREATE TABLE dbo.file_ledger")
