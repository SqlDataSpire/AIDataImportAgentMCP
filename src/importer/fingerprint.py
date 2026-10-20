"""Deterministic file signature (design §8.6).

signature = PROJECT|CLASS|FILE_TYPE|<physical part>, matched exactly.
XML: the root element's local name (namespace checked separately). Its nesting tree rides along in observed (§8.24).
Tabular: h:<16 hex> over normalized structure (header / sheets+headers / keys).
"""

from __future__ import annotations

import hashlib

from .models import Declared, Signature
from .readers.base import Document, XmlDocument, normalize_name
from .trees import build_tree


def _h(parts: list[str]) -> str:
    return "h:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def physical_part(doc: Document) -> tuple[str, dict]:
    if isinstance(doc, XmlDocument):
        return doc.local_name, {"root": doc.local_name, "namespace": doc.namespace, "tree": build_tree(doc)}
    if doc.format == "EXCEL":
        sheets = sorted((normalize_name(t.name), t.columns) for t in doc.tables)
        part = _h([f"{name}:{','.join(cols)}" for name, cols in sheets])
        return part, {"sheets": {t.name: t.columns for t in doc.tables}}
    table = doc.tables[0]
    if doc.format in ("JSON", "NDJSON"):
        keys = sorted(set(table.columns))
        return _h(keys), {"keys": keys}
    return _h(table.columns), {"columns": table.columns}


def fingerprint(doc: Document, declared: Declared) -> Signature:
    part, observed = physical_part(doc)
    namespace = doc.namespace if isinstance(doc, XmlDocument) else None
    return Signature(
        fingerprint=f"{declared.prefix}|{part}",
        physical_part=part,
        xml_namespace=namespace,
        observed=observed,
    )
