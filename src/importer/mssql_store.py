"""SQL Server repositories (design §8.11): SQLAlchemy Core, bound parameters only.

Engine comes from importer.db.get_engine() (DataEngine). One MssqlTx per
engine.begin() transaction; every repository shares that connection.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager

import pyodbc
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .models import (
    ArtifactType,
    Declared,
    FileStatus,
    Reject,
    RejectStatus,
    Signature,
    SignatureHit,
    SignatureStatus,
    can_transition,
)
from .proposals import with_candidates
from .repositories import (
    ArtifactRow,
    ConcurrentTransition,
    IllegalTransition,
    LedgerRow,
    NotFound,
    RegistryRow,
    RowDataError,
    StagedRow,
)
from .addresses import parse
from .schema_tables import (
    LINEAGE_COLUMNS,
    add_column_sql,
    create_raw_sql,
    create_stage_sql,
    ensure_schema_sql,
    q,
    raw_name,
    stage_name,
    validate_columns,
)

USE_SETINPUTSIZES = False  # Task 2 spike decision
DATA_SQLSTATE_CLASSES = ("22", "23")
_SQLSTATE = re.compile(r"^\[?([0-9A-Z]{5})\]?")


def _sqlstate(exc: pyodbc.Error) -> str:
    for arg in exc.args:
        match = _SQLSTATE.match(arg) if isinstance(arg, str) else None
        if match:
            return match.group(1)
    return ""


def is_data_error(exc: pyodbc.Error) -> bool:
    """Row-data problem (bisect into rejects) vs infrastructure (fail the file).

    Server errors carry SQLSTATE class 22/23. pyodbc's fast_executemany also
    raises a client-side "String data, right truncation" as generic HY000
    before anything reaches the server — that is a data error too.
    """
    if _sqlstate(exc)[:2] in DATA_SQLSTATE_CLASSES:
        return True
    return any(isinstance(arg, str) and "right truncation" in arg.lower() for arg in exc.args)


def _dump(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _load(value):
    return None if value is None else json.loads(value)


def _float(value) -> float | None:
    return None if value is None else float(value)


LEDGER_COLS = (
    "file_id, idempotency_key, source, original_filename, project, [class], file_type, status, signature_id, "
    "request_context, source_path, encoding, attempts, rows_total, rows_landed, rows_rejected, "
    "percent_parsed, percent_loaded, last_error"
)


def _ledger_row(m) -> LedgerRow:
    return LedgerRow(
        file_id=m["file_id"], idempotency_key=m["idempotency_key"], source=m["source"],
        original_filename=m["original_filename"], project=m["project"], cls=m["class"], file_type=m["file_type"],
        status=FileStatus(m["status"]), signature_id=m["signature_id"], request_context=_load(m["request_context"]),
        source_path=m["source_path"], encoding=m["encoding"], attempts=m["attempts"], rows_total=m["rows_total"],
        rows_landed=m["rows_landed"], rows_rejected=m["rows_rejected"], percent_parsed=_float(m["percent_parsed"]),
        percent_loaded=_float(m["percent_loaded"]), last_error=m["last_error"],
    )


class MssqlLedger:
    def __init__(self, conn: Connection):
        self.c = conn

    def _fetch(self, where: str, params: dict) -> LedgerRow | None:
        m = self.c.execute(text(f"SELECT {LEDGER_COLS} FROM dbo.file_ledger WHERE {where}"), params).mappings().first()
        return None if m is None else _ledger_row(m)

    def create_or_get(self, *, idempotency_key, source, original_filename, source_path, declared: Declared, request_context):
        existing = self._fetch("idempotency_key = :k", {"k": idempotency_key})
        if existing is not None:
            return existing, False
        file_id = self.c.execute(text(
            "INSERT INTO dbo.file_ledger (idempotency_key, source, original_filename, source_path, project, [class], "
            "file_type, request_context) OUTPUT INSERTED.file_id "
            "VALUES (:k, :source, :name, :path, :project, :cls, :ftype, :ctx)"
        ), {"k": idempotency_key, "source": source, "name": original_filename, "path": source_path,
            "project": declared.project, "cls": declared.cls, "ftype": declared.file_type,
            "ctx": _dump(request_context)}).scalar_one()
        return self.get(file_id), True

    def get(self, file_id):
        row = self._fetch("file_id = :id", {"id": file_id})
        if row is None:
            raise NotFound(file_id)
        return row

    def transition(self, file_id, src, dst):
        if not can_transition(src, dst):
            raise IllegalTransition(f"{src.value} -> {dst.value}")
        changed = self.c.execute(text(
            "UPDATE dbo.file_ledger SET status = :dst, updated_at = SYSUTCDATETIME() WHERE file_id = :id AND status = :src"
        ), {"dst": dst.value, "src": src.value, "id": file_id}).rowcount
        if changed != 1:
            raise ConcurrentTransition(f"file {file_id} is not in status {src.value}")

    def begin_reentry(self, file_id):
        row = self.get(file_id)
        if row.status not in (FileStatus.QUARANTINED, FileStatus.FAILED):
            raise IllegalTransition(f"re-entry needs quarantined or failed, file {file_id} is {row.status.value}")
        self.transition(file_id, row.status, FileStatus.DISCOVERED)
        self.c.execute(text("UPDATE dbo.file_ledger SET attempts = attempts + 1, last_error = NULL WHERE file_id = :id"),
                       {"id": file_id})
        return self.get(file_id)

    def _set(self, file_id, column: str, value) -> None:
        self.c.execute(text(f"UPDATE dbo.file_ledger SET {column} = :v, updated_at = SYSUTCDATETIME() WHERE file_id = :id"),
                       {"v": value, "id": file_id})

    def set_signature(self, file_id, signature_id):
        self._set(file_id, "signature_id", signature_id)

    def set_encoding(self, file_id, encoding):
        self._set(file_id, "encoding", encoding)

    def set_error(self, file_id, message):
        self._set(file_id, "last_error", message)

    def record_metrics(self, file_id, *, rows_total, rows_landed, rows_rejected, percent_parsed, percent_loaded):
        self.c.execute(text(
            "UPDATE dbo.file_ledger SET rows_total = :t, rows_landed = :l, rows_rejected = :r, percent_parsed = :pp, "
            "percent_loaded = CASE WHEN percent_loaded IS NULL OR :pl > percent_loaded THEN :pl ELSE percent_loaded END, "
            "updated_at = SYSUTCDATETIME() WHERE file_id = :id"
        ), {"t": rows_total, "l": rows_landed, "r": rows_rejected, "pp": percent_parsed, "pl": percent_loaded, "id": file_id})

    def files_for_signature(self, signature_id):
        rows = self.c.execute(text(
            f"SELECT {LEDGER_COLS} FROM dbo.file_ledger WHERE signature_id = :s ORDER BY file_id"
        ), {"s": signature_id}).mappings().all()
        return [_ledger_row(m) for m in rows]


REG_COLS = "signature_id, fingerprint, physical_part, xml_namespace, status, mapper_version_id, observed_schema, proposal, approved_by"


def _registry_row(m) -> RegistryRow:
    return RegistryRow(
        signature_id=m["signature_id"], fingerprint=m["fingerprint"], physical_part=m["physical_part"],
        xml_namespace=m["xml_namespace"], status=SignatureStatus(m["status"]), mapper_version_id=m["mapper_version_id"],
        observed_schema=_load(m["observed_schema"]), proposal=_load(m["proposal"]), approved_by=m["approved_by"],
    )


class MssqlRegistry:
    def __init__(self, conn: Connection):
        self.c = conn

    def _by_fp(self, fingerprint: str) -> RegistryRow | None:
        m = self.c.execute(text(f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE fingerprint = :fp"),
                           {"fp": fingerprint}).mappings().first()
        return None if m is None else _registry_row(m)

    def lookup(self, fingerprint, xml_namespace):
        row = self._by_fp(fingerprint)
        if row is None or row.status is not SignatureStatus.ACTIVE or row.mapper_version_id is None:
            return None
        if row.xml_namespace != xml_namespace:
            return None
        return SignatureHit(row.signature_id, row.fingerprint, row.mapper_version_id, row.xml_namespace)

    def get(self, signature_id):
        m = self.c.execute(text(f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE signature_id = :id"),
                           {"id": signature_id}).mappings().first()
        if m is None:
            raise NotFound(signature_id)
        return _registry_row(m)

    def find_active_by_physical_part(self, physical_part, xml_namespace):
        rows = self.c.execute(text(
            f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE physical_part = :p AND status = 'active'"
        ), {"p": physical_part}).mappings().all()
        return [r for r in (_registry_row(m) for m in rows) if r.xml_namespace == xml_namespace]

    def propose(self, sig: Signature, candidates):
        existing = self._by_fp(sig.fingerprint)
        if existing is not None:
            if existing.status is SignatureStatus.PROPOSED:
                self.c.execute(text(
                    "UPDATE dbo.signature_registry SET observed_schema = :obs, proposal = :prop WHERE signature_id = :id"
                ), {"obs": _dump(sig.observed), "prop": _dump(with_candidates(existing.proposal, candidates)),
                    "id": existing.signature_id})
            return existing.signature_id
        return self.c.execute(text(
            "INSERT INTO dbo.signature_registry (fingerprint, physical_part, xml_namespace, status, observed_schema, "
            "proposal, provenance) OUTPUT INSERTED.signature_id "
            "VALUES (:fp, :part, :ns, 'proposed', :obs, :prop, 'profile_match')"
        ), {"fp": sig.fingerprint, "part": sig.physical_part, "ns": sig.xml_namespace,
            "obs": _dump(sig.observed), "prop": _dump(with_candidates(None, candidates))}).scalar_one()

    def list_by_status(self, status):
        rows = self.c.execute(text(
            f"SELECT {REG_COLS} FROM dbo.signature_registry WHERE status = :s ORDER BY signature_id"
        ), {"s": status.value}).mappings().all()
        return [_registry_row(m) for m in rows]

    def set_proposal(self, signature_id, proposal):
        self.c.execute(text("UPDATE dbo.signature_registry SET proposal = :p WHERE signature_id = :id"),
                       {"p": _dump(proposal), "id": signature_id})

    def set_observed(self, signature_id, observed):
        self.c.execute(text("UPDATE dbo.signature_registry SET observed_schema = :o WHERE signature_id = :id"),
                       {"o": _dump(observed), "id": signature_id})

    def activate(self, signature_id, mapper_version_id, approved_by):
        self.c.execute(text(
            "UPDATE dbo.signature_registry SET status = 'active', mapper_version_id = :mv, approved_by = :by, "
            "approved_at = SYSUTCDATETIME() WHERE signature_id = :id"
        ), {"mv": mapper_version_id, "by": approved_by, "id": signature_id})

    def register(self, sig: Signature, mapper_version_id, approved_by):
        existing = self._by_fp(sig.fingerprint)
        params = {"fp": sig.fingerprint, "part": sig.physical_part, "ns": sig.xml_namespace,
                  "obs": _dump(sig.observed), "mv": mapper_version_id, "by": approved_by}
        if existing is not None:
            self.c.execute(text(
                "UPDATE dbo.signature_registry SET status = 'active', mapper_version_id = :mv, physical_part = :part, "
                "xml_namespace = :ns, observed_schema = :obs, approved_by = :by, approved_at = SYSUTCDATETIME() "
                "WHERE fingerprint = :fp"
            ), params)
            return existing.signature_id
        return self.c.execute(text(
            "INSERT INTO dbo.signature_registry (fingerprint, physical_part, xml_namespace, status, mapper_version_id, "
            "observed_schema, provenance, approved_by, approved_at) OUTPUT INSERTED.signature_id "
            "VALUES (:fp, :part, :ns, 'active', :mv, :obs, 'hand_registration', :by, SYSUTCDATETIME())"
        ), params).scalar_one()


ART_COLS = "version_id, artifact_type, artifact_key, version_no, definition, is_active, created_by, content_sha256, approved_by"


def _artifact_row(m) -> ArtifactRow:
    return ArtifactRow(
        version_id=m["version_id"], artifact_type=ArtifactType(m["artifact_type"]), artifact_key=m["artifact_key"],
        version_no=m["version_no"], definition=json.loads(m["definition"]), is_active=bool(m["is_active"]),
        created_by=m["created_by"], content_sha256=m["content_sha256"], approved_by=m["approved_by"],
    )


class MssqlArtifacts:
    def __init__(self, conn: Connection):
        self.c = conn

    def put_version(self, artifact_type, artifact_key, definition, *, created_by, activate=True, content_sha256=None):
        params = {"t": artifact_type.value, "k": artifact_key}
        version_no = self.c.execute(text(
            "SELECT ISNULL(MAX(version_no), 0) + 1 FROM dbo.artifact_versions WITH (UPDLOCK, HOLDLOCK) "
            "WHERE artifact_type = :t AND artifact_key = :k"
        ), params).scalar_one()
        version_id = self.c.execute(text(
            "INSERT INTO dbo.artifact_versions (artifact_type, artifact_key, version_no, definition, is_active, "
            "content_sha256, created_by) OUTPUT INSERTED.version_id VALUES (:t, :k, :n, :d, 0, :sha, :by)"
        ), {**params, "n": version_no, "d": json.dumps(definition, ensure_ascii=False), "sha": content_sha256,
            "by": created_by}).scalar_one()
        if activate:
            self.c.execute(text(
                "UPDATE dbo.artifact_versions SET is_active = CASE WHEN version_id = :vid THEN 1 ELSE 0 END "
                "WHERE artifact_type = :t AND artifact_key = :k"
            ), {**params, "vid": version_id})
        return self.get(version_id)

    def get(self, version_id):
        m = self.c.execute(text(f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE version_id = :id"),
                           {"id": version_id}).mappings().first()
        if m is None:
            raise NotFound(version_id)
        return _artifact_row(m)

    def active_schema(self, schema_id):
        m = self.c.execute(text(
            f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE artifact_type = 'schema' AND artifact_key = :k AND is_active = 1"
        ), {"k": schema_id}).mappings().first()
        return None if m is None else _artifact_row(m)

    def active_schemas(self):
        rows = self.c.execute(text(
            f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE artifact_type = 'schema' AND is_active = 1"
        )).mappings().all()
        return [_artifact_row(m) for m in rows]

    def active(self, artifact_type, key):
        m = self.c.execute(text(
            f"SELECT {ART_COLS} FROM dbo.artifact_versions WHERE artifact_type = :t AND artifact_key = :k AND is_active = 1"
        ), {"t": artifact_type.value, "k": key}).mappings().first()
        return None if m is None else _artifact_row(m)

    def activate(self, version_id, *, approved_by):
        row = self.get(version_id)
        self.c.execute(text(
            "UPDATE dbo.artifact_versions SET is_active = CASE WHEN version_id = :vid THEN 1 ELSE 0 END "
            "WHERE artifact_type = :t AND artifact_key = :k"
        ), {"vid": version_id, "t": row.artifact_type.value, "k": row.artifact_key})
        self.c.execute(text("UPDATE dbo.artifact_versions SET approved_by = :by WHERE version_id = :vid"),
                       {"by": approved_by, "vid": version_id})
        return self.get(version_id)


REJECT_COLS = "file_id, row_identity, raw_payload, reason_code, schema_id, reason_detail, status, created_at, reclaimed_at"


def _reject(m) -> Reject:
    return Reject(
        file_id=m["file_id"], row_identity=m["row_identity"], raw_payload=m["raw_payload"], reason_code=m["reason_code"],
        schema_id=m["schema_id"], reason_detail=m["reason_detail"], status=RejectStatus(m["status"]),
        created_at=m["created_at"], reclaimed_at=m["reclaimed_at"],
    )


class MssqlRejectStore:
    def __init__(self, conn: Connection):
        self.c = conn

    def add(self, reject: Reject) -> bool:
        inserted = self.c.execute(text(
            "INSERT INTO dbo.reject_repository (file_id, schema_id, row_identity, raw_payload, reason_code, reason_detail, status) "
            "SELECT :f, :s, :rid, :p, :rc, :rd, :st WHERE NOT EXISTS "
            "(SELECT 1 FROM dbo.reject_repository WHERE file_id = :f AND row_identity = :rid)"
        ), {"f": reject.file_id, "s": reject.schema_id, "rid": reject.row_identity, "p": reject.raw_payload,
            "rc": reject.reason_code, "rd": reject.reason_detail, "st": reject.status.value}).rowcount
        return inserted == 1

    def open_rejects_for_schema(self, schema_id: str) -> list[Reject]:
        rows = self.c.execute(text(
            f"SELECT {REJECT_COLS} FROM dbo.reject_repository WHERE schema_id = :s AND status = 'rejected'"
        ), {"s": schema_id}).mappings().all()
        return [_reject(m) for m in rows]

    def mark_reclaimed(self, file_id: int, row_identity: str) -> bool:
        changed = self.c.execute(text(
            "UPDATE dbo.reject_repository SET status = 'reclaimed', reclaimed_at = SYSUTCDATETIME() "
            "WHERE file_id = :f AND row_identity = :rid AND status = 'rejected'"
        ), {"f": file_id, "rid": row_identity}).rowcount
        return changed == 1

    def all_rejects(self) -> list[Reject]:
        return [_reject(m) for m in self.c.execute(text(f"SELECT {REJECT_COLS} FROM dbo.reject_repository")).mappings().all()]

    def count_open_for_file(self, file_id: int) -> int:
        return self.c.execute(text(
            "SELECT COUNT(*) FROM dbo.reject_repository WHERE file_id = :f AND status = 'rejected'"
        ), {"f": file_id}).scalar_one()


class MssqlAudit:
    def __init__(self, conn: Connection):
        self.c = conn

    def quarantine(self, file_id, reason_code, detail, signature_id):
        self.c.execute(text(
            "INSERT INTO dbo.file_quarantine (file_id, reason_code, reason_detail, signature_id) VALUES (:f, :rc, :d, :s)"
        ), {"f": file_id, "rc": reason_code, "d": detail, "s": signature_id})

    def decision(self, file_id, action_type, observed, proposed, applied, approval):
        self.c.execute(text(
            "INSERT INTO dbo.decision_log (file_id, action_type, observed, proposed, applied, approval) "
            "VALUES (:f, :a, :o, :p, :ap, :appr)"
        ), {"f": file_id, "a": action_type, "o": _dump(observed), "p": _dump(proposed), "ap": 1 if applied else 0,
            "appr": approval})


class MssqlRawZone:
    def __init__(self, conn: Connection):
        self.c = conn
        self._columns: dict[str, list[str]] = {}

    def _table_columns(self, schema: str, table: str) -> list[str] | None:
        names = self.c.execute(text(
            "SELECT c.name FROM sys.columns AS c JOIN sys.tables AS t ON t.object_id = c.object_id "
            "JOIN sys.schemas AS s ON s.schema_id = t.schema_id WHERE s.name = :s AND t.name = :t ORDER BY c.column_id"
        ), {"s": schema, "t": table}).scalars().all()
        return list(names) or None

    def _canonical(self, target: str) -> list[str]:
        a = parse(target)
        if a.key not in self._columns:
            existing = self._table_columns("stage", a.stage_table)
            if existing is None:
                raise RuntimeError(f"{stage_name(a)} does not exist; ensure_tables was never called for {a.key}")
            self._columns[a.key] = [c for c in existing if not c.startswith("_")]
        return self._columns[a.key]

    def ensure_tables(self, schema_id, columns):
        a = parse(schema_id)
        validate_columns(columns)
        self.c.exec_driver_sql(ensure_schema_sql(a.raw_schema))
        self.c.exec_driver_sql(ensure_schema_sql("stage"))
        for zone, schema, table, create in (("raw", a.raw_schema, a.table, create_raw_sql),
                                            ("stage", "stage", a.stage_table, create_stage_sql)):
            existing = self._table_columns(schema, table)
            if existing is None:
                self.c.exec_driver_sql(create(a, columns))
                continue
            have = {c.lower() for c in existing}
            for column in columns:
                if column.lower() not in have:
                    self.c.exec_driver_sql(add_column_sql(zone, a, column))
        self._columns.pop(a.key, None)

    def clear_stage(self, file_id, target):
        self.c.execute(text(f"DELETE FROM {stage_name(parse(target))} WHERE _file_id = :f"), {"f": file_id})

    def stage_batch(self, target, rows: list[StagedRow]):
        columns = self._canonical(target)
        names = LINEAGE_COLUMNS + columns
        sql = (f"INSERT INTO {stage_name(parse(target))} ({', '.join(q(n) for n in names)}) "
               f"VALUES ({', '.join('?' for _ in names)})")
        params = [
            (r.file_id, r.row_identity, r.signature_id, r.artifact_version_id, r.extras_json,
             *(r.fields.get(c) for c in columns))
            for r in rows
        ]
        with self.c.begin_nested():
            cursor = self.c.connection.cursor()
            try:
                cursor.fast_executemany = True
                if USE_SETINPUTSIZES:
                    cursor.setinputsizes(
                        [(pyodbc.SQL_BIGINT, 0, 0), (pyodbc.SQL_WVARCHAR, 256, 0), (pyodbc.SQL_BIGINT, 0, 0),
                         (pyodbc.SQL_BIGINT, 0, 0)] + [(pyodbc.SQL_WVARCHAR, 0, 0)] * (len(names) - 4)
                    )
                cursor.executemany(sql, params)
            except pyodbc.Error as exc:
                if is_data_error(exc):
                    raise RowDataError(str(exc)) from exc
                raise
            finally:
                cursor.close()

    def promote(self, file_id, target):
        a = parse(target)
        names = LINEAGE_COLUMNS + self._canonical(target)
        column_list = ", ".join(q(n) for n in names)
        inserted = self.c.execute(text(
            f"INSERT INTO {raw_name(a)} ({column_list}) "
            f"SELECT {', '.join('s.' + q(n) for n in names)} FROM {stage_name(a)} AS s "
            f"WHERE s._file_id = :f AND NOT EXISTS (SELECT 1 FROM {raw_name(a)} AS r "
            f"WHERE r._file_id = s._file_id AND r._row_identity = s._row_identity)"
        ), {"f": file_id}).rowcount
        self.c.execute(text(f"DELETE FROM {stage_name(a)} WHERE _file_id = :f"), {"f": file_id})
        return inserted

    def count_landed(self, file_id, target):
        a = parse(target)
        if self._table_columns(a.raw_schema, a.table) is None:
            return 0
        return self.c.execute(text(f"SELECT COUNT(*) FROM {raw_name(a)} WHERE _file_id = :f"), {"f": file_id}).scalar_one()


class MssqlTx:
    def __init__(self, conn: Connection):
        self.conn = conn
        self.ledger = MssqlLedger(conn)
        self.registry = MssqlRegistry(conn)
        self.artifacts = MssqlArtifacts(conn)
        self.rejects = MssqlRejectStore(conn)
        self.raw = MssqlRawZone(conn)
        self.audit = MssqlAudit(conn)


class MssqlStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def transaction(self) -> Iterator[MssqlTx]:
        with self.engine.begin() as conn:
            yield MssqlTx(conn)
