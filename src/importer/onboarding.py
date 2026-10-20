"""Confirm-and-bind onboarding (design §8.18).

The deterministic core behind the MCP server: what is waiting, what a file looks
like, the cheapest draft (reuse / exact / fallback), previews, and (Task 10)
submit, reject and accept. Writes only versioned data; a draft mapper is an
inactive version that routing never uses until a person accepts it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import locators
from .addresses import parse
from .mappers.base import RunContext
from .masking import SAMPLE_ROWS, mask, mask_row
from .mappers.preview import preview
from .mappers.loading import declarative_mapper
from .mappers.spec import SpecError
from .mappers.tree import outline, tables_in
from .models import ArtifactType, FileStatus, SignatureStatus
from .profiler import Candidate, profile
from .proposals import normalize
from .readers import parse_document
from .readers.base import ReaderError, XmlDocument, normalize_name
from .readers.xml_reader import local
from .repositories import LedgerRow, NotFound, RegistryRow
from .settings import load as load_settings
from .trees import build_tree, subtree, tree_warnings



class OnboardingError(ValueError):
    """The request does not fit the proposal's current state."""


PROFILE_DEPTH = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _proposed(tx, signature_id: int) -> RegistryRow:
    try:
        row = tx.registry.get(signature_id)
    except NotFound:
        raise OnboardingError(f"no signature {signature_id}") from None
    if row.status is not SignatureStatus.PROPOSED:
        raise OnboardingError(f"signature {signature_id} is {row.status.value}, not proposed")
    return row


def _trigger(tx, signature_id: int) -> LedgerRow | None:
    rows = tx.ledger.files_for_signature(signature_id)
    waiting = [r for r in rows if r.status is FileStatus.QUARANTINED]
    return (waiting or rows or [None])[0]


def _document(row: LedgerRow):
    return parse_document(locators.read_verified(row.source_path, row.idempotency_key), row.file_type)


def _observed_tables(observed: dict | None) -> dict[str, list[str]] | None:
    if not observed or "root" in observed:
        return None
    if "sheets" in observed:
        return dict(observed["sheets"])
    return {"*": list(observed.get("columns") or observed.get("keys") or [])}


def _targets(definition: dict) -> list[str]:
    kind = definition.get("kind")
    if kind == "tree":
        return tables_in(definition)
    if kind not in ("xml", "tabular"):
        return []
    return [e["target"] for e in definition.get("extractions", []) if isinstance(e, dict) and "target" in e]


def _suggest(tx, row: RegistryRow) -> dict | None:
    candidates = normalize(row.proposal)["candidates"]
    reuse = next((c for c in candidates if c.get("kind") == "reuse_mapper"), None)
    if reuse:
        mapper = tx.artifacts.get(reuse["mapper_version_id"])
        return {"path": "reuse", "spec": mapper.definition, "mapper_version_id": mapper.version_id}
    tables = _observed_tables(row.observed_schema)
    if not tables:
        return None
    best: dict[str, dict] = {}
    for c in candidates:
        if c.get("kind") == "schema" and c.get("table") in tables:
            current = best.get(c["table"])
            if current is None or (c["score"], c["jaccard"]) > (current["score"], current["jaccard"]):
                best[c["table"]] = c
    if not best:
        return None
    exact = set(best) == set(tables) and all(not c["extra"] for c in best.values())
    spec = {"kind": "tabular", "extractions": [
        {"target": c["schema_id"], "records": table, "auto": True, "unmapped": "extras"} for table, c in best.items()
    ]}
    return {"path": "exact" if exact else "fallback", "spec": spec}


def _file_info(row: LedgerRow | None) -> dict | None:
    if row is None:
        return None
    return {"file_id": row.file_id, "name": row.original_filename, "file_type": row.file_type, "locator": row.source_path}


def pending(store) -> list[dict]:
    out: list[dict] = []
    with store.transaction() as tx:
        for row in tx.registry.list_by_status(SignatureStatus.PROPOSED):
            proposal = normalize(row.proposal)
            waiting = [f for f in tx.ledger.files_for_signature(row.signature_id) if f.status is FileStatus.QUARANTINED]
            suggestion = _suggest(tx, row)
            out.append({
                "signature_id": row.signature_id,
                "fingerprint": row.fingerprint,
                "state": proposal["review"]["state"],
                "suggested_path": suggestion["path"] if suggestion else "model",
                "candidates": [Candidate(**c).summary() for c in proposal["candidates"][:3]],
                "waiting_files": len(waiting),
                "trigger_file": _file_info(_trigger(tx, row.signature_id)),
            })
    return out


def stranded(store) -> list[dict]:
    """Files still quarantined under a signature that is already active (a retry failed, or
    the file arrived while its signature was being bound). `importer retry` lands them."""
    out: list[dict] = []
    with store.transaction() as tx:
        for sig in tx.registry.list_by_status(SignatureStatus.ACTIVE):
            for f in tx.ledger.files_for_signature(sig.signature_id):
                if f.status is FileStatus.QUARANTINED:
                    out.append({**_file_info(f), "signature_id": sig.signature_id, "fingerprint": sig.fingerprint,
                                "last_error": f.last_error, "retry": f"importer retry {f.file_id}"})
    return out


def file_profile(store, signature_id: int) -> dict:
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        trigger = _trigger(tx, signature_id)
        suggestion = _suggest(tx, row)
    proposal = normalize(row.proposal)
    result = {
        "signature_id": signature_id, "fingerprint": row.fingerprint, "observed": row.observed_schema,
        "candidates": proposal["candidates"], "suggested_draft": suggestion, "review": proposal["review"],
        "trigger_file": _file_info(trigger), "profile": None,
    }
    if trigger is not None:
        try:
            prof = profile(_document(trigger))
            if prof.get("kind") == "xml":
                prof["tree"] = subtree(prof["tree"], depth=PROFILE_DEPTH)
            result["profile"] = prof
        except (locators.LocatorError, ReaderError) as exc:
            result["profile_error"] = str(exc)
    return result


def tree(store, signature_id: int, path: str | None = None, depth: int = PROFILE_DEPTH) -> dict:
    """A signature's nesting tree (names, counts, repeats, attribute names; never values), paged by path/depth."""
    with store.transaction() as tx:
        try:
            row = tx.registry.get(signature_id)
        except NotFound:
            raise OnboardingError(f"no signature {signature_id}") from None
        trigger = _trigger(tx, signature_id)
    stored = (row.observed_schema or {}).get("tree")
    if stored is None:
        if trigger is None:
            raise OnboardingError(f"signature {signature_id} has no stored tree and no file to profile")
        doc = _document(trigger)
        if not isinstance(doc, XmlDocument):
            raise OnboardingError(f"signature {signature_id} is not an XML signature")
        stored = build_tree(doc)
    try:
        return subtree(stored, path, max(1, int(depth)))
    except KeyError as exc:
        raise OnboardingError(str(exc.args[0])) from None


def samples(store, signature_id: int, columns: list[str]) -> dict[str, list[str]]:
    with store.transaction() as tx:
        _proposed(tx, signature_id)
        trigger = _trigger(tx, signature_id)
        cfg = load_settings(tx.artifacts)
    if trigger is None:
        raise OnboardingError(f"no file is waiting under signature {signature_id}")
    doc = _document(trigger)
    wanted: dict[str, list[str]] = {c: [] for c in columns}

    def add(column: str, value) -> None:
        bucket = wanted[column]
        if value is None or value == "" or len(bucket) >= cfg.max_values:
            return
        text = mask(value, cfg.max_chars)  # shapes only: row values never cross MCP
        if text not in bucket:
            bucket.append(text)

    if isinstance(doc, XmlDocument):
        for el in doc.root.iter():
            if not isinstance(el.tag, str):
                continue
            path = "/" + "/".join(local(node) for node in reversed([el, *el.iterancestors()]))
            if path in wanted and el.text and el.text.strip():
                add(path, el.text.strip())
    else:
        for table in doc.tables:
            for record in table.records:
                for column in columns:
                    if column in record.values:
                        add(column, record.values[column])
    return wanted


def search_schemas(store, columns: list[str] | None = None, text: str | None = None, limit: int = 10) -> list[dict]:
    with store.transaction() as tx:
        schemas = sorted((a.artifact_key, a.definition["columns"]) for a in tx.artifacts.active_schemas())
    if columns:
        observed = {normalize_name(c) for c in columns} - {""}
        ranked = []
        for address, cols in schemas:
            canonical = {normalize_name(c) for c in cols}
            common = observed & canonical
            if not common:
                continue
            ranked.append({
                "address": address, "score": round(len(common) / len(observed), 2),
                "jaccard": round(len(common) / len(observed | canonical), 2),
                "matched": sorted(common), "schema_columns_not_in_file": len(canonical - observed),
            })
        ranked.sort(key=lambda r: (-r["score"], -r["jaccard"], r["address"]))
        return ranked[:limit]
    needle = (text or "").lower()
    return [{"address": address, "columns": len(cols)} for address, cols in schemas if needle in address][:limit]


def get_schema(store, address: str) -> dict:
    key = parse(address).key
    with store.transaction() as tx:
        row = tx.artifacts.active_schema(key)
    if row is None:
        raise OnboardingError(f"no registered schema {key}")
    return {"address": key, "version_no": row.version_no, "columns": row.definition["columns"],
            "source": row.definition.get("source")}


def prior_mappings(store, address: str) -> list[dict]:
    key = parse(address).key
    out: list[dict] = []
    with store.transaction() as tx:
        for sig in tx.registry.list_by_status(SignatureStatus.ACTIVE):
            if sig.mapper_version_id is None:
                continue
            mapper = tx.artifacts.get(sig.mapper_version_id)
            if key in _targets(mapper.definition):
                out.append({"fingerprint": sig.fingerprint, "mapper_version_id": mapper.version_id, "spec": mapper.definition})
    return out


def _preview(trigger: LedgerRow, signature_id: int, spec, schemas: dict[str, list[str]], max_chars: int,
             stored_tree: dict | None = None) -> dict:
    if not isinstance(spec, dict):
        return {"errors": ["spec must be a JSON object"]}
    if spec.get("kind") == "plugin":
        return {"errors": ["plugin mappers cannot be submitted through onboarding (design §8.22); use a declarative spec"]}
    try:
        mapper = declarative_mapper(spec, known_schemas=set(schemas))
        ctx = RunContext(trigger.file_id, signature_id, 0, trigger.request_context or {},
                         {t: schemas[t] for t in mapper.targets})
        doc = _document(trigger)
        result = preview(mapper, doc, ctx, n=SAMPLE_ROWS)
    except (SpecError, locators.LocatorError, ReaderError) as exc:
        return {"errors": [str(exc)]}
    out = {
        "file_id": trigger.file_id, "counts": result.counts, "extras_keys": result.extras_keys,
        "rejects": result.rejects,
        "sample": {t: [mask_row(row) for row in rows[:SAMPLE_ROWS]] for t, rows in result.sample.items()},
    }
    if spec.get("kind") == "tree":
        out["counts"] = {t: result.counts.get(t, 0) for t in mapper.targets}
        tree_doc = stored_tree or (build_tree(doc) if isinstance(doc, XmlDocument) else None)
        out["tree_warnings"] = tree_warnings(tree_doc, spec) if tree_doc else []
    return out


def _preview_inputs(tx, signature_id: int):
    trigger = _trigger(tx, signature_id)
    schemas = {a.artifact_key: a.definition["columns"] for a in tx.artifacts.active_schemas()}
    stored_tree = (tx.registry.get(signature_id).observed_schema or {}).get("tree")
    return trigger, schemas, load_settings(tx.artifacts).max_chars, stored_tree


def preview_spec(store, signature_id: int, spec) -> dict:
    with store.transaction() as tx:
        _proposed(tx, signature_id)
        trigger, schemas, max_chars, stored_tree = _preview_inputs(tx, signature_id)
    if trigger is None:
        raise OnboardingError(f"no file is waiting under signature {signature_id}")
    return _preview(trigger, signature_id, spec, schemas, max_chars, stored_tree)


PATHS = ("reuse", "exact", "fallback", "model")


def review_table(fingerprint: str, spec: dict, rationale: list[dict], result: dict) -> str:
    lines = [f"**{fingerprint}** -> {', '.join(_targets(spec)) or '(no targets)'}", "",
             "| file column | target | how | evidence | confidence |", "|---|---|---|---|---|"]
    for item in rationale or []:
        lines.append("| " + " | ".join(str(item.get(k, "")) for k in ("column", "target", "how", "evidence", "confidence")) + " |")
    counts = ", ".join(f"{t}: {n} rows" for t, n in result.get("counts", {}).items()) or "no rows"
    extras = "; ".join(f"{t}: {', '.join(keys)}" for t, keys in result.get("extras_keys", {}).items()) or "none"
    rejects = ", ".join(f"{code}: {n}" for code, n in result.get("rejects", {}).items()) or "none"
    lines += ["", f"Preview: {counts}. _extras: {extras}. Rejects: {rejects}."]
    if spec.get("kind") == "tree":
        lines += ["", "Tables:"] + [f"- {t['table']} <- {t['path']} (carries: {', '.join(t['carries']) or 'nothing'})"
                                    for t in outline(spec)]
        if result.get("tree_warnings"):
            lines += ["", "Tree warnings:"] + [f"- {w}" for w in result["tree_warnings"]]
    return "\n".join(lines)


def submit(store, signature_id: int, spec, rationale: list[dict], *, by: str, note: str | None = None,
           path: str = "model") -> dict:
    if path not in PATHS:
        raise OnboardingError(f"path must be one of {PATHS}, got {path!r}")
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        trigger, schemas, max_chars, stored_tree = _preview_inputs(tx, signature_id)
    if trigger is None:
        raise OnboardingError(f"no file is waiting under signature {signature_id}")
    result = _preview(trigger, signature_id, spec, schemas, max_chars, stored_tree)
    if "errors" in result:
        return result
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        proposal = normalize(row.proposal)
        review = proposal["review"]
        if review["state"] == "accepted":
            raise OnboardingError(f"signature {signature_id} is already accepted")
        draft = tx.artifacts.put_version(ArtifactType.MAPPER, f"sig.{signature_id}", spec, created_by=by, activate=False)
        review.update(state="ready_for_review", draft_version_id=draft.version_id, path=path,
                      rationale=list(rationale or []), preview=result)
        if note:
            review["notes"].append({"at": _now(), "by": by, "text": note})
        review["history"].append({"at": _now(), "by": by, "event": "draft", "draft_version_id": draft.version_id})
        tx.registry.set_proposal(signature_id, proposal)
        tx.audit.decision(trigger.file_id, "draft_mapping", observed={"signature": row.fingerprint},
                          proposed={"draft_version_id": draft.version_id, "path": path, "rationale": review["rationale"]},
                          applied=False, approval=None)
    return {"draft_version_id": draft.version_id, "preview": result,
            "review_table": review_table(row.fingerprint, spec, rationale, result)}


def reject(store, signature_id: int, reason: str, *, by: str) -> dict:
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        proposal = normalize(row.proposal)
        review = proposal["review"]
        if review["state"] == "accepted":
            raise OnboardingError(f"signature {signature_id} is already accepted")
        review["state"] = "rejected"
        review["notes"].append({"at": _now(), "by": by, "text": reason})
        review["history"].append({"at": _now(), "by": by, "event": "reject", "draft_version_id": review["draft_version_id"]})
        tx.registry.set_proposal(signature_id, proposal)
        trigger = _trigger(tx, signature_id)
        tx.audit.decision(trigger.file_id if trigger else None, "reject_mapping", observed={"signature": row.fingerprint},
                          proposed={"reason": reason}, applied=False, approval=by)
    return {"signature_id": signature_id, "state": "rejected"}


def _canonical(definition: dict) -> str:
    return json.dumps(definition, sort_keys=True, separators=(",", ":"))


def _identical_bound(tx, draft) -> int | None:
    wanted = _canonical(draft.definition)
    for sig in tx.registry.list_by_status(SignatureStatus.ACTIVE):
        if sig.mapper_version_id is None or sig.mapper_version_id == draft.version_id:
            continue
        if _canonical(tx.artifacts.get(sig.mapper_version_id).definition) == wanted:
            return sig.mapper_version_id
    return None


def bind_summary(store, signature_id: int, draft_version_id: int) -> str:
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        draft = tx.artifacts.get(draft_version_id)
        waiting = [f for f in tx.ledger.files_for_signature(signature_id) if f.status is FileStatus.QUARANTINED]
    targets = ", ".join(_targets(draft.definition)) or "its mapper"
    return f"Bind {row.fingerprint} to {targets} and retry {len(waiting)} waiting file(s)?"


def _default_retry():
    from .pipeline import retry
    return retry


def accept(store, signature_id: int, draft_version_id: int, *, by: str, retry_fn=None) -> dict:
    retry_fn = retry_fn or _default_retry()
    with store.transaction() as tx:
        row = _proposed(tx, signature_id)
        proposal = normalize(row.proposal)
        review = proposal["review"]
        if review["state"] != "ready_for_review":
            raise OnboardingError(f"signature {signature_id} has no draft ready for review (state {review['state']})")
        if review["draft_version_id"] != draft_version_id:
            raise OnboardingError(
                f"draft {draft_version_id} is not the latest draft ({review['draft_version_id']}); review the latest draft"
            )
        draft = tx.artifacts.get(draft_version_id)
        bound = _identical_bound(tx, draft)
        if bound is None:
            tx.artifacts.activate(draft_version_id, approved_by=by)
            bound = draft_version_id
        tx.registry.activate(signature_id, bound, approved_by=by)
        review["state"] = "accepted"
        review["history"].append({"at": _now(), "by": by, "event": "accept", "draft_version_id": draft_version_id,
                                  "mapper_version_id": bound})
        tx.registry.set_proposal(signature_id, proposal)
        waiting = [f.file_id for f in tx.ledger.files_for_signature(signature_id) if f.status is FileStatus.QUARANTINED]
        tx.audit.decision(waiting[0] if waiting else None, "bind_signature",
                          observed={"signature": row.fingerprint, "draft_version_id": draft_version_id},
                          proposed={"mapper_version_id": bound, "waiting_files": waiting}, applied=True, approval=by)
    files: list[dict] = []
    tried: set[int] = set()
    # Two passes: the second picks up files quarantined under this signature by a concurrent
    # process() that read the registry before the bind committed. Anything still left is
    # surfaced by stranded().
    for _ in range(2):
        with store.transaction() as tx:
            todo = [f.file_id for f in tx.ledger.files_for_signature(signature_id)
                    if f.status is FileStatus.QUARANTINED and f.file_id not in tried]
        for file_id in todo:
            tried.add(file_id)
            try:
                files.append(retry_fn(file_id, store=store).to_dict())
            except Exception as exc:  # reported per file; the bind stands
                files.append({"file_id": file_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"signature_id": signature_id, "mapper_version_id": bound, "reused_existing": bound != draft_version_id,
            "files": files}
