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

STAND_IN = "import_agent_dev.it_m2c1."  # benefits land in a stand-in namespace, never in production addresses


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
            register_schema(tx, address.replace("app_db.xml.", STAND_IN), columns, created_by="it")
    spec = json.loads(json.dumps(load_json("benefits_tree_spec.json")).replace("app_db.xml.", STAND_IN))
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
