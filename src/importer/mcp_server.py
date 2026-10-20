"""MCP server: the user's own Claude client is the onboarding agent (design §8.19).

Tools expose metadata only — headers, schemas, earlier mappings and capped samples;
row data never crosses MCP. accept_mapping and register_schema are sign-off tools:
annotated destructive so clients ask first, and, when the client supports it, they
also ask the user directly (elicitation, via the SDK's resolvers so it works on both
the handshake-era and the 2026-07-28 input_required protocols). The approver is the OS user, never a
model-supplied value. Every call writes a decision_log row; sample values never do.
"""

# No `from __future__ import annotations`: the sign-off resolvers are closures over
# the store, and the SDK must evaluate `Resolve(...)` annotations where they are defined.
import getpass
import json
from importlib.resources import files
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import onboarding
from .addresses import AddressError
from .admin import SchemaRefused
from .locators import LocatorError
from .schema_sources import SchemaSourceError, import_schemas, live_source

EXPECTED = (onboarding.OnboardingError, AddressError, SchemaRefused, SchemaSourceError, LocatorError)

SERVER_INSTRUCTIONS = (
    "Data boundary: no incoming file's contents may reach you outside the importer tools. Never read data files, shares, Blob URLs, raw/stage tables or reject payloads directly (Read, Grep, shell, Python, SQL, `importer preview`); if you believe you need real values, stop and ask the user for explicit permission first.\n"
    "Authentication: if SQL Server requires a password for this session and none is set, ask the user in chat for their password and call `authenticate(password=...)`."
)

ONBOARD_PROMPT = """You are helping onboard and land files using the Data Import Agent. Work through them with the user.

0. First-run check: Call get_connection_status. If unauthenticated, ask the user for their SQL Server password and call authenticate.
1. Introducing files: If the user provides a file path or URL to import, call ingest_file to route it.
2. If quarantined: Call list_pending. Take one signature at a time, oldest first.
3. Call get_file_profile. If suggested_draft is reuse or exact, call preview_mapping with its spec and present it.
4. Otherwise gather evidence: get_prior_mappings for each candidate schema (strongest evidence), search_schemas
   when the candidates are weak, and get_samples only for columns whose names alone do not settle the mapping.
   Read get_spec_reference, then draft a declarative spec. Never use "kind": "plugin".
   For XML, write a "kind": "tree" spec that mirrors the nesting tree (get_file_profile shows the top levels;
   get_tree expands a branch): one table node per record node (the "record" candidates say which schema each
   node probably feeds), keys carried down with "column" on ancestor nodes, and never combine sibling datasets.
   Fix every tree_warnings entry before you submit.
5. Target database: The current control database is always available. To query or register external warehouse targets,
   call register_destination or register_schema.
6. Never invent a target column. If unsure, leave a file column unmapped so it lands in _extras, and say so.
   In rationale evidence, notes and reasons, describe values ("URLs", "5-digit codes"); never quote sample values.
7. Call preview_mapping and fix any problems, then submit_mapping with a rationale entry
   {column, target, how, evidence, confidence} for every non-exact column.
8. Show the user the review table and the preview summary. Ask them to accept, correct, or reject.
9. For corrections, submit_mapping again with the user's words as note. For a rejection, call reject_mapping.
10. Call accept_mapping (with user_confirmed=True when confirmed in chat) only after the user explicitly approves the latest draft, then report each file's result.
11. Never edit code or files. Use only the importer tools.
12. Row values never reach you: samples and preview rows are masked shapes (every letter is "a", every digit
    is "9", punctuation kept), so "99999aa9999999-99" is a code and "99% aaaaaaaaaaa" a percentage phrase.
    Reason from column names, counts, shapes and prior mappings.
13. Data boundary: no incoming file's contents may reach you outside the importer tools. Never read data files, shares, Blob URLs, raw/stage tables or reject payloads directly (Read, Grep, shell, Python, SQL, `importer preview`); if you believe you need real values, stop and ask the user for explicit permission first.
"""


class Confirm(BaseModel):
    confirm: bool


def _annotations(*, read_only: bool, destructive: bool) -> ToolAnnotations:
    if "read_only_hint" in ToolAnnotations.model_fields:
        return ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive,
                               idempotent_hint=read_only, open_world_hint=False)
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=read_only, openWorldHint=False)


READ_ONLY = _annotations(read_only=True, destructive=False)
WRITES = _annotations(read_only=False, destructive=False)
SIGN_OFF = _annotations(read_only=False, destructive=True)


def spec_reference() -> str:
    return files("importer").joinpath("mcp_reference.md").read_text(encoding="utf-8")


def _out(data) -> str:
    return json.dumps(data, indent=2, default=str)


def _supports_elicitation(ctx: Context) -> bool:
    """Form elicitation declared (a bare `elicitation: {}` counts; url-only does not)."""
    elicitation = getattr(getattr(ctx, "client_capabilities", None), "elicitation", None)
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def _ask(ctx: Context, message: str):
    """Resolver result: ask the user when the client can, else defer to its tool-approval prompt (§8.19)."""
    if not _supports_elicitation(ctx):
        return Confirm(confirm=True)
    return Elicit(message=message, schema=Confirm)


def _confirmed(outcome, fallback: bool = False) -> bool:
    if fallback:
        return True
    if isinstance(outcome, Confirm):
        return bool(outcome.confirm)
    return isinstance(outcome, AcceptedElicitation) and bool(getattr(outcome.data, "confirm", False))


def create_server(store, *, user: str | None = None, target: str = "", retry_fn=None,
                  live_source_factory=live_source) -> MCPServer:
    user = user or getpass.getuser()
    mcp = MCPServer("importer", instructions=SERVER_INSTRUCTIONS)

    def log(tool: str, args: dict, *, signoff: bool = False) -> None:
        try:
            with store.transaction() as tx:
                tx.audit.decision(None, f"mcp:{tool}", observed=args, proposed=None, applied=False,
                                  approval=user if signoff else None)
        except Exception:
            pass  # Avoid failing tool before database is authenticated

    def run(tool: str, args: dict, work, *, signoff: bool = False) -> str:
        log(tool, args, signoff=signoff)
        try:
            return _out(work())
        except EXPECTED as exc:
            return _out({"error": str(exc)})
        except Exception as exc:
            err_msg = str(exc)
            if "password" in err_msg.lower() or "login failed" in err_msg.lower():
                return _out({"error": f"Database authentication required: {err_msg}. Ask the user for their password in chat and call authenticate(password=...)." })
            return _out({"error": err_msg})

    @mcp.tool(annotations=READ_ONLY)
    def list_pending() -> str:
        """Files waiting for a mapping: signature, review state, suggested path, top candidates, waiting files.
        `stranded` lists files still quarantined under an already-bound signature; tell the user to run
        the `retry` command shown for each."""
        return run("list_pending", {}, lambda: {"target": target, "pending": onboarding.pending(store),
                                                "stranded": onboarding.stranded(store)})

    @mcp.tool(annotations=READ_ONLY)
    def get_file_profile(signature_id: int) -> str:
        """A waiting file's structure: columns, or for XML its nesting tree (element names, counts, repeats and
        attribute names, top 4 levels; never values), plus candidates and the suggested draft."""
        return run("get_file_profile", {"signature_id": signature_id},
                   lambda: onboarding.file_profile(store, signature_id))

    @mcp.tool(annotations=READ_ONLY)
    def get_tree(signature_id: int, path: str | None = None, depth: int = 4) -> str:
        """An XML signature's nesting tree: element names, counts, repeat ranges, attribute names, never values.
        Pass a node's `path` to expand a branch whose children were omitted."""
        return run("get_tree", {"signature_id": signature_id, "path": path, "depth": depth},
                   lambda: onboarding.tree(store, signature_id, path=path, depth=depth))

    @mcp.tool(annotations=READ_ONLY)
    def search_schemas(columns: list[str] | None = None, text: str | None = None, limit: int = 10) -> str:
        """Registered schemas ranked by overlap with the given columns, or filtered by address text."""
        return run("search_schemas", {"columns": columns, "text": text, "limit": limit},
                   lambda: onboarding.search_schemas(store, columns=columns, text=text, limit=limit))

    @mcp.tool(annotations=READ_ONLY)
    def get_schema(address: str) -> str:
        """A registered schema's columns (address = database.schema.table)."""
        return run("get_schema", {"address": address}, lambda: onboarding.get_schema(store, address))

    @mcp.tool(annotations=READ_ONLY)
    def get_prior_mappings(address: str) -> str:
        """Specs already bound to signatures that feed this schema; the strongest evidence for a new mapping."""
        return run("get_prior_mappings", {"address": address}, lambda: onboarding.prior_mappings(store, address))

    @mcp.tool(annotations=READ_ONLY)
    def get_samples(signature_id: int, columns: list[str]) -> str:
        """A few distinct sample values per column of the waiting file (capped by the shared settings)."""
        return run("get_samples", {"signature_id": signature_id, "columns": columns},
                   lambda: onboarding.samples(store, signature_id, columns))

    @mcp.tool(annotations=READ_ONLY)
    def get_spec_reference() -> str:
        """The mapping spec format and the onboarding rules."""
        log("get_spec_reference", {})
        return spec_reference()

    @mcp.tool(annotations=READ_ONLY)
    def preview_mapping(signature_id: int, spec: dict) -> str:
        """Dry-run a spec against the waiting file: per-target counts, _extras keys, rejects, a few sample rows."""
        return run("preview_mapping", {"signature_id": signature_id, "spec": spec},
                   lambda: onboarding.preview_spec(store, signature_id, spec))

    @mcp.tool(annotations=WRITES)
    def submit_mapping(signature_id: int, spec: dict, rationale: list[dict], note: str | None = None,
                       path: str = "model") -> str:
        """Store a draft for the user's review (validated and previewed first). Returns the review table."""
        args = {"signature_id": signature_id, "spec": spec, "rationale": rationale, "note": note, "path": path}
        return run("submit_mapping", args,
                   lambda: onboarding.submit(store, signature_id, spec, rationale, by=user, note=note, path=path))

    @mcp.tool(annotations=WRITES)
    def reject_mapping(signature_id: int, reason: str) -> str:
        """Record that the user rejected this proposal; its files stay quarantined."""
        return run("reject_mapping", {"signature_id": signature_id, "reason": reason},
                   lambda: onboarding.reject(store, signature_id, reason, by=user))

    def confirm_bind(signature_id: int, draft_version_id: int, ctx: Context):
        try:
            message = onboarding.bind_summary(store, signature_id, draft_version_id)
        except EXPECTED:
            return None  # nothing to ask; accept_mapping reports the error
        return _ask(ctx, message)

    def confirm_register(address_or_pattern: str, ctx: Context, columns: list[str] | None = None):
        source = "the given column list" if columns is not None else "the live destination"
        return _ask(ctx, f"Register destination schema(s) {address_or_pattern} from {source}?")

    @mcp.tool(annotations=READ_ONLY)
    def get_connection_status() -> str:
        """Check SQL Server connection status, active server/database, and whether session authentication is required."""
        from . import db
        return _out(db.current_connection_info())

    @mcp.tool(annotations=WRITES)
    def authenticate(password: str) -> str:
        """Authenticate this session in-memory with SQL Server using the user's password.

        Verifies credentials and maintains the connection for the duration of the server process.
        The password is kept only in memory and never written to disk.
        """
        from . import db
        db.set_session_password(password)
        if hasattr(store, "reset_engine"):
            store.reset_engine()
        try:
            with store.engine.connect() as conn:
                pass
            info = db.current_connection_info()
            return _out({
                "authenticated": True,
                "server": info.get("server"),
                "database": info.get("database"),
                "message": "Successfully authenticated with SQL Server for this session."
            })
        except Exception as exc:
            db.set_session_password(None)
            if hasattr(store, "reset_engine"):
                store.reset_engine()
            return _out({
                "authenticated": False,
                "error": f"Authentication failed: {exc}"
            })

    @mcp.tool(annotations=WRITES)
    def ingest_file(path: str, project: str = "DEFAULT", cls: str = "IMPORT", file_type: str | None = None) -> str:
        """Introduce an incoming file directly to the landing pipeline.

        Extracts identity, fingerprints, and routes the file:
        - If matching an active registered signature/mapper, it lands and completes immediately.
        - If new or drifted, it is quarantined and appears in list_pending for mapping onboarding.
        """
        from .pipeline import process
        ft = file_type
        if not ft:
            p = str(path).lower()
            if p.endswith(".csv"):
                ft = "csv"
            elif p.endswith(".json") or p.endswith(".jsonl") or p.endswith(".ndjson"):
                ft = "json"
            elif p.endswith(".xml"):
                ft = "xml"
            elif p.endswith(".parquet"):
                ft = "parquet"
            else:
                ft = "csv"

        def work():
            res = process(path, project=project, cls=cls, file_type=ft, source=f"mcp:{user}", store=store)
            return res.to_dict()

        return run("ingest_file", {"path": path, "project": project, "cls": cls, "file_type": ft}, work)

    @mcp.tool(annotations=WRITES)
    def register_destination(name: str, server: str, database: str) -> str:
        """Register a destination database alias for schema discovery and mapping.

        Writes to the versioned configuration artifact in the control database so
        all clients and tools recognize the destination.
        """
        from . import settings

        def work():
            with store.transaction() as tx:
                row = settings.set_destination(tx, name, server=server, database=database, by=user)
            return {"name": name.upper(), "server": server, "database": database, "config_version": row.version_no}

        return run("register_destination", {"name": name, "server": server, "database": database}, work)

    @mcp.tool(annotations=SIGN_OFF)
    async def accept_mapping(
        signature_id: int, draft_version_id: int,
        approval: Annotated[ElicitationResult[Confirm], Resolve(confirm_bind)],
        user_confirmed: bool = False,
    ) -> str:
        """SIGN-OFF. Bind the signature to the reviewed draft and retry every waiting file.
        Call only after the user explicitly approves the latest draft. Pass user_confirmed=True
        if the user confirmed approval directly in chat."""
        args = {"signature_id": signature_id, "draft_version_id": draft_version_id}
        try:
            onboarding.bind_summary(store, signature_id, draft_version_id)
        except EXPECTED as exc:
            return _out({"error": str(exc)})
        if not _confirmed(approval, fallback=user_confirmed):
            log("accept_mapping", {**args, "declined": True}, signoff=True)
            return _out({"accepted": False, "reason": "the user declined"})
        return run("accept_mapping", args, signoff=True,
                   work=lambda: onboarding.accept(store, signature_id, draft_version_id, by=user, retry_fn=retry_fn))

    @mcp.tool(annotations=SIGN_OFF)
    async def register_schema(
        address_or_pattern: str,
        approval: Annotated[ElicitationResult[Confirm], Resolve(confirm_register)],
        columns: list[str] | None = None,
        user_confirmed: bool = False,
    ) -> str:
        """SIGN-OFF. Register destination schema(s): from the live destination (wildcards allowed,
        e.g. TARGET_DB.*.*), or from an explicit column list for one table. Pass user_confirmed=True
        if the user confirmed approval directly in chat."""
        if not _confirmed(approval, fallback=user_confirmed):
            log("register_schema", {"address_or_pattern": address_or_pattern, "declined": True}, signoff=True)
            return _out({"registered": False, "reason": "the user declined"})

        def work():
            if columns is not None:
                return import_schemas(store, address_or_pattern, by=user, columns=columns)
            source = live_source_factory(store, address_or_pattern.split(".")[0])
            try:
                return import_schemas(store, address_or_pattern, by=user, source=source)
            finally:
                source.close()

        return run("register_schema", {"address_or_pattern": address_or_pattern, "columns": columns}, work, signoff=True)

    @mcp.prompt()
    def onboard() -> str:
        """Map files waiting for review, with the user's approval."""
        return ONBOARD_PROMPT

    return mcp
