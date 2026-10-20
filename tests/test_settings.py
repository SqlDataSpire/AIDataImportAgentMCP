from __future__ import annotations

import pytest

from importer import settings
from importer.memory_store import MemoryStore
from importer.models import ArtifactType


def test_defaults_without_a_config_version():
    store = MemoryStore()
    with store.transaction() as tx:
        s = settings.load(tx.artifacts)
    assert s.destinations == {} and (s.max_values, s.max_chars) == (5, 200)
    assert s.destination("APP_DB") is None


def test_set_and_unset_destination_versions_and_logs():
    store = MemoryStore()
    with store.transaction() as tx:
        settings.set_destination(tx, "app_db", server="test_server", database="APP_DB", by="test_user")
        settings.set_destination(tx, "EVENT_DB", server="test_server", database="EVENT_DB", by="test_user")
        s = settings.load(tx.artifacts)
        assert s.destination("App_Db").database == "APP_DB"
        assert sorted(s.destinations) == ["APP_DB", "EVENT_DB"]
        settings.unset_destination(tx, "event_db", by="test_user")
        assert settings.load(tx.artifacts).destination("EVENT_DB") is None
    configs = [a for a in store.state.artifacts.values() if a.artifact_type is ArtifactType.CONFIG]
    assert [a.version_no for a in configs] == [1, 2, 3]
    assert [a.is_active for a in configs] == [False, False, True]
    assert [d["action_type"] for d in store.state.decisions] == ["config_change"] * 3


def test_set_value_and_validation():
    store = MemoryStore()
    with store.transaction() as tx:
        settings.set_value(tx, "samples.max_values", 3, by="t")
        assert settings.load(tx.artifacts).max_values == 3
        with pytest.raises(settings.SettingsError):
            settings.set_value(tx, "samples.nope", 3, by="t")
        with pytest.raises(settings.SettingsError):
            settings.set_value(tx, "samples.max_chars", 0, by="t")
        with pytest.raises(settings.SettingsError):
            settings.set_destination(tx, "bad name", server="s", database="d", by="t")
        with pytest.raises(settings.SettingsError):
            settings.unset_destination(tx, "MISSING", by="t")


def test_artifacts_active_and_activate():
    store = MemoryStore()
    with store.transaction() as tx:
        v1 = tx.artifacts.put_version(ArtifactType.MAPPER, "sig.1", {"kind": "tabular"}, created_by="t", activate=False)
        assert tx.artifacts.active(ArtifactType.MAPPER, "sig.1") is None
        row = tx.artifacts.activate(v1.version_id, approved_by="tony")
        assert row.is_active and row.approved_by == "tony"
        assert tx.artifacts.active(ArtifactType.MAPPER, "sig.1").version_id == v1.version_id
