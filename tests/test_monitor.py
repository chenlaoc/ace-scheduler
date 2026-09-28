from dataclasses import replace

import psutil

from ace_scheduler.config.models import AppConfig, ProcessRule, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_monitor import MonitorEngine
from ace_scheduler.core.scheduler import Scheduler
from tests.fakes import FakeApi, FakeProcess


def setup(keep=False):
    processes = {1: FakeProcess(1)}
    clock = [10.0]
    api = FakeApi()
    config = AppConfig([ProcessRule("test.exe", policy=preset("Strong"), keep_enforced=keep)])
    engine = MonitorEngine(Scheduler(api, CpuTopology(4, 8, tuple(range(8)))), config,
                           iterator=lambda _: list(processes.values()), process_factory=lambda pid: processes[pid],
                           clock=lambda: clock[0])
    return engine, processes, clock


def test_monitor_only_then_apply_once_restart_and_pid_reuse():
    engine, processes, clock = setup()
    engine.scan()
    assert not engine.scheduler.api.writes
    engine.arm("test.exe")
    engine.scan()
    assert len(engine.scheduler.api.writes) == 3
    identity = engine.rows[0].identity
    engine.scheduler.api.values[identity]["priority"] = 0x20
    clock[0] += 10
    engine.scan()
    assert len(engine.scheduler.api.writes) == 3  # Apply Once does not fight resets.
    processes[1] = FakeProcess(1, created=2)
    engine.scan()
    assert len(engine.scheduler.api.writes) == 6
    assert identity not in engine.attempted
    assert identity not in engine.scheduler.originals
    processes.clear()
    engine.scan()
    assert not engine.known and not engine.scheduler.originals


def test_keep_enforced_independent_timer_and_stop():
    engine, processes, clock = setup(keep=True)
    engine.arm("test.exe")
    engine.scan()
    identity = engine.rows[0].identity
    engine.scheduler.api.values[identity]["priority"] = 0x20
    clock[0] += 2
    engine.enforce()
    assert len(engine.scheduler.api.writes) == 3
    clock[0] += 1
    engine.enforce()
    assert len(engine.scheduler.api.writes) == 4
    engine.disarm("test.exe")
    engine.scheduler.api.values[identity]["priority"] = 0x20
    clock[0] += 10
    engine.enforce()
    assert len(engine.scheduler.api.writes) == 4


def test_multimatch_rule_change_and_failure_backoff():
    engine, processes, clock = setup(keep=True)
    processes[2] = FakeProcess(2)
    engine.arm("test.exe")
    engine.scheduler.api.fail.add("set_affinity")
    engine.scan()
    assert len(engine.attempted) == 2
    assert all(value == 40 for value in engine.next_enforce.values())
    engine.configure(AppConfig([ProcessRule("test.exe", policy=preset("Mild"))]))
    assert not engine.armed
    assert engine.scheduler.originals  # Saving/disabling never discards restore data.


def test_access_denied_one_process_does_not_block_others():
    engine, processes, clock = setup()
    processes[2] = FakeProcess(2)
    def denied(): raise psutil.AccessDenied(1)
    processes[1].create_time = denied
    engine.arm("test.exe")
    rows = engine.scan()
    assert len(rows) == 2
    assert "AccessDenied" in rows[0].status
    assert len(engine.scheduler.api.writes) == 3


def test_process_exit_during_metrics_is_ignored():
    engine, processes, clock = setup()
    def exited(): raise psutil.NoSuchProcess(1)
    processes[1].io_counters = exited
    assert engine.scan() == []
    assert not engine.scheduler.api.writes
