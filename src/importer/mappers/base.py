"""Mapper contract — one interface for all three tiers (design §8.5, §8.9).

A mapper turns a whole Document into LandRow / RejectRow items, possibly for
several target schemas. Declarative specs, spec + shapers, and plugin classes
all implement this interface; the pipeline cannot tell them apart.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import LandRow, RejectRow

if TYPE_CHECKING:
    from ..readers.base import Document


@dataclass(frozen=True)
class RunContext:
    file_id: int
    signature_id: int
    artifact_version_id: int
    request_context: dict = field(default_factory=dict)
    schemas: dict[str, list[str]] = field(default_factory=dict)  # target -> canonical columns


class Mapper(ABC):
    version: int = 1

    @property
    @abstractmethod
    def targets(self) -> tuple[str, ...]:
        """Target schema ids this mapper writes to."""

    @abstractmethod
    def extract(self, doc: "Document", ctx: RunContext) -> Iterator[LandRow | RejectRow]:
        """Yield one item per extracted row; never raise for a single bad row."""
