from dataclasses import replace

import pytest

from ace_scheduler.config.models import AffinitySpec, Policy, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.policy import preview_policy
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.core.recovery import RecoveryJournal
from ace_scheduler.core.scheduler import Scheduler
from ace_scheduler.windows.eco_qos import EcoState
from tests.fakes import FakeApi


UNCHANGED = Policy("unchanged", AffinitySpec("unchanged"), "unchanged")
KEY = ProcessIdentity(10, 100, "helper.exe")
TOPOLOGY = CpuTopology(4, 8, tuple(range(8)))


@pytest.mark.parametrize("field,policy", [
    ("priority", replace(UNCHANGED, priority="Idle")),
    ("affinity", replace(UNCHANGED, affinity=AffinitySpec("last_n", count=2))),
    ("eco", replace(UNCHANGED, eco="on")),
    ("eco", replace(UNCHANGED, eco="off")),
    ("eco", replace(UNCHANGED, eco="system")),
])
def test_single_field_only_reads_writes_and_journals_that_field(tmp_path, monkeypatch, field, policy):
    api = FakeApi()
    scheduler = Scheduler(api, TOPOLOGY, RecoveryJournal(tmp_path / "recovery.json"))
    original = {"priority": 0x20, "affinity": tuple(range(8)),
                "eco": EcoState(1, 1) if policy.eco == "system" else EcoState(0, 0)}
    api.values[KEY] = original.copy()
    for untouched in {"priority", "affinity", "eco"} - {field}:
        for action in ("get", "set"):
            monkeypatch.setattr(api, f"{action}_{untouched}", lambda *args: pytest.fail("untouched field accessed"))
    result = scheduler.apply(KEY, policy)
    assert result.ok, result.status
    assert [name for _, name, _ in api.writes] == [field]
    assert set(scheduler.originals[KEY]) == {field}
    assert set(scheduler.journal.records[KEY]["fields"]) == {field}
    assert len([op for op in result.operations if op.untouched]) == 2
    assert all(api.values[KEY][name] == value for name, value in original.items() if name != field)
    if field == "eco":
        assert api.values[KEY]["eco"].label == {"on": "ON", "off": "OFF", "system": "系统管理"}[policy.eco]
    assert scheduler.apply(KEY, policy).ok
    assert len(api.writes) == 1
    assert scheduler.restore(KEY).ok
    assert api.values[KEY] == original
    assert not scheduler.originals and not scheduler.journal.records


def test_all_unchanged_does_not_open_handle_or_create_journal(tmp_path):
    api = FakeApi()
    api.fail.add("open")
    path = tmp_path / "recovery.json"
    scheduler = Scheduler(api, CpuTopology(None, 128, (), 2), RecoveryJournal(path))
    result = scheduler.apply(KEY, UNCHANGED)
    assert result.ok and "全部字段均不接管" in result.status
    assert "已验证" not in result.status
    assert not api.opened and not api.writes and not path.exists()


def test_eco_only_on_multigroup_machine_is_full_success():
    scheduler = Scheduler(FakeApi(), CpuTopology(None, 128, (), 2))
    result = scheduler.apply(KEY, replace(UNCHANGED, eco="on"))
    assert result.ok and not any(op.skipped for op in result.operations)
    assert [name for _, name, _ in scheduler.api.writes] == ["eco"]
    result = scheduler.apply(KEY, replace(UNCHANGED, affinity=AffinitySpec()))
    assert not result.ok and "部分成功" not in result.status
    assert next(op for op in result.operations if op.field == "affinity").skipped


def test_unchanged_keeps_originals_and_does_not_restore_previous_modifications(tmp_path):
    scheduler = Scheduler(FakeApi(), TOPOLOGY, RecoveryJournal(tmp_path / "recovery.json"))
    assert scheduler.apply(KEY, preset("Strong")).ok
    originals = scheduler.originals[KEY].copy()
    scheduler.api.values[KEY]["priority"] = 0x4000
    count = len(scheduler.api.writes)
    assert scheduler.apply(KEY, replace(UNCHANGED, eco="system")).ok
    assert scheduler.api.values[KEY]["priority"] == 0x4000
    assert scheduler.originals[KEY] == originals
    assert [name for _, name, _ in scheduler.api.writes[count:]] == ["eco"]
    assert not scheduler.restore(KEY).ok  # External priority is still a restore conflict.
    assert scheduler.restore(KEY, force=True).ok
    assert scheduler.api.values[KEY]["priority"] == 0x20


@pytest.mark.parametrize("policy", [replace(UNCHANGED, eco=True), replace(UNCHANGED, eco="invalid"),
                                  replace(UNCHANGED, priority="Realtime")])
def test_invalid_policy_blocks_every_write(policy):
    scheduler = Scheduler(FakeApi(), TOPOLOGY)
    assert not scheduler.apply(KEY, policy).ok
    assert not scheduler.api.opened and not scheduler.api.writes


def test_preview_uses_same_cpu_resolution_and_explicit_eco_semantics():
    text = preview_policy(replace(UNCHANGED, eco="system"), TOPOLOGY)
    assert "Priority：不修改" in text and "Affinity：不修改" in text and "EcoQoS：系统管理" in text
    assert "CPU 6, 7" in preview_policy(replace(UNCHANGED, affinity=AffinitySpec("last_n", count=2)), TOPOLOGY)
    assert "显式关闭" in preview_policy(preset("Default"), TOPOLOGY)
    with pytest.raises(ValueError, match="全部无效"):
        preview_policy(replace(UNCHANGED, affinity=AffinitySpec("custom", cpus=(99,))), TOPOLOGY)
