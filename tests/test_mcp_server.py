from __future__ import annotations

import json

import pytest
from mcp import Client
from mcp.types import ElicitResult

from importer.admin import register_schema
from importer.mcp_server import create_server, spec_reference
from importer.memory_store import MemoryStore
from importer.models import SignatureStatus
from importer.pipeline import process
from tests.support import URL_SCHEMA, URL_SPEC_B, URL_TARGET, configure, fixture

TOOLS = {
    "list_pending", "get_file_profile", "search_schemas", "get_schema", "get_prior_mappings", "get_samples",
    "get_spec_reference", "preview_mapping", "submit_mapping", "reject_mapping", "accept_mapping", "register_schema",
    "get_tree", "get_connection_status", "authenticate", "ingest_file", "register_destination",
}
RATIONALE = [{"column": "url", "target": "newurl", "how": "model", "evidence": "values are URLs", "confidence": 0.9}]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _parked() -> MemoryStore:
    store = MemoryStore()
    configure(store, "app_db")
    with store.transaction() as tx:
        register_schema(tx, URL_TARGET, URL_SCHEMA[URL_TARGET], created_by="t")
    process(fixture("url_template_b.csv"), project="VT", cls="PC", file_type="CSV", source="t", store=store)
    return store


def _json(result) -> dict:
    return json.loads(result.content[0].text)


async def _approve(context, params):
    return ElicitResult(action="accept", content={"confirm": True})


async def _decline(context, params):
    return ElicitResult(action="decline")


@pytest.mark.anyio
async def test_tools_annotations_and_the_onboard_prompt():
    async with Client(create_server(MemoryStore(), user="tony"), raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == TOOLS
        accept = tools["accept_mapping"].annotations.model_dump(by_alias=True)
        listing = tools["list_pending"].annotations.model_dump(by_alias=True)
        assert accept["destructiveHint"] is True and accept["readOnlyHint"] is False
        assert listing["readOnlyHint"] is True
        assert [p.name for p in (await client.list_prompts()).prompts] == ["onboard"]
        prompt = await client.get_prompt("onboard")
        assert "accept_mapping" in prompt.messages[0].content.text
        assert "$parent(" in spec_reference() and '"kind": "plugin"' in spec_reference()


@pytest.mark.anyio
async def test_end_to_end_with_elicitation_and_audit():
    store = _parked()
    async with Client(create_server(store, user="tony", target="importer @ test [DEV]"), raise_exceptions=True,
                      elicitation_callback=_approve) as client:
        listing = _json(await client.call_tool("list_pending", {}))
        assert listing["target"] == "importer @ test [DEV]"
        sid = listing["pending"][0]["signature_id"]
        samples = _json(await client.call_tool("get_samples", {"signature_id": sid, "columns": ["url"]}))
        assert len(samples["url"]) == 2
        draft = _json(await client.call_tool("submit_mapping", {"signature_id": sid, "spec": URL_SPEC_B, "rationale": RATIONALE}))
        result = _json(await client.call_tool("accept_mapping", {"signature_id": sid, "draft_version_id": draft["draft_version_id"]}))
    assert result["files"][0]["status"] == "completed"
    logged = [d for d in store.state.decisions if d["action_type"].startswith("mcp:")]
    assert {d["action_type"] for d in logged} >= {"mcp:list_pending", "mcp:get_samples", "mcp:submit_mapping", "mcp:accept_mapping"}
    assert next(d for d in logged if d["action_type"] == "mcp:accept_mapping")["approval"] == "tony"
    assert all("example.net" not in json.dumps(d, default=str) for d in store.state.decisions)  # samples never logged
    seen = json.dumps([listing, samples, draft, result])
    assert not [v for v in ("example.net", "54321VT0020001", "Payment") if v in seen]  # only masked shapes cross MCP


@pytest.mark.anyio
async def test_declined_elicitation_binds_nothing():
    store = _parked()
    async with Client(create_server(store, user="tony"), raise_exceptions=True, elicitation_callback=_decline) as client:
        sid = _json(await client.call_tool("list_pending", {}))["pending"][0]["signature_id"]
        draft = _json(await client.call_tool("submit_mapping", {"signature_id": sid, "spec": URL_SPEC_B, "rationale": RATIONALE}))
        result = _json(await client.call_tool("accept_mapping", {"signature_id": sid, "draft_version_id": draft["draft_version_id"]}))
    assert result == {"accepted": False, "reason": "the user declined"}
    assert store.state.registry[sid].status is SignatureStatus.PROPOSED


@pytest.mark.anyio
async def test_errors_come_back_as_messages_and_register_schema_from_columns():
    store = _parked()
    async with Client(create_server(store, user="tony"), raise_exceptions=True, elicitation_callback=_approve) as client:
        missing = _json(await client.call_tool("get_schema", {"address": "app_db.relational.nope"}))
        assert missing == {"error": "no registered schema app_db.relational.nope"}
        report = _json(await client.call_tool("register_schema", {"address_or_pattern": "APP_DB.dbo.extra",
                                                                  "columns": ["a", "b"]}))
    assert report["added"][0]["address"] == "app_db.dbo.extra"


@pytest.mark.anyio
async def test_live_register_schema_closes_the_destination_connection():
    store = MemoryStore()
    configure(store, "app_db")

    class Live:
        closed = False

        def tables(self):
            return [("relational", "plans")]

        def columns(self, schema, table):
            return ["planid"]

        def close(self):
            Live.closed = True

    server = create_server(store, user="tony", live_source_factory=lambda store, name: Live())
    async with Client(server, raise_exceptions=True, elicitation_callback=_approve) as client:
        report = _json(await client.call_tool("register_schema", {"address_or_pattern": "APP_DB.relational.*"}))
    assert report["added"][0]["address"] == "app_db.relational.plans" and Live.closed


def test_the_model_is_told_never_to_quote_sample_values():
    from importer.mcp_server import ONBOARD_PROMPT

    for text in (ONBOARD_PROMPT, spec_reference()):
        assert "never quote sample values" in text


def test_the_model_is_told_it_only_sees_masked_shapes():
    from importer.mcp_server import ONBOARD_PROMPT

    for text in (ONBOARD_PROMPT, spec_reference()):
        assert "masked shapes" in text


def test_the_server_announces_the_data_boundary_rule_on_connect():
    from importer.mcp_server import ONBOARD_PROMPT

    server = create_server(MemoryStore(), user="tony")
    for text in (server.instructions, ONBOARD_PROMPT):
        assert "outside the importer tools" in text and "explicit permission" in text


@pytest.mark.anyio
async def test_get_tree_returns_names_and_counts_only(tmp_path):
    store = MemoryStore()
    configure(store, "app_db")
    path = tmp_path / "f.xml"
    path.write_bytes(fixture("formulary_dc.xml").read_bytes())
    process(path, project="ME", cls="PC", file_type="XML", source="t", store=store)
    async with Client(create_server(store, user="tony"), raise_exceptions=True) as client:
        sid = _json(await client.call_tool("list_pending", {}))["pending"][0]["signature_id"]
        tree = _json(await client.call_tool("get_tree", {"signature_id": sid, "depth": 2}))
        profile = await client.call_tool("get_file_profile", {"signature_id": sid})
    assert tree["root"] == "prescriptionDrugTemplateVO"
    seen = json.dumps(tree) + profile.content[0].text
    assert not [v for v in ("DCF001", "197361", "$10.00", "Preferred Brand") if v in seen]  # no values cross MCP


def test_the_prompt_and_reference_cover_tree_specs():
    from importer.mcp_server import ONBOARD_PROMPT

    assert "get_tree" in ONBOARD_PROMPT and '"kind": "tree"' in ONBOARD_PROMPT
    assert '"kind": "tree"' in spec_reference() and "forEach" in spec_reference()


@pytest.mark.anyio
async def test_accept_mapping_with_user_confirmed_fallback_without_elicitation():
    store = _parked()
    # Client without elicitation callback
    async with Client(create_server(store, user="tony"), raise_exceptions=True) as client:
        sid = _json(await client.call_tool("list_pending", {}))["pending"][0]["signature_id"]
        draft = _json(await client.call_tool("submit_mapping", {"signature_id": sid, "spec": URL_SPEC_B, "rationale": RATIONALE}))
        # Pass user_confirmed=True directly from chat approval
        result = _json(await client.call_tool("accept_mapping", {
            "signature_id": sid,
            "draft_version_id": draft["draft_version_id"],
            "user_confirmed": True,
        }))
    assert result["files"][0]["status"] == "completed"


@pytest.mark.anyio
async def test_wrangler_tools_ingest_and_register_destination(tmp_path):
    store = MemoryStore()
    configure(store, "app_db")
    async with Client(create_server(store, user="tony"), raise_exceptions=True) as client:
        # 1. Check connection status
        status = _json(await client.call_tool("get_connection_status", {}))
        assert "authenticated" in status

        # 2. Register destination
        reg = _json(await client.call_tool("register_destination", {"name": "WAREHOUSE", "server": "sql01", "database": "wh_db"}))
        assert reg["name"] == "WAREHOUSE"
        assert reg["database"] == "wh_db"

        # 3. Ingest file directly
        csv_file = tmp_path / "incoming.csv"
        csv_file.write_text("col_a,col_b\n1,2\n3,4\n", encoding="utf-8")
        ingest = _json(await client.call_tool("ingest_file", {"path": str(csv_file), "project": "PRJ", "cls": "CLS"}))
        assert ingest["status"] == "quarantined"

        # 4. Verify it appears in list_pending
        pending = _json(await client.call_tool("list_pending", {}))
        assert len(pending["pending"]) == 1

