import copy
import csv
import json
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ace_scheduler.core.clock import ClockTracker, discontinuities
from ace_scheduler.core.disk_recording import DiskStore, FIELDS, disk_statistics, validate_rows
from ace_scheduler.core.experiment import History, save_session, export_csv
from ace_scheduler.core.process_metrics import Metrics
from tests.test_experiment import KEY, A, event
from tests.test_ui import app


DEVICE = {"id": "disk-a", "name": "Test device", "number": 0, "instance": "0 C:",
          "volumes": ["C:"], "fingerprint": "test", "uncertainty": []}


def row(t=1, read_iops=10, latency=.002, **extra):
    return {"timestamp": t, "seconds": 1, "device_id": "disk-a", "values": {
        "read_B_s": 1000, "write_B_s": 0, "read_iops": read_iops, "write_iops": 0,
        "read_latency_s": latency, "write_latency_s": None, "queue_mean": .1, "queue_current": 2},
        "errors": {}, "gap": None, **extra}


def anchor(t, utc=None, awake=None):
    return {"monotonic": t, "utc_unix": 1700000000 + t if utc is None else utc,
            "qpc": int(t * 10000000), "qpc_frequency": 10000000, "awake_seconds": t if awake is None else awake,
            "pairing_error_seconds": .001, "source": "fixture", "utc_accuracy": "not measured"}


def selected_history():
    history = History()
    history.disk_packet({"devices": [DEVICE], "timestamp": 0, "rows": []})
    history.selected_disk = DEVICE["id"]
    history.clock_update({"anchor": anchor(0), "breaks": []})
    session = history.begin(KEY, 0, baseline_seconds=2, transition_seconds=0, after_seconds=2, utc_offset=1700000000)
    return history, session


def test_disk_statistics_request_weighting_gaps_and_instantaneous_semantics():
    rows = [row(1, 1, .1), row(2, 99, .001), row(3, 0, None), row(5, 10, 0)]
    result = disk_statistics(rows, "read_latency_s", 0, 5)
    assert result["mean"] == pytest.approx((.1 + .099) / 110)
    assert result["valid_seconds"] == 3
    assert result["estimated_requests"] == 110
    assert result["gaps"] == [[2, 4]]
    assert "p95" not in result
    zero = disk_statistics([row(1, 10, 0)], "read_latency_s", 0, 1)
    assert zero["mean"] == 0
    idle = disk_statistics([row(1, 0, None)], "read_latency_s", 0, 1)
    assert idle["mean"] is None
    current = disk_statistics(rows, "queue_current", 0, 5)
    assert current["mean"] is None and current["coverage"] is None
    assert current["instantaneous_max"] == 2 and current["instantaneous_points"] == 4


def test_disk_clipped_windows_do_not_double_count_overlapping_intervals():
    rows = [row(1), row(1.5)]
    report = disk_statistics(rows, "read_iops", .75, 1.25)
    assert report["valid_seconds"] == .5 and report["mean"] == 10


def test_disk_store_is_shared_bounded_and_never_evicts(monkeypatch):
    store = DiskStore()
    try:
        store.append("disk-a", [row(1), row(2)])
        store.append("disk-a", [row(2)])
        assert store.count == 2
        monkeypatch.setattr(store, "MAX_ROWS", 2)
        with pytest.raises(ValueError, match="上限"):
            store.append("disk-a", [row(3)])
        assert len(store.rows("disk-a", [(0, 2)])) == 2
        monkeypatch.setattr(store, "MAX_ROWS", 100)
        with pytest.raises(ValueError, match="冲突"):
            store.append("disk-a", [row(2, latency=.5)])
    finally:
        store.close()
    assert not store.path.exists()


def test_scene_marker_never_changes_scheduling_or_windows():
    history, session = selected_history()
    windows, context = copy.deepcopy(session.windows()), copy.deepcopy(session.context)
    session.set_scene("训练场", "固定画质")
    session.scene_marker(1, "进入游戏")
    assert session.marker is None and session.state == "baseline"
    assert session.windows() == windows and session.context == context
    assert session.events[-1]["kind"] == "scene_marker"
    assert session.scene_name == "训练场"


def test_schema_two_round_trip_csv_disk_and_schema_one_compatibility(tmp_path):
    history, session = selected_history()
    session.set_scene("训练场", "相同路线")
    session.scene_marker(.5, "加载开始")
    history.disk_packet({"devices": None, "timestamp": 1, "rows": [row(1)]})
    history.add(KEY, Metrics(1, read_mbps=2, sample_seconds=1), A)
    history.clock_update({"anchor": anchor(1), "breaks": ["utc_jump"]})
    path = tmp_path / "session.json"
    save_session(session, path)
    loaded = History().load(path)
    assert loaded.imported and loaded.to_dict() == session.to_dict()
    assert loaded.samples[0].read_mbps is None
    with pytest.raises(ValueError, match="只读"):
        loaded.set_scene("other", "")
    csv_path = tmp_path / "session.csv"
    export_csv(loaded, csv_path)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert any(r["row_type"] == "disk_sample" for r in rows)
    assert any("scene_marker" in r["event_json"] for r in rows)
    old = session.to_dict()
    old["schema"] = 1
    old.pop("disk_recording")
    for key in ("scene_name", "scene_notes", "clock", "disk_selection"):
        old["session"].pop(key)
    path.write_text(json.dumps(old), encoding="utf-8")
    legacy = History().load(path)
    assert legacy.clock == {"source": "legacy_fixed_utc_offset", "anchors": [], "breaks": []}
    assert legacy.disk_selection is None


def test_device_disappearance_invalidates_selection_without_joining_replacement():
    history, session = selected_history()
    history.disk_packet({"devices": None, "timestamp": 1, "rows": [row(1)]})
    replacement = {**DEVICE, "id": "disk-b", "fingerprint": "other"}
    history.disk_packet({"devices": [replacement], "timestamp": 2, "rows": []})
    assert history.selected_disk is None
    assert session.disk_selection["cutoff"] == 2
    assert session.disk_selection["device"]["id"] == "disk-a"
    assert len(session.disk_rows()) == 1


def test_stopping_capture_keeps_shared_data_for_multiple_sessions():
    history, session = selected_history()
    other = history.begin(KEY, 0, baseline_seconds=2, supersede=False)
    history.disk_packet({"devices": None, "timestamp": 1, "rows": [row(1)]})
    assert session._disk_store is other._disk_store
    assert history.disk_store.count == 1
    history.disk_stop("stopped", 1.5)
    history.disk_packet({"devices": None, "timestamp": 2, "rows": [row(2)]})
    assert len(session.disk_rows()) == len(other.disk_rows()) == 1


def test_clock_discontinuities_and_real_anchor():
    assert discontinuities(anchor(1), anchor(2)) == []
    assert "utc_jump" in discontinuities(anchor(1), anchor(2, utc=1700000100))
    reasons = discontinuities(anchor(1), anchor(20, awake=2))
    assert reasons == ["sampling_pause", "sleep_resume"]
    live = ClockTracker().sample()["anchor"]
    assert live["qpc_frequency"] > 0 and abs(live["utc_unix"] - time.time()) < 1
    assert live["pairing_error_seconds"] > 0


def test_monotonic_reset_stops_session_and_remains_readable(tmp_path):
    history, session = selected_history()
    history.clock_update({"anchor": anchor(10), "breaks": []})
    history.clock_update({"anchor": anchor(5), "breaks": ["monotonic_reset"]})
    assert session.state == "interrupted" and session.ended == 10
    path = tmp_path / "clock-reset.json"
    save_session(session, path)
    assert History().load(path).to_dict() == session.to_dict()


def test_helper_failure_is_reported_without_unhandled_window(tmp_path, monkeypatch):
    from ace_scheduler import disk_helper
    def fail(path):
        raise OSError("counter unavailable")
    monkeypatch.setattr(disk_helper, "_run", fail)
    path = tmp_path / "mailbox.json"
    assert disk_helper.run(path) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["error"] == "counter unavailable"


@pytest.mark.parametrize("corrupt", [
    lambda d: d["session"].update(scene_name="x" * 201),
    lambda d: d["session"]["clock"]["anchors"][0].update(qpc_frequency=0),
    lambda d: d["disk_recording"]["rows"][0]["values"].update(read_iops=-1),
    lambda d: d["disk_recording"]["rows"].append(d["disk_recording"]["rows"][0]),
    lambda d: d["disk_recording"]["rows"][0].update(gap="missing"),
    lambda d: d["disk_recording"]["rows"][0]["values"].update(read_iops=0),
    lambda d: d.update(disk_recording=[]),
    lambda d: d["session"]["disk_selection"]["device"].update(volumes=4),
    lambda d: d["disk_recording"]["rows"][0].update(device_id="other-device"),
])
def test_bad_stage_four_import_preserves_history(tmp_path, corrupt):
    history, session = selected_history()
    history.disk_packet({"devices": None, "timestamp": 1, "rows": [row(1)]})
    data = session.to_dict()
    corrupt(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        history.load(path)
    assert list(history.sessions) == [session.id]
    assert history.disk_store.count == 1


def test_scene_ui_real_mouse_marker_and_readonly_reopen(app, tmp_path):
    from ace_scheduler.ui.main_window import MainWindow
    from ace_scheduler.config.models import AppConfig
    from ace_scheduler.config.config_manager import ConfigManager
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    session = window.history.begin(KEY, time.monotonic(), baseline_seconds=60)
    window.selected_experiment = session.id
    window.show_page(2)
    window.show()
    window.refresh_experiment()
    page = window.experiment_page
    page.scene_name.setText("测试场景")
    page.ensureWidgetVisible(page.scene_save)
    QTest.mouseClick(page.scene_save, Qt.MouseButton.LeftButton)
    page.ensureWidgetVisible(page.marker_button)
    QTest.mouseClick(page.marker_button, Qt.MouseButton.LeftButton)
    assert session.scene_name == "测试场景" and session.events[-1]["kind"] == "scene_marker"
    assert not window.armed and session.marker is None
    session.imported = True
    window.refresh_experiment()
    assert not page.marker_button.isEnabled() and page.scene_name.isReadOnly()
    window.close()


def test_live_helper_and_timeout_leave_scheduler_unaffected(app, tmp_path):
    from ace_scheduler.ui.disk_capture import DiskCapture
    capture = DiskCapture()
    packets, stopped = [], []
    capture.packet.connect(packets.append)
    capture.stopped.connect(stopped.append)
    try:
        capture.start()
        deadline = time.monotonic() + 6
        while len(packets) < 3 and time.monotonic() < deadline:
            QTest.qWait(100)
        assert len(packets) >= 3 and capture.active
        assert packets[0]["devices"] is not None
        # A bounded mailbox may overwrite the initial warmup before the GUI polls.
        for packet in packets:
            for r in packet["rows"]:
                validate_rows([r])
        capture.last_received = time.monotonic() - 9
        capture.poll()
        assert not capture.active and "超时" in stopped[-1]
    finally:
        capture.close()
    assert not capture.folder


def test_pdh_checks_sample_status_idle_reset_and_pause(monkeypatch):
    from types import SimpleNamespace
    from ace_scheduler.windows.disk_counters import DiskQuery, COUNTERS
    class Api:
        status = 0
        raw = 100
        def PdhOpenQueryW(self, a, b, target):
            target._obj.value = 1
            return 0
        def PdhAddEnglishCounterW(self, query, path, _, target):
            target._obj.value = list(COUNTERS.values()).index(path.split("\\")[-1]) + 1
            return 0
        def PdhCollectQueryData(self, query):
            return 0
        def PdhGetFormattedCounterValue(self, handle, flags, _, target):
            target._obj.value = 0 if handle.value == 4 else .001
            target._obj.status = self.status
            return 0
        def PdhGetRawCounterValue(self, handle, _, target):
            target._obj.first = self.raw
            target._obj.status = 0
            return 0
        def PdhCloseQuery(self, query):
            return 0
    api = Api()
    times = iter([1, 2, 3, 4, 10])
    monkeypatch.setattr("ace_scheduler.windows.disk_counters.time.monotonic", lambda: next(times))
    query = DiskQuery(SimpleNamespace(api=api), DEVICE)
    assert query.sample()["gap"] == "warmup"
    valid = query.sample()
    assert valid["values"]["write_latency_s"] is None
    assert valid["errors"]["write_latency_s"] == "no_requests"
    api.status = 0xc0000bc6
    invalid = query.sample()
    assert all(v is None for v in invalid["values"].values())
    assert invalid["errors"]
    api.status, api.raw = 1, 50
    assert query.sample()["gap"] == "counter_reset"
    assert query.sample()["gap"] == "sampling_pause"
    query.close()
