"""Shared test helpers and synthetic mapping definitions."""

from __future__ import annotations

import json
from pathlib import Path

from importer.mappers.base import RunContext

FIXTURES = Path(__file__).parent / "fixtures"

URL_TARGET = "app_db.relational.url_template"
EVENT_TARGET = "app_db.dbo.event"
FORMULARY_TARGETS = (
    "app_db.xml.formulary_benefit_definition", "app_db.xml.formulary_tier_definition", "app_db.xml.formulary_drug_list",
)
BENEFIT_TARGETS = (
    "app_db.xml.package", "app_db.xml.benefits", "app_db.xml.planattributes", "app_db.xml.costsharevariance",
    "app_db.xml.sbc", "app_db.xml.hsa", "app_db.xml.url", "app_db.xml.avcalculator", "app_db.xml.mooplist",
    "app_db.xml.plandeductiblelist", "app_db.xml.servicevisitlist",
)


def configure(store, *names: str) -> None:
    """Register destinations in the shared settings (what `importer config set destination` does)."""
    from importer import settings

    with store.transaction() as tx:
        have = settings.load(tx.artifacts)
        for name in names:
            if have.destination(name) is None:
                settings.set_destination(tx, name, server="test", database=name.upper(), by="test")


def configure_for(store, schemas: dict) -> None:
    configure(store, *sorted({key.split(".")[0] for key in schemas}))


def fixture(name: str) -> Path:
    return FIXTURES / name


def load_json(name: str):
    return json.loads(fixture(name).read_text(encoding="utf-8"))


def ctx(schemas: dict[str, list[str]], request_context: dict | None = None, file_id: int = 7) -> RunContext:
    return RunContext(
        file_id=file_id, signature_id=1, artifact_version_id=1,
        request_context=request_context or {}, schemas=schemas,
    )


URL_SCHEMA = {"app_db.relational.url_template": ["urlid", "newurl", "urltype", "issuerid", "run_id", "file_id"]}
URL_SPEC_A = {
    "kind": "tabular",
    "extractions": [{
        "target": "app_db.relational.url_template", "records": "*", "auto": True,
        "fields": {"file_id": "$file_id", "run_id": "$context.run_id"},
    }],
}
URL_SPEC_B = {
    "kind": "tabular",
    "extractions": [{
        "target": "app_db.relational.url_template", "records": "*", "auto": True,
        "fields": {"newurl": "url", "file_id": "$file_id", "run_id": "$context.run_id"},
    }],
}
EVENT_SCHEMA = {"app_db.dbo.event": ["id", "name", "amount"]}
EVENT_SPEC = {"kind": "tabular", "extractions": [{"target": "app_db.dbo.event", "records": "*", "auto": True}]}


def setup_signature(store, sample: Path, declared, schemas: dict[str, list[str]], mapper_def: dict, key: str) -> int:
    """Hand-register schemas + a mapper + a signature (what `importer mapper add` / `signature register` do)."""
    configure_for(store, schemas)
    from importer.admin import register_mapper, register_schema, register_signature

    with store.transaction() as tx:
        for schema_id, columns in schemas.items():
            register_schema(tx, schema_id, columns, created_by="test")
        mapper = register_mapper(tx, key, mapper_def, created_by="test")
        register_signature(tx, sample.read_bytes(), declared, mapper.version_id, approved_by="test")
    return mapper.version_id
