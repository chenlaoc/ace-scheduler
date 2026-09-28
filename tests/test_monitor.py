from dataclasses import replace

import psutil

from ace_scheduler.config.models import AppConfig, ProcessRule, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_monitor import MonitorEngine
from ace_scheduler.core.scheduler import Scheduler
from tests.fakes import FakeApi, FakeProcess
from ace_scheduler.core.recovery import RecoveryJournal


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


def test_pending_recovery_pauses_both_timers_only_for_affected_instance(tmp_path, monkeypatch):
    engine, processes, clock = setup(keep=True)
    engine.scheduler.journal = RecoveryJournal(tmp_path / "recovery.json")
    journal = engine.scheduler.journal
    save = journal._save
    failed = False

    def fail_once(records):
        nonlocal failed
        if not failed and not next(iter(records.values()))["fields"]["priority"]["pending"]:
            failed = True
            raise OSError("confirm save failed")
        save(records)

    monkeypatch.setattr(journal, "_save", fail_once)
    engine.arm("test.exe")
    row = engine.scan()[0]
    identity = row.identity
    assert "已暂停" in row.status and "待确认" in row.status and "已验证" not in row.status
    writes = len(engine.scheduler.api.writes)
    apply = engine.scheduler.apply

    def only_new_instances(key, policy):
        assert key != identity, "pending instance must not be retried by either timer"
        return apply(key, policy)

    monkeypatch.setattr(engine.scheduler, "apply", only_new_instances)
    for _ in range(2):
        clock[0] += 100
        engine.enforce()
        assert "待确认" in engine.scan()[0].status
    assert len(engine.scheduler.api.writes) == writes
    engine.disarm("test.exe")
    assert "待确认" in engine.scan()[0].status
    engine.arm("test.exe")
    processes[2] = FakeProcess(2)
    rows = engine.scan()
    assert "已暂停" in rows[0].status
    assert "已验证" in rows[1].status
    assert journal.entry(identity, "priority")["pending"]
    assert not engine.restore()
    assert engine.restore(force=True)
    assert not engine.scheduler.pending_fields(identity)


def test_multigroup_partial_result_reaches_monitor_status():
    engine, _, _ = setup()
    engine.scheduler.topology = CpuTopology(None, 128, (), 2)
    engine.arm("test.exe")
    row = engine.scan()[0]
    assert "部分成功" in row.status and "affinity" in row.status
    assert "已验证" not in row.status


def test_field_opt_out_stops_enforcing_field_but_preserves_restore_data():
    from ace_scheduler.config.models import AffinitySpec, Policy
    from ace_scheduler.windows.eco_qos import EcoState
    engine, _, clock = setup(keep=True)
    engine.arm("test.exe")
    identity = engine.scan()[0].identity
    original = engine.scheduler.originals[identity].copy()
    policy = Policy("unchanged", AffinitySpec("unchanged"), "on")
    engine.configure(AppConfig([ProcessRule("test.exe", policy=policy, keep_enforced=True)]))
    assert not engine.armed
    engine.arm("test.exe")
    engine.scan()
    engine.scheduler.api.values[identity]["priority"] = 0x20
    engine.scheduler.api.values[identity]["affinity"] = (0, 1)
    engine.scheduler.api.values[identity]["eco"] = EcoState(1, 0)
    count = len(engine.scheduler.api.writes)
    clock[0] += 60
    engine.enforce()
    engine.scan()
    assert [field for _, field, _ in engine.scheduler.api.writes[count:]] == ["eco"]
    assert engine.scheduler.originals[identity] == original


def test_structured_recording_orders_samples_before_apply_and_records_independent_maintenance():
    from ace_scheduler.core.experiment import History
    engine, processes, clock = setup(keep=True)
    history, records = History(), []
    def record(value):
        records.append(value)
        history.consume(value)
    engine.record = record
    for t in range(1, 62):
        clock[0] = t
        engine.scan()
    engine.arm("test.exe")
    clock[0] = 62
    engine.scan()
    identity = engine.rows[0].identity
    session = history.experiments[identity]
    assert session.baseline_verified
    assert records[-3]["kind"] == "sample" and records[-2]["kind"] == "apply"
    op = records[-2]["result"]["operations"][0]
    assert op["original"] == 32 and op["requested"] == 64 and op["actual"] == 64 and op["verified"]
    assert session.context["topology"]["available"] == list(range(8))
    engine.scheduler.api.values[identity]["priority"] = 32
    clock[0] = 65
    engine.enforce()
    assert records[-1]["kind"] == "maintenance"
    assert session.events[-1]["before"]["priority"] == 32
    assert session.events[-1]["after"]["priority"] == 64
    assert identity not in engine.sampler.previous  # Do not mix across reset/write boundaries.
    clock[0] = 66
    assert engine.restore()
    assert session.events[-1]["kind"] == "restore"
    assert session.state == "interrupted" and len(history.sessions) == 1
    processes.clear()
    engine.scan()
    assert session.events[-1]["kind"] == "process_ended"
    assert len(history.sessions) == 1
