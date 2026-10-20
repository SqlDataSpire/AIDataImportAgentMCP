"""M2a acceptance (design §8.13) against the DEV SQL Server."""

from __future__ import annotations

import json
import uuid

import pytest

from importer.admin import register_mapper, register_schema, register_signature
from importer.models import Declared, FileStatus
from importer.pipeline import process
from tests.support import EVENT_SCHEMA, EVENT_SPEC, URL_SCHEMA, URL_SPEC_A, URL_SPEC_B, fixture, load_json

pytestmark = pytest.mark.integration


def _unique_copy(tmp_path, name: str, comment: bytes):
    path = tmp_path / name
    path.write_bytes(fixture(name).read_bytes() + comment)
    return path


def _setup(store, sample, declared, schemas, spec, key) -> int:
    with store.transaction() as tx:
        for schema_id, columns in schemas.items():
            register_schema(tx, schema_id, columns, created_by="acceptance")
        mapper = register_mapper(tx, key, spec, created_by="acceptance")
        register_signature(tx, sample.read_bytes(), declared, mapper.version_id, approved_by="acceptance")
    return mapper.version_id


def _landed(store, file_id: int, target: str) -> int:
    with store.transaction() as tx:
        return tx.raw.count_landed(file_id, target)


def test_1_formulary_lands_three_tables(mssql_store, tmp_path):
    path = _unique_copy(tmp_path, "formulary_dc.xml", f"\n<!-- {uuid.uuid4()} -->\n".encode())
    _setup(mssql_store, path, Declared.normalize("DC", "PC", "XML"), load_json("formulary_schemas.json"),
           load_json("formulary_spec.json"), "formulary")
    result = process(path, project="DC", cls="PC", file_type="XML", source="acceptance",
                     context={"binder_name": "ABCD-DC25-1", "run_id": "1"}, store=mssql_store)
    assert result.status is FileStatus.COMPLETED and result.percent_loaded == 100.0
    assert [_landed(mssql_store, result.file_id, t) for t in
            ("app_db.xml.formulary_benefit_definition", "app_db.xml.formulary_tier_definition", "app_db.xml.formulary_drug_list")] == [5, 2, 3]


def test_2_two_variants_one_schema(mssql_store, tmp_path):
    tag = uuid.uuid4().hex[:8]
    a = tmp_path / "a.csv"
    a.write_text(fixture("url_template_a.csv").read_text() + f"A{tag},https://example.org/{tag},SBC\n")
    b = tmp_path / "b.csv"
    b.write_text(fixture("url_template_b.csv").read_text() + f"B{tag},https://example.net/{tag},SBC\n")
    _setup(mssql_store, a, Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_A, "urls_a")
    _setup(mssql_store, b, Declared.normalize("VT", "PC", "CSV"), URL_SCHEMA, URL_SPEC_B, "urls_b")
    ra = process(a, project="VT", cls="PC", file_type="CSV", source="acceptance", store=mssql_store)
    rb = process(b, project="VT", cls="PC", file_type="CSV", source="acceptance", store=mssql_store)
    assert ra.status is rb.status is FileStatus.COMPLETED
    assert _landed(mssql_store, ra.file_id, "app_db.relational.url_template") == 3
    assert _landed(mssql_store, rb.file_id, "app_db.relational.url_template") == 3


def test_3_bad_ndjson_line_completes_with_a_reject(mssql_store, tmp_path):
    path = tmp_path / "events.ndjson"
    path.write_bytes(fixture("events.ndjson").read_bytes() + json.dumps({"id": uuid.uuid4().hex, "name": "z", "amount": "1"}).encode() + b"\n")
    _setup(mssql_store, path, Declared.normalize("DC", "PD", "NDJSON"), EVENT_SCHEMA, EVENT_SPEC, "events")
    result = process(path, project="DC", cls="PD", file_type="NDJSON", source="acceptance", store=mssql_store)
    assert result.status is FileStatus.COMPLETED
    assert (result.rows_total, result.rows_landed, result.rows_rejected) == (5, 4, 1)
    assert result.percent_loaded == 80.0


def test_4_unknown_project_is_quarantined_with_reuse_proposal(mssql_store, tmp_path):
    mapper_id = _setup(mssql_store, fixture("formulary_dc.xml"), Declared.normalize("DC", "PC", "XML"),
                       load_json("formulary_schemas.json"), load_json("formulary_spec.json"), "formulary")
    project = "Z" + uuid.uuid4().hex[:6].upper()
    path = _unique_copy(tmp_path, "formulary_dc.xml", f"\n<!-- {project} -->\n".encode())
    result = process(path, project=project, cls="PC", file_type="XML", source="acceptance", store=mssql_store)
    assert result.status is FileStatus.QUARANTINED and result.quarantine_reason == "unroutable"
    # Any project already bound to this template may be the one proposed (DEV keeps earlier acceptance data);
    # what matters is a reuse proposal whose mapper is the same spec as the one registered here.
    import re
    match = re.fullmatch(r"looks like \w+\|PC\|XML\|prescriptionDrugTemplateVO -> reuse mapper (\d+)",
                         result.proposal_summary or "")
    assert match, result.proposal_summary
    with mssql_store.transaction() as tx:
        proposed = tx.artifacts.get(int(match.group(1))).definition
        registered = tx.artifacts.get(mapper_id).definition
    assert proposed == registered


def test_5_byte_identical_redelivery(mssql_store, tmp_path):
    path = _unique_copy(tmp_path, "formulary_dc.xml", f"\n<!-- {uuid.uuid4()} -->\n".encode())
    _setup(mssql_store, path, Declared.normalize("DC", "PC", "XML"), load_json("formulary_schemas.json"),
           load_json("formulary_spec.json"), "formulary")
    first = process(path, project="DC", cls="PC", file_type="XML", source="acceptance", store=mssql_store)
    again = process(path, project="DC", cls="PC", file_type="XML", source="acceptance", store=mssql_store)
    assert again.file_id == first.file_id
    assert _landed(mssql_store, first.file_id, "app_db.xml.formulary_drug_list") == 3
