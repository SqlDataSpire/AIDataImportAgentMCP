"""Detect & route, model-free (design §4, §8.6).

Computes the signature and looks it up. The signature is returned even on a
miss, because the pipeline profiles the file against it (§8.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fingerprint import fingerprint
from .models import Declared, Signature, SignatureHit
from .readers.base import Document

if TYPE_CHECKING:
    from .repositories import SignatureRegistry


def detect(doc: Document, declared: Declared, registry: "SignatureRegistry") -> tuple[Signature, SignatureHit | None]:
    sig = fingerprint(doc, declared)
    return sig, registry.lookup(sig.fingerprint, sig.xml_namespace)
