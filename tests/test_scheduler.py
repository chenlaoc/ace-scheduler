import pytest

from ace_scheduler.config.models import AffinitySpec, Policy, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.core.scheduler import Scheduler
from tests.fakes import FakeApi


@pytest.fixture
def scheduler():
    return Scheduler(FakeApi(), CpuTopology(4, 8, tuple(range(8))))


def test_apply_verify_idempotence_restore(scheduler):
    key = ProcessIdentity(5, 1, "test.exe")
    result = scheduler.apply(key, preset("Strong"))
    assert result.ok and len(scheduler.api.writes) == 3
    assert scheduler.api.values[key]["affinity"] == (7,)
    assert scheduler.apply(key, preset("Strong")).ok
    assert len(scheduler.api.writes) == 3
    assert scheduler.restore(key).ok
    assert scheduler.api.values[key]["affinity"] == tuple(range(8))
    assert scheduler.api.values[key]["eco"].label == "系统管理"
    assert not scheduler.originals
    assert scheduler.api.opened == scheduler.api.closed


def test_partial_failure_is_not_success_and_restorable(scheduler):
    key = ProcessIdentity(5, 1, "test.exe")
    scheduler.api.fail.add("set_affinity")
    result = scheduler.apply(key, preset("Strong"))
    assert not result.ok
    assert "affinity" in result.status
    assert scheduler.api.values[key]["priority"] == 0x40
    scheduler.api.fail.clear()
    assert scheduler.restore(key).ok
    assert scheduler.api.opened == scheduler.api.closed


def test_unknown_eco_not_silently_off(scheduler):
    key = ProcessIdentity(5, 1, "test.exe")
    scheduler.api.fail.add("get_eco")
    assert scheduler.inspect(key).eco is None
    assert not scheduler.apply(key, preset("Strong")).ok
    assert all(field != "eco" for _, field, _ in scheduler.api.writes)


def test_empty_affinity_blocks_every_write(scheduler):
    result = scheduler.apply(ProcessIdentity(1, 1, "test.exe"), Policy(affinity=AffinitySpec("custom")))
    assert not result.ok
    assert not scheduler.api.writes


def test_groups_skip_affinity_without_guessing():
    scheduler = Scheduler(FakeApi(), CpuTopology(None, 128, (), 2))
    result = scheduler.apply(ProcessIdentity(1, 1, "test.exe"), preset("Strong"))
    assert result.ok
    assert all(field != "affinity" for _, field, _ in scheduler.api.writes)
    assert any("跳过" in op.message for op in result.operations)
