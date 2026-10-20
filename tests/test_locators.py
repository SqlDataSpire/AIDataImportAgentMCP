from __future__ import annotations

import hashlib

import pytest

from importer import locators
from importer.memory_store import MemoryStore
from importer.pipeline import process, retry
from tests.support import fixture

BLOB = "https://acct.blob.core.windows.net/incoming/dc/formulary.xml"


def test_kinds_and_names():
    assert locators.is_blob(BLOB) and not locators.is_unc(BLOB)
    assert locators.is_unc(r"\\fileserver\drop\vt\urls.csv") and locators.is_unc("//fileserver/drop/urls.csv")
    assert locators.name_of(BLOB) == "formulary.xml"
    assert locators.name_of(r"\\fileserver\drop\vt\urls.csv") == "urls.csv"


def test_shared_locators_are_kept_verbatim_and_local_paths_resolved(tmp_path):
    assert locators.normalize(BLOB) == BLOB
    assert locators.normalize(r"\\fileserver\drop\a.csv") == r"\\fileserver\drop\a.csv"
    local = tmp_path / "a.csv"
    assert locators.normalize(local) == str(local.resolve())


def test_blob_reads_go_through_the_blob_reader(monkeypatch):
    monkeypatch.setattr(locators, "_read_blob", lambda url: b"data:" + url.encode())
    assert locators.read_bytes(BLOB) == b"data:" + BLOB.encode()


def test_unreachable_and_changed_files_are_refused(tmp_path):
    with pytest.raises(locators.LocatorError, match="not reachable"):
        locators.read_bytes(str(tmp_path / "missing.csv"))
    path = tmp_path / "f.csv"
    path.write_bytes(b"a\n1\n")
    good = hashlib.sha256(b"a\n1\n").hexdigest()
    assert locators.read_verified(str(path), good) == b"a\n1\n"
    with pytest.raises(locators.LocatorError, match="changed since"):
        locators.read_verified(str(path), "0" * 64)


def test_process_records_a_blob_locator_and_retry_reads_it(monkeypatch):
    data = fixture("url_template_a.csv").read_bytes()
    monkeypatch.setattr(locators, "_read_blob", lambda url: data)
    store = MemoryStore()
    result = process(BLOB.replace("formulary.xml", "urls.csv"), project="VT", cls="PC", file_type="CSV",
                     source="test", store=store)
    row = store.state.ledger[result.file_id]
    assert row.source_path.endswith("/incoming/dc/urls.csv") and row.original_filename == "urls.csv"
    again = retry(result.file_id, store=store)
    assert again.file_id == result.file_id


def test_blob_locators_with_a_sas_token_are_refused_without_echoing_it():
    with pytest.raises(locators.LocatorError, match="SAS") as err:
        locators.normalize(BLOB + "?sv=2024-01-01&sig=SECRET123")
    assert "SECRET123" not in str(err.value)


def test_only_real_blob_hosts_count_as_blob():
    for fake in ("https://evil.com#.blob.core.windows.net/c/p.csv",
                 "https://acct.blob.core.windows.net.evil.com/c/p.csv",
                 "https://user@acct.blob.core.windows.net/c/p.csv",
                 "http://acct.blob.core.windows.net/c/p.csv"):
        assert not locators.is_blob(fake), fake
        with pytest.raises(locators.LocatorError, match="unsupported locator"):
            locators.normalize(fake)
    assert locators.is_blob(BLOB)
