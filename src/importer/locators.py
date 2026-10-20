"""Read a file from where it lives (design §8.21).

Production files are always on a shared location known to the catalog: CIFS/SMB
(\\\\server\\share\\...), an Azure Files share (\\\\<acct>.file.core.windows.net\\...)
or Azure Blob (https://<acct>.blob.core.windows.net/<container>/...). The importer
copies nothing; the ledger keeps the locator verbatim so any machine can re-read it.
Blob access uses the caller's Entra identity, so a Blob locator never carries a SAS
token or other query string (secrets must not reach the ledger, logs or the model).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

log = logging.getLogger("importer")
_BLOB_SUFFIX = ".blob.core.windows.net"


class LocatorError(ValueError):
    """The file cannot be read from this machine, or it changed since discovery."""


def is_blob(loc: str) -> bool:
    """https://<account>.blob.core.windows.net/<container>/<name>, checked on the parsed host."""
    try:
        parts = urlsplit(loc)
    except ValueError:
        return False
    host = parts.hostname or ""
    segments = [s for s in parts.path.split("/") if s]
    return (parts.scheme == "https" and host.endswith(_BLOB_SUFFIX) and len(host) > len(_BLOB_SUFFIX)
            and "@" not in parts.netloc and len(segments) >= 2)


def is_unc(loc: str) -> bool:
    return loc.startswith("\\\\") or loc.startswith("//")


def normalize(path) -> str:
    text = str(path)
    if is_blob(text):
        parts = urlsplit(text)
        if parts.query or parts.fragment:
            raise LocatorError(f"https://{parts.hostname}{parts.path}: Blob locators must not carry a SAS token or "
                               "query string; access uses your Entra identity")
        return text
    if is_unc(text):
        return text
    if "://" in text:
        raise LocatorError(r"unsupported locator: use a \\server\share path, an Azure Files share, "
                           "or https://<account>.blob.core.windows.net/<container>/<name>")
    resolved = str(Path(text).resolve())
    log.warning("local path %s: other machines cannot read it; use a share or Blob URL in production", resolved)
    return resolved


def name_of(loc: str) -> str:
    if is_blob(loc):
        return urlsplit(loc).path.rstrip("/").rsplit("/", 1)[-1]
    return PureWindowsPath(loc).name if "\\" in loc else Path(loc).name


def read_bytes(loc: str) -> bytes:
    if is_blob(loc):
        return _read_blob(loc)
    try:
        return Path(loc).read_bytes()
    except OSError as exc:
        raise LocatorError(f"file not reachable from this machine: {loc} ({exc})") from exc


def read_verified(loc: str, sha256: str) -> bytes:
    data = read_bytes(loc)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise LocatorError(f"{loc} changed since it was discovered; deliver it as a new file")
    return data


def _read_blob(url: str) -> bytes:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient
    except ImportError as exc:
        raise LocatorError("Blob locators need azure-storage-blob and azure-identity installed") from exc
    try:
        return BlobClient.from_blob_url(url, credential=DefaultAzureCredential()).download_blob().readall()
    except Exception as exc:
        raise LocatorError(f"file not reachable from this machine: {url} ({type(exc).__name__}: {exc})") from exc
