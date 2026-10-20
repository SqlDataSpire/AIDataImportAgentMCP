"""importer — command-line interface (design spec §11)."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import logging
import sys
from pathlib import Path

EXIT_CODES = {"completed": 0, "quarantined": 2, "failed": 1}


def _context(pairs: list[str] | None) -> dict:
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--context expects KEY=VALUE, got {pair!r}")
        out[key] = value
    return out


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _default_store():
    from .db import get_engine
    from .mssql_store import MssqlStore

    # DataEngine prints to stdout while building the engine; keep stdout clean for
    # `importer mcp`, where it is the JSON-RPC stream (design §8.19).
    with contextlib.redirect_stdout(sys.stderr):
        return MssqlStore(get_engine())


def cmd_process(args, store) -> int:
    from .pipeline import process

    result = process(args.path, project=args.project, cls=args.cls, file_type=args.type, source=args.source,
                     context=_context(args.context), store=store)
    _print(result.to_dict())
    return EXIT_CODES.get(result.status.value, 1)


def cmd_retry(args, store) -> int:
    from .pipeline import retry

    result = retry(args.file_id, store=store)
    _print(result.to_dict())
    return EXIT_CODES.get(result.status.value, 1)


def cmd_pending(args, store) -> int:
    from .onboarding import pending, stranded

    _print(pending(store))
    for item in stranded(store):
        print(f"stranded: file {item['file_id']} ({item['name']}) is quarantined under active signature "
              f"{item['signature_id']}; run: {item['retry']}", file=sys.stderr)
    return 0


def cmd_mcp(args, store) -> int:
    from .environment import banner, target_of
    from .mcp_server import create_server

    from .data_guard import clear_marker, mark_server_running

    identity = target_of(store)
    marker = mark_server_running()  # the data guard's 'auto' mode is on while this server runs
    try:
        create_server(store, target=banner(identity) if identity else "").run(transport="stdio")
    finally:
        clear_marker(marker)
    return 0


def cmd_preview(args, store) -> int:
    from .mappers.base import RunContext
    from .mappers.loading import load_mapper
    from .mappers.preview import preview
    from .models import Declared
    from .readers import open_document

    declared = Declared.normalize(args.project, args.cls, args.type)
    with store.transaction() as tx:
        artifact = tx.artifacts.get(args.mapper_version)
        schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
    mapper = load_mapper(artifact, known_schemas=set(schemas))
    doc = open_document(args.path, declared.file_type)
    ctx = RunContext(file_id=0, signature_id=0, artifact_version_id=artifact.version_id,
                     request_context=_context(args.context), schemas={t: schemas[t] for t in mapper.targets})
    _print(preview(mapper, doc, ctx, n=args.rows).to_dict())
    return 0


def cmd_schema_import(args, store) -> int:
    from .schema_sources import SchemaSourceError, import_schemas, live_source, read_column_file

    try:
        if args.from_file:
            report = import_schemas(store, args.address, by=getpass.getuser(), columns=read_column_file(args.from_file))
        else:
            source = live_source(store, args.address.split(".")[0])
            try:
                report = import_schemas(store, args.address, by=getpass.getuser(), source=source)
            finally:
                source.close()
    except SchemaSourceError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    _print(report)
    return 1 if report["refused"] and not (report["added"] or report["changed"] or report["unchanged"]) else 0


def cmd_mapper_add(args, store) -> int:
    from .admin import register_mapper

    definition = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    with store.transaction() as tx:
        row = register_mapper(tx, args.key, definition, created_by=getpass.getuser())
    _print({"mapper": row.artifact_key, "version_id": row.version_id, "version_no": row.version_no})
    return 0


def cmd_signature_register(args, store) -> int:
    from .admin import register_signature
    from .models import Declared

    declared = Declared.normalize(args.project, args.cls, args.type)
    with store.transaction() as tx:
        signature_id = register_signature(tx, Path(args.sample).read_bytes(), declared, args.mapper_version,
                                          approved_by=getpass.getuser())
        fingerprint = tx.registry.get(signature_id).fingerprint
    _print({"signature_id": signature_id, "fingerprint": fingerprint})
    return 0


def cmd_db_rebuild(args) -> int:
    from . import config, db, environment
    from .schema_tables import REBUILD_DROP_SQL

    engine = db.get_engine()
    with engine.connect() as conn:
        identity = environment.read(conn)
    reason = environment.dev_guard(identity.environment, identity.database)
    if reason:
        print(f"refusing: {reason}", file=sys.stderr)
        return 1
    typed = args.confirm if args.confirm is not None else input(
        f"This drops every importer table in {identity.database}. Type the database name to confirm: ")
    if typed.strip() != identity.database:
        print("confirmation did not match the database name; nothing changed", file=sys.stderr)
        return 1
    schema_path = config.home() / "sql" / "001_schema.sql"
    if not schema_path.exists():
        schema_path = Path(__file__).resolve().parent / "sql" / "001_schema.sql"
    script = schema_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(REBUILD_DROP_SQL)
        conn.exec_driver_sql(script)
    print(f"rebuilt the importer schema in {identity.database}")
    return 0


def cmd_db_init(args) -> int:
    from . import db, environment

    engine = db.get_engine()
    with engine.connect() as conn:
        target = environment.read(conn)
    print(environment.banner(target), file=sys.stderr)
    typed = args.confirm if args.confirm is not None else input(
        f"This marks {target.server}/{target.database} as {args.environment}. Type the database name to confirm: ")
    if typed.strip() != target.database:
        print("confirmation did not match the database name; nothing changed", file=sys.stderr)
        return 1
    try:
        with engine.begin() as conn:
            environment.init(conn, args.environment, by=getpass.getuser())
            identity = environment.read(conn)
    except environment.GuardError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    print(environment.banner(identity))
    return 0


def cmd_config(args, store) -> int:
    from . import settings

    with store.transaction() as tx:
        if args.config_command == "show":
            _print(settings.document(tx.artifacts))
            return 0
        by = getpass.getuser()
        try:
            if args.config_command == "unset":
                settings.unset_destination(tx, args.name, by=by)
            elif args.key == "destination":
                if not args.name:
                    raise settings.SettingsError("usage: config set destination NAME --server S --database D")
                settings.set_destination(tx, args.name, server=args.server, database=args.database, by=by)
            else:
                settings.set_value(tx, args.key, int(args.name), by=by)
        except (settings.SettingsError, ValueError, TypeError) as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return 1
        _print(settings.document(tx.artifacts))
    return 0


def _declared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True)
    parser.add_argument("--class", dest="cls", required=True)
    parser.add_argument("--type", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="importer", description="ELT import agent - deterministic fast path (M2a)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", help="import one file")
    p.add_argument("path")
    _declared(p)
    p.add_argument("--source", required=True)
    p.add_argument("--context", action="append", metavar="KEY=VALUE")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("preview", help="dry-run a mapper against a file")
    p.add_argument("path")
    p.add_argument("--mapper-version", type=int, required=True)
    _declared(p)
    p.add_argument("--context", action="append", metavar="KEY=VALUE")
    p.add_argument("--rows", type=int, default=20)
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("retry", help="re-enter a quarantined or failed file")
    p.add_argument("file_id", type=int)
    p.set_defaults(func=cmd_retry)

    sub.add_parser("pending", help="list unknown signatures waiting for a mapping").set_defaults(func=cmd_pending)
    sub.add_parser("mcp", help="run the onboarding MCP server over stdio (for Claude Code / Desktop)").set_defaults(func=cmd_mcp)

    config_parser = sub.add_parser("config", help="show or change the shared settings (stored in the database)")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show").set_defaults(func=cmd_config)
    p = config_sub.add_parser("set")
    p.add_argument("key", help="'destination' or samples.max_values / samples.max_chars")
    p.add_argument("name", nargs="?", help="destination NAME, or the numeric value")
    p.add_argument("--server")
    p.add_argument("--database")
    p.set_defaults(func=cmd_config)
    p = config_sub.add_parser("unset")
    p.add_argument("key", choices=["destination"])
    p.add_argument("name")
    p.set_defaults(func=cmd_config)

    schema = sub.add_parser("schema").add_subparsers(dest="schema_command", required=True)
    p = schema.add_parser("import", help="register destination table schemas (live, or --from-file)")
    p.add_argument("address", help="DATABASE.SCHEMA.TABLE; wildcards allowed in SCHEMA and TABLE, e.g. APP_DB.*.*")
    p.add_argument("--from-file", help="JSON list or one-name-per-line CSV of column names (one explicit table)")
    p.set_defaults(func=cmd_schema_import)

    mapper = sub.add_parser("mapper").add_subparsers(dest="mapper_command", required=True)
    p = mapper.add_parser("add", help="add a mapper version from a JSON spec")
    p.add_argument("spec")
    p.add_argument("--key", required=True)
    p.set_defaults(func=cmd_mapper_add)

    signature = sub.add_parser("signature").add_subparsers(dest="signature_command", required=True)
    p = signature.add_parser("register", help="hand-register a sample file's signature to a mapper")
    p.add_argument("sample")
    _declared(p)
    p.add_argument("--mapper-version", type=int, required=True)
    p.set_defaults(func=cmd_signature_register)

    database = sub.add_parser("db").add_subparsers(dest="db_command", required=True)
    p = database.add_parser("rebuild", help="DEV only: drop and re-create the importer schema")
    p.add_argument("--confirm", metavar="DATABASE_NAME")
    p = database.add_parser("init", help="mark this database DEV or PROD (once)")
    p.add_argument("--environment", required=True, choices=["DEV", "PROD"])
    p.add_argument("--confirm", metavar="DATABASE_NAME")

    p = sub.add_parser("setup", help="interactive standup & setup wizard for SQL Server connection and schema")
    p.add_argument("--server", help="SQL Server hostname or IP")
    p.add_argument("--database", help="control database name (default: import_agent_dev)")
    p.add_argument("--auth", choices=["sql", "trusted", "entra"], help="authentication method")
    p.add_argument("-u", "--user", help="SQL Server username")
    p.add_argument("-p", "--password", help="SQL Server password")
    p.add_argument("--save-password", action="store_true", help="save password in database.env on disk")
    p.add_argument("--create-db", action="store_true", help="automatically create database on server if it does not exist")
    p.add_argument("--skip-schema", action="store_true", help="skip deploying sql/001_schema.sql")
    p.add_argument("--non-interactive", action="store_true", help="run non-interactively with provided flags")
    return parser


def main(argv: list[str] | None = None, store=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "setup":
        from .setup_wizard import run_setup
        return run_setup(args)
    if args.command == "db":
        return cmd_db_init(args) if args.db_command == "init" else cmd_db_rebuild(args)
    store = store if store is not None else _default_store()
    from .environment import banner, target_of
    identity = target_of(store)
    if identity is not None:
        print(banner(identity), file=sys.stderr)
    return args.func(args, store)


if __name__ == "__main__":
    sys.exit(main())
