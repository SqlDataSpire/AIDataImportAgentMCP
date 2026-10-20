"""Miss handling: observed-schema profile and "looks like" matching (design §8.4).

Deterministic — no model. Candidates, best first:
  1. reuse_mapper — an active signature with the same physical part (and XML
     namespace) under another project/class, e.g. another state's template.
  2. schema — tabular files whose columns are largely found in a schema.
     Score = share of the file's columns present in the schema (Jaccard as the
     tie-break); mirrored tables carry lineage/context columns files never have.
  3. record — XML only: each record-like node of the nesting tree (§8.24) with its
     best-matching schema, by the same score over its leaf names (at most 20).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .models import Signature
from .readers.base import Document, XmlDocument, normalize_name
from .trees import build_tree

MAX_RECORD_CANDIDATES = 20


def profile(doc: Document) -> dict:
    if isinstance(doc, XmlDocument):
        return {"kind": "xml", "root": doc.local_name, "namespace": doc.namespace, "tree": build_tree(doc)}
    return {"kind": "tabular", "format": doc.format, "tables": {t.name: t.columns for t in doc.tables}}


@dataclass
class Candidate:
    kind: str  # reuse_mapper | schema | record (table = the record node's path)
    score: float
    signature_id: int | None = None
    fingerprint: str | None = None
    mapper_version_id: int | None = None
    schema_id: str | None = None
    table: str | None = None
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    jaccard: float = 0.0

    def summary(self) -> str:
        if self.kind == "reuse_mapper":
            return f"looks like {self.fingerprint} -> reuse mapper {self.mapper_version_id}"
        parts = [f"{self.score:.2f}"]
        if self.extra:
            parts.append("extra: " + ", ".join(self.extra))
        if self.missing:
            parts.append("missing: " + ", ".join(self.missing))
        where = f" at {self.table}" if self.kind == "record" else ""
        return f"looks like {self.schema_id}{where} ({'; '.join(parts)})"

    def to_dict(self) -> dict:
        return asdict(self)


def match(sig: Signature, prof: dict, registry, artifacts, *, limit: int = 5, min_score: float = 0.5) -> list[Candidate]:
    candidates = [
        Candidate("reuse_mapper", 1.0, signature_id=r.signature_id, fingerprint=r.fingerprint,
                  mapper_version_id=r.mapper_version_id)
        for r in registry.find_active_by_physical_part(sig.physical_part, sig.xml_namespace)
        if r.fingerprint != sig.fingerprint
    ]
    schemas = [(a.artifact_key, a.definition["columns"]) for a in artifacts.active_schemas()]
    if prof["kind"] == "tabular":
        for table_name, columns in prof["tables"].items():
            observed = set(columns)
            if not observed:
                continue
            for schema_id, schema_columns in schemas:
                canonical = {normalize_name(c) for c in schema_columns}
                common = observed & canonical
                score = round(len(common) / len(observed), 2)
                if score < min_score:
                    continue
                candidates.append(Candidate(
                    "schema", score, schema_id=schema_id, table=table_name,
                    matched=sorted(common), missing=sorted(canonical - observed), extra=sorted(observed - canonical),
                    jaccard=round(len(common) / len(observed | canonical), 2),
                ))
    candidates.sort(key=lambda c: (-c.score, -c.jaccard, c.kind != "reuse_mapper", c.fingerprint or c.schema_id or ""))
    records = _record_candidates(prof["tree"], schemas, min_score) if prof["kind"] == "xml" else []
    return candidates[:limit] + records


def _record_nodes(node: dict):
    kids = node.get("children", [])
    leaves = [normalize_name(c["name"]) for c in kids if c.get("leaf")]
    if kids and ("repeats" in node or len(leaves) >= 2) and leaves:
        yield node["path"], set(leaves)
    for child in kids:
        yield from _record_nodes(child)


def _record_candidates(tree: dict, schemas: list[tuple[str, list[str]]], min_score: float) -> list[Candidate]:
    out: list[Candidate] = []
    for path, observed in _record_nodes(tree["tree"]):
        best: Candidate | None = None
        for schema_id, schema_columns in schemas:
            canonical = {normalize_name(c) for c in schema_columns}
            common = observed & canonical
            score = round(len(common) / len(observed), 2)
            if score < min_score:
                continue
            c = Candidate("record", score, schema_id=schema_id, table=path, matched=sorted(common),
                          missing=sorted(canonical - observed), extra=sorted(observed - canonical),
                          jaccard=round(len(common) / len(observed | canonical), 2))
            if best is None or (c.score, c.jaccard, -len(c.schema_id)) > (best.score, best.jaccard, -len(best.schema_id)):
                best = c
        if best:
            out.append(best)
    out.sort(key=lambda c: (-c.score, -c.jaccard, c.table))
    return out[:MAX_RECORD_CANDIDATES]
