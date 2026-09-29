import copy
import csv
from dataclasses import asdict
import json

import pytest

from ace_scheduler.core.experiment import History, export_csv, save_session, statistics, summary
from ace_scheduler.core.process_metrics import Metrics, ProcessIdentity

KEY = ProcessIdentity(1, 10, "test.exe")
A = {"priority": 32, "affinity": [0, 1], "eco": {"control": 0, "state": 0}, "errors": {}}
B = {"priority": 64, "affinity": [1], "eco": {"control": 1, "state": 1}, "errors": {}}


def event(t=60, kind="apply", ok=True, before=A, after=B, changed=True):
    return {"identity": asdict(KEY), "kind": kind, "timestamp": t, "ended": t,
            "utc_offset": 1_700_000_000, "policy": {"priority": "Idle", "affinity": {"mode": "last_n"}, "eco": "on"},
            "before": before, "after": after, "result": {"ok": ok, "status": "已验证" if ok else "部分成功",
            "operations": [{"field": "priority", "ok": ok, "changed": changed, "untouched": False,
                            "requested": 64, "original": 32, "actual": 64, "message": "example",
                            "verified": ok, "recovery_pending": not ok}]},
            "context": {"app_version": "test", "topology": {"available": [0, 1], "groups": 1},
                        "sampling": {"monitor_interval": 1}, "target": {"version": None}}}


def baseline(history):
    for t in range(1, 61):
        history.add(KEY, Metrics(t, cpu_percent=4, read_mbps=2, write_mbps=1, sample_seconds=1), A)


def completed():
    history = History()
    baseline(history)
    session = history.operation(event())
    for t in range(61, 124):
        history.add(KEY, Metrics(t, cpu_percent=2, read_mbps=1, write_mbps=.5, sample_seconds=1), B)
    return history, session


def test_fixed_windows_remain_frozen_after_restore_exit_and_rolling_eviction():
    history, session = completed()
    assert session.state == "completed" and session.baseline_verified
    assert session.report()["after"]["read_mbps"]["valid_seconds"] == 60
    before = copy.deepcopy(session.observations)
    report = session.report()
    history.operation(event(124, "restore", before=B, after=A))
    for t in range(125, 501):
        history.add(KEY, Metrics(t, read_mbps=999, sample_seconds=1), A)
    history.interrupt(KEY, 501, "exited", "process_ended")
    history.retain(set())
    assert not history.samples and session.id in history.sessions
    assert session.observations == before and session.report() == report
    assert session.state == "completed"
    assert [e["kind"] for e in session.events][-2:] == ["restore", "process_ended"]


def test_multiple_apply_append_sessions_and_partial_failure_has_no_normal_after():
    history = History()
    baseline(history)
    first = history.operation(event())
    second = history.operation(event(80, ok=False, before=B))
    assert len(history.sessions) == 2 and first.id != second.id
    assert first.state == "interrupted" and first.events[-1]["kind"] == "next_apply"
    assert second.state == "partial_failure" and "after" not in second.windows()
    history.operation(event(81, "restore", before=B, after=A))
    assert len(history.sessions) == 2 and second.events[-1]["kind"] == "restore"


def test_unchanged_application_does_not_start_an_after_window():
    history = History()
    baseline(history)
    operation = event(changed=False, after=A)
    operation["result"]["operations"][0]["untouched"] = True
    session = history.operation(operation)
    assert session.state == "not_applied" and session.ended == 60
    assert "after" not in session.windows()
    assert session.events[0]["result"]["ok"]


def test_manual_baseline_and_fixed_configured_windows():
    history = History()
    session = history.begin(KEY, 0, baseline_seconds=10, transition_seconds=2, after_seconds=5, utc_offset=1_700_000_000)
    for t in range(1, 21):
        history.add(KEY, Metrics(t, read_mbps=1, sample_seconds=1), A)
    assert session.state == "awaiting_apply" and session.samples[-1].timestamp == 10
    assert history.operation(event(20)) is session
    assert session.baseline_end == 10 and session.after_start == 22 and session.after_end == 27
    history.add(KEY, Metrics(28, read_mbps=2, sample_seconds=6), B)
    assert session.state == "completed"
    assert session.report()["after"]["read_mbps"]["estimated_MB"] == 10


def test_window_intersection_totals_percentiles_and_missing_intervals():
    samples = [Metrics(2, read_mbps=10, sample_seconds=2), Metrics(6, read_mbps=2, sample_seconds=4)]
    result = statistics(samples, "read_mbps", 1, 5)
    assert result["valid_seconds"] == 4 and result["mean"] == 4
    assert result["estimated_MB"] == 16 and result["p95_time_weighted"] == 10
    assert result["coverage"] == 1 and result["gaps"] == []
    result = statistics([Metrics(2, read_mbps=0, sample_seconds=1), Metrics(4), Metrics(6, read_mbps=2, sample_seconds=1)], "read_mbps", 0, 6)
    assert result["gaps"] == [[0, 1], [2, 5]] and result["coverage"] == pytest.approx(1 / 3)
    assert statistics([Metrics(4)], "read_mbps", 0, 6)["mean"] is None
    assert "均值 4.00" in summary(samples, "read_mbps", 1, 5)
    assert "峰值 10.00" in summary(samples, "read_mbps", 1, 5)


def test_baseline_is_actual_settings_not_default_or_result_text():
    history = History()
    baseline(history)
    drift = copy.deepcopy(A)
    drift["priority"] = 64
    history.add(KEY, Metrics(60.5, read_mbps=1, sample_seconds=.5), drift)
    session = history.operation(event(61, before=drift))
    assert not session.baseline_verified and session.baseline_state == drift
    assert session.issues


def test_late_drift_does_not_change_completed_window_but_maintenance_drift_does():
    history, session = completed()
    history.operation(event(130, "maintenance", before=A, changed=True))
    assert session.state == "completed"
    history = History()
    baseline(history)
    session = history.operation(event())
    history.operation(event(65, "maintenance", before=A, changed=True))
    history.add(KEY, Metrics(123, read_mbps=1, sample_seconds=1), B)
    assert session.state == "interrupted" and "维护期间" in session.issues[0]


def test_pid_reuse_and_restore_without_application_keep_separate_records():
    history, session = completed()
    replacement = ProcessIdentity(KEY.pid, 20, KEY.name)
    history.add(replacement, Metrics(200, read_mbps=1, sample_seconds=1), A)
    new_event = event(201)
    new_event["identity"] = asdict(replacement)
    new = history.operation(new_event)
    assert new.identity != session.identity and new.id != session.id
    restored = History().operation(event(10, "restore", before=B, after=A))
    assert restored.events[0]["kind"] == "restore" and "after" not in restored.windows()


def test_json_reopen_is_readonly_and_csv_contains_all_context_and_events(tmp_path):
    history, session = completed()
    history.operation(event(124, "restore", before=B, after=A))
    path, csv_path = tmp_path / "session.json", tmp_path / "session.csv"
    save_session(session, path)
    reopened = History()
    loaded = reopened.load(path)
    assert loaded.imported and loaded.to_dict() == session.to_dict()
    reopened.add(KEY, Metrics(500, read_mbps=999, sample_seconds=1), A)
    assert loaded.to_dict() == session.to_dict()
    export_csv(loaded, csv_path)
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    context = json.loads(rows[0]["context_json"])["session"]["context"]
    assert context["requested_policy"]["eco"] == "on"
    assert context["application_result"]["operations"][0]["actual"] == 64
    assert context["topology"]["available"] == [0, 1]
    assert [json.loads(r["event_json"])["kind"] for r in rows if r["row_type"] == "event"] == ["apply", "restore"]
    assert {r["phase"] for r in rows if r["row_type"] == "sample"} == {"before", "transition", "after"}
    original = csv_path.read_bytes()
    export_csv(loaded, csv_path)
    assert csv_path.read_bytes() == original


@pytest.mark.parametrize("corrupt", [
    lambda d: d.update(schema=4),
    lambda d: d.update(session="invalid"),
    lambda d: d.update(session=[]),
    lambda d: d["session"].update(after_end=float("nan")),
    lambda d: d["session"]["observations"][0]["metrics"].update(sample_seconds=-1),
    lambda d: d["session"]["events"][0].update(result=[]),
    lambda d: d["session"]["context"].update(requested_policy={"eco": []}),
    lambda d: d["session"]["baseline_state"].update(priority={}),
])
def test_invalid_import_preserves_existing_history(tmp_path, corrupt):
    history, session = completed()
    data = session.to_dict()
    corrupt(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        history.load(path)
    assert list(history.sessions) == [session.id]


def test_atomic_export_failure_preserves_file(tmp_path, monkeypatch):
    from ace_scheduler.core import experiment
    _, session = completed()
    path = tmp_path / "keep.json"
    path.write_bytes(b"old")
    monkeypatch.setattr(experiment.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(OSError):
        save_session(session, path)
    assert path.read_bytes() == b"old" and not list(tmp_path.glob("experiment-*.tmp"))


def test_capacity_never_silently_evicts_sessions():
    history = History()
    for t in range(64):
        history.begin(ProcessIdentity(t + 1, 1, "test.exe"), t)
    ids = list(history.sessions)
    assert history.begin(KEY, 100) is None and history.warning
    assert list(history.sessions) == ids


def test_missing_samples_still_complete_fixed_window_with_zero_coverage():
    history = History()
    baseline(history)
    session = history.operation(event())
    history.consume({"kind": "tick", "timestamp": 200})
    assert session.ended == 123 and session.state == "completed"
    assert session.report()["after"]["read_mbps"]["coverage"] == 0
    assert session.report()["after"]["read_mbps"]["mean"] is None
    history.add(KEY, Metrics(201, read_mbps=9, sample_seconds=1), B)
    assert not session.report()["after"]["read_mbps"]["valid_seconds"]
