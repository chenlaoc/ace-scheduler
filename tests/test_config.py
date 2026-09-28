import json

import pytest

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, AffinitySpec, Policy, ProcessRule, DEFAULT_NAMES, preset, process_name


def test_atomic_roundtrip_and_semantic_affinity(tmp_path):
    config = AppConfig(rules=[ProcessRule("SGuard64.exe", policy=preset("Strong"))])
    manager = ConfigManager(tmp_path / "nested" / "config.json")
    manager.save(config)
    loaded, warning = manager.load()
    assert not warning
    assert loaded.rules[0].policy.affinity.mode == "last_n"
    assert loaded.rules[0].policy.affinity.cpus == ()
    assert "armed" not in manager.path.read_text()
    assert not list(manager.path.parent.glob("*.tmp"))


def test_corrupt_config_preserved(tmp_path):
    manager = ConfigManager(tmp_path / "config.json")
    manager.path.write_text('{"bad":', encoding="utf-8")
    config, warning = manager.load()
    assert warning and len(config.rules) == 5
    assert len(list(tmp_path.glob("config.invalid-*.json"))) == 1


@pytest.mark.parametrize("value", ["driver.sys", "C:\\thing.exe", "foo/bar.exe", "*.exe", "", None])
def test_only_executable_basenames(value):
    with pytest.raises(ValueError):
        process_name(value)


@pytest.mark.parametrize("value", [{"priority": "Realtime"}, {"eco": "false"}, {"affinity": {"percentage": 0}},
                                   {"affinity": {"count": -1}}, {"affinity": {"mode": "invalid"}}])
def test_invalid_policy_rejected(value):
    with pytest.raises(ValueError):
        Policy.parse(value)


def test_defaults_and_duplicates():
    assert len(AppConfig.parse({}).rules) == 5
    with pytest.raises(ValueError, match="重复"):
        AppConfig.parse({"rules": [{"name": "a.exe"}, {"name": "A.exe"}]})
    assert AffinitySpec.parse({"mode": "custom", "cpus": [-1, 2, True, 2, "3"]}).cpus == (2,)


@pytest.mark.parametrize("builtin_count", range(len(DEFAULT_NAMES) + 1))
@pytest.mark.parametrize("custom_count", [0, 250, 251])
def test_capacity_roundtrip_includes_missing_builtins(builtin_count, custom_count, tmp_path):
    raw = [{"name": n, "enabled": False} for n in DEFAULT_NAMES[:builtin_count]]
    raw += [{"name": f"custom-{i}.exe"} for i in range(custom_count)]
    config = AppConfig.parse({"rules": raw})
    assert len(config.rules) == custom_count + len(DEFAULT_NAMES)
    assert AppConfig.parse(config.to_dict()).to_dict() == config.to_dict()
    manager = ConfigManager(tmp_path / "config.json")
    manager.save(config)
    loaded, warning = manager.load()
    assert not warning and loaded.to_dict() == config.to_dict()


@pytest.mark.parametrize("count", [252, 256, 257])
def test_capacity_overflow_rejected_before_returning_config(count):
    with pytest.raises(ValueError, match="256"):
        AppConfig.parse({"rules": [{"name": f"custom-{i}.exe"} for i in range(count)]})


@pytest.mark.parametrize("legacy,mode", [(True, "on"), (False, "off")])
def test_v1_migration_preserves_policy_and_backs_up_before_save(tmp_path, legacy, mode):
    raw = {"version": 1, "theme": "dark", "monitor_interval": 3,
           "rules": [{"name": "old.exe", "keep_enforced": True,
                      "policy": {"priority": "Below Normal", "affinity": {"mode": "last_n", "count": 2}, "eco": legacy}}]}
    manager = ConfigManager(tmp_path / "config.json")
    original = json.dumps(raw).encode("utf-8")
    manager.path.write_bytes(original)
    loaded, warning = manager.load()
    assert not warning and loaded.rules[0].policy.eco == mode
    assert loaded.rules[0].keep_enforced and loaded.rules[0].policy.priority == "Below Normal"
    assert loaded.rules[0].policy.affinity.count == 2 and loaded.monitor_interval == 3 and loaded.theme == "dark"
    assert manager.path.read_bytes() == original
    manager.save(loaded)
    assert json.loads(manager.path.read_text())["version"] == 2
    assert next(tmp_path.glob("config.v1-*.json")).read_bytes() == original
    assert manager.load() == (loaded, "")
    manager.save(loaded)
    assert len(list(tmp_path.glob("config.v1-*.json"))) == 1


def test_migration_backup_failure_keeps_legacy_file(tmp_path, monkeypatch):
    from ace_scheduler.config import config_manager
    manager = ConfigManager(tmp_path / "config.json")
    manager.path.write_text('{"version":1}', encoding="utf-8")
    loaded, _ = manager.load()
    monkeypatch.setattr(config_manager.shutil, "copy2", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        manager.save(loaded)
    assert manager.path.read_text() == '{"version":1}'


@pytest.mark.parametrize("eco", ["unchanged", "system", "on", "off"])
def test_v2_field_modes_roundtrip(eco):
    raw = {"version": 2, "rules": [{"name": "one.exe", "policy": {
        "priority": "unchanged", "affinity": {"mode": "unchanged"}, "eco": eco}}]}
    config = AppConfig.parse(raw)
    assert AppConfig.parse(config.to_dict()) == config
    assert config.rules[0].policy.priority == "unchanged"
    assert config.rules[0].policy.affinity.mode == "unchanged"
    assert config.rules[0].policy.eco == eco


@pytest.mark.parametrize("version,eco", [(1, "off"), (2, False), (2, True), (2, None), (3, "on")])
def test_versioned_policy_rejects_ambiguous_or_future_values(version, eco):
    with pytest.raises(ValueError):
        AppConfig.parse({"version": version, "rules": [{"name": "one.exe", "policy": {"eco": eco}}]})
