"""M2b on the DEV SQL Server (design §8.20)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from importer import environment, onboarding, settings
from importer.admin import register_schema
from importer.db import engine_for, load_connection_document
from importer.models import FileStatus
from importer.pipeline import process
from importer.schema_sources import import_schemas, live_source
from tests.support import URL_SCHEMA, URL_SPEC_B, URL_TARGET, fixture

pytestmark = pytest.mark.integration


def test_identity_is_dev_and_unmarked_databases_are_refused(dev_identity):
    assert dev_identity.environment == "DEV" and environment.dev_guard("DEV", dev_identity.database) is None
    with engine_for(load_connection_document()["server"], "master").connect() as conn:
        master = environment.read(conn)
    assert environment.dev_guard(master.environment, master.database) is not None


def test_live_schema_import_from_a_stand_in_destination(mssql_store, dev_identity):
    uid = uuid.uuid4().hex[:8]
    doc = load_connection_document()
    with mssql_store.engine.begin() as conn:
        conn.exec_driver_sql("IF SCHEMA_ID(N'it_dest') IS NULL EXEC(N'CREATE SCHEMA it_dest')")
        conn.exec_driver_sql(f"CREATE TABLE it_dest.t_{uid} (a NVARCHAR(10), b INT)")
    try:
        with mssql_store.transaction() as tx:
            settings.set_destination(tx, "IMPORT_AGENT_DEV", server=doc["server"], database=doc["database"], by="it")
        report = import_schemas(mssql_store, f"IMPORT_AGENT_DEV.it_dest.t_{uid}", by="it",
                                source=live_source(mssql_store, "IMPORT_AGENT_DEV"))
        assert report["added"][0]["address"] == f"import_agent_dev.it_dest.t_{uid}"
        assert report["added"][0]["new_columns"] == ["a", "b"]
    finally:
        with mssql_store.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE it_dest.t_{uid}")


def test_onboarding_end_to_end_without_a_model(mssql_store, tmp_path):
    uid = uuid.uuid4().hex[:6].upper()
    with mssql_store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="it")
    path = tmp_path / "b.csv"
    path.write_text(fixture("url_template_b.csv").read_text() + f"B{uid},https://example.net/{uid},SBC\n", encoding="utf-8")
    project = f"IT{uid}"
    parked = process(path, project=project, cls="PC", file_type="CSV", source="it", store=mssql_store)
    assert parked.status is FileStatus.QUARANTINED
    item = next(p for p in onboarding.pending(mssql_store) if p["fingerprint"].startswith(f"{project}|"))
    draft = onboarding.submit(mssql_store, item["signature_id"], URL_SPEC_B, [], by="it")
    result = onboarding.accept(mssql_store, item["signature_id"], draft["draft_version_id"], by="it")
    assert [f["status"] for f in result["files"]] == ["completed"]
    with mssql_store.transaction() as tx:
        assert tx.raw.count_landed(parked.file_id, URL_TARGET) == 3


def test_locator_round_trip_over_unc(mssql_store, tmp_path):
    local = tmp_path / "a.csv"
    local.write_text(fixture("url_template_a.csv").read_text() + f"U{uuid.uuid4().hex[:6]},https://e.org/u,SBC\n",
                     encoding="utf-8")
    drive, rest = os.path.splitdrive(str(local.resolve()))
    unc = f"\\\\localhost\\{drive.rstrip(':')}${rest}"
    if not Path(unc).exists():
        pytest.skip(f"administrative share not reachable: {unc}")
    parked = process(unc, project=f"UNC{uuid.uuid4().hex[:4].upper()}", cls="PC", file_type="CSV", source="it",
                     store=mssql_store)
    with mssql_store.transaction() as tx:
        assert tx.ledger.get(parked.file_id).source_path == unc
