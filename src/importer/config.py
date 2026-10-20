"""Importer settings: where database.env lives."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    """Directory holding database.env (the pointer to the control database).

    IMPORTER_HOME wins; otherwise the repo root (editable install); otherwise the
    current directory (the importer used as a library from another project).
    """
    override = os.environ.get("IMPORTER_HOME")
    if override:
        return Path(override)
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo
    return Path.cwd()

