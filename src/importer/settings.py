"""Shared settings in the control database (design §8.17).

Every client and user must behave identically, so settings are a versioned
`config` artifact, never a local file. Only the pointer to the control database
(the DataEngine `importer` entry in database.env) is local.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from .models import ArtifactType
from .repositories import ArtifactRow

CONFIG_KEY = "importer"
DEFAULT_SAMPLES = {"max_values": 5, "max_chars": 200}
NUMERIC_KEYS = frozenset({"samples.max_values", "samples.max_chars"})
_NAME = re.compile(r"^[A-Za-z0-9_]+$")


class SettingsError(ValueError):
    """An invalid settings change."""


@dataclass(frozen=True)
class Destination:
    name: str
    server: str
    database: str


@dataclass(frozen=True)
class Settings:
    destinations: dict[str, Destination]
    max_values: int
    max_chars: int

    def destination(self, name: str) -> Destination | None:
        dest = self.destinations.get(name.upper())
        if dest is not None:
            return dest
        try:
            from . import db
            doc = db.load_connection_document()
            curr_db = doc.get("database", "")
            if curr_db and name.upper() == curr_db.upper():
                return Destination(name=curr_db.upper(), server=doc.get("server", "localhost"), database=curr_db)
        except Exception:
            pass
        return None


def document(artifacts) -> dict:
    row = artifacts.active(ArtifactType.CONFIG, CONFIG_KEY)
    doc = copy.deepcopy(row.definition) if row else {}
    doc["destinations"] = {k.upper(): v for k, v in doc.get("destinations", {}).items()}
    doc["samples"] = {**DEFAULT_SAMPLES, **doc.get("samples", {})}
    return doc


def load(artifacts) -> Settings:
    doc = document(artifacts)
    destinations = {
        name: Destination(name, entry["server"], entry["database"]) for name, entry in doc["destinations"].items()
    }
    return Settings(destinations, int(doc["samples"]["max_values"]), int(doc["samples"]["max_chars"]))


def _save(tx, doc: dict, *, by: str, change: dict) -> ArtifactRow:
    row = tx.artifacts.put_version(ArtifactType.CONFIG, CONFIG_KEY, doc, created_by=by)
    tx.audit.decision(None, "config_change", observed=None, proposed=change, applied=True, approval=by)
    return row


def set_destination(tx, name: str, *, server: str, database: str, by: str) -> ArtifactRow:
    if not _NAME.match(name or ""):
        raise SettingsError(f"destination name {name!r} must match [A-Za-z0-9_]+")
    if not server or not database:
        raise SettingsError("a destination needs --server and --database")
    doc = document(tx.artifacts)
    doc["destinations"][name.upper()] = {"server": server, "database": database}
    return _save(tx, doc, by=by, change={"set_destination": name.upper(), "server": server, "database": database})


def unset_destination(tx, name: str, *, by: str) -> ArtifactRow:
    doc = document(tx.artifacts)
    if doc["destinations"].pop(name.upper(), None) is None:
        raise SettingsError(f"no destination named {name!r}")
    return _save(tx, doc, by=by, change={"unset_destination": name.upper()})


def set_value(tx, key: str, value: int, *, by: str) -> ArtifactRow:
    if key not in NUMERIC_KEYS:
        raise SettingsError(f"unknown setting {key!r}; settable: {sorted(NUMERIC_KEYS)}")
    if not isinstance(value, int) or value < 1:
        raise SettingsError(f"{key} must be a positive integer")
    doc = document(tx.artifacts)
    doc["samples"][key.split(".", 1)[1]] = value
    return _save(tx, doc, by=by, change={key: value})
