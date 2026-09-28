import json

import pytest

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, AffinitySpec, Policy, ProcessRule, preset, process_name


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
