"""Shaper registry (design §8.5 tier 2): reviewed structural helpers.

Shapers reshape awkward layouts (anchor-cell headers, cross-tabs) into plain
tables. They never clean or coerce values — that stays downstream (§0).
"""

from __future__ import annotations

from collections.abc import Callable

from ..readers.base import TabularDocument

Shaper = Callable[..., TabularDocument]
_REGISTRY: dict[str, Shaper] = {}


def shaper(name: str) -> Callable[[Shaper], Shaper]:
    def register(fn: Shaper) -> Shaper:
        if name in _REGISTRY:
            raise ValueError(f"shaper {name!r} is already registered")
        _REGISTRY[name] = fn
        return fn
    return register


def _load_builtin() -> None:
    from . import excel  # noqa: F401  (registers on import)


def registered_shapers() -> set[str]:
    _load_builtin()
    return set(_REGISTRY)


def apply_shapers(doc: TabularDocument, shapers: list[dict]) -> TabularDocument:
    _load_builtin()
    for step in shapers:
        doc = _REGISTRY[step["name"]](doc, **step.get("options", {}))
    return doc
