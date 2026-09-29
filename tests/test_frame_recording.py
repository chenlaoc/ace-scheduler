import copy
import csv
import hashlib
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from ace_scheduler.core import frame_recording as frames
from ace_scheduler.core.experiment import History, save_session, export_csv
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.ui.frame_import import FrameImportDialog
from tests.test_stage_four import anchor
from tests.test_ui import app


def session():
    history = History()
    history.clock_update({"anchor": anchor(10), "breaks": []})
    value = history.begin(ProcessIdentity(7, 100, "game.exe"), 10, baseline_seconds=1, utc_offset=1700000000)
    value.finish(11, "fixture observation")
    return history, value


def write_csv(path, rows=None, clock="CPUStartQPC", extra=None):
    header = ["Application", "ProcessID", "SwapChainAddress", "PresentRuntime", clock,
              "FrameTime", "DisplayedTime", "DisplayLatency"]
    if extra:
        header.append(extra)
    if rows is None:
        rows = [["game.exe", 7, "0x1", "DXGI", 100000000 + i * 1000000, 100, 100, 0] for i in range(10)]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def source(tmp_path, rows=None, clock="CPUStartQPC", log="Started recording.\nStopped recording."):
    path = write_csv(tmp_path / "frames.csv", rows, clock)
    log_path = tmp_path / "capture.log"
    log_path.write_text(log, encoding="utf-16")
    return frames.read_presentmon(path, frames.VERSIONS[0], log_path), path


def attach(history, value, store, **kwargs):
    return history.attach_frames(value, store, 0, mode="qpc", same_boot=True, **kwargs)


def test_roundtrip_independent_analysis_export_and_source_hash(tmp_path):
    history, value = session()
    original = value.to_dict()
    store, path = source(tmp_path)
    clone = attach(history, value, store)
    assert clone.imported and clone.id != value.id and value.to_dict() == original
    assert clone.frame_attachment["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    report = clone.frame_report()["before"]
    for metric in report.values():
        assert metric["coverage"] == pytest.approx(1)
        assert metric["median_ms"] == metric["p95_ms"] == metric["p99_ms"] == 100
        assert metric["long_frames"] == 10
        assert metric["coverage_and_log_gate_passed"]
    saved = tmp_path / "session.json"
    save_session(clone, saved)
    loaded = History().load(saved)
    assert loaded.to_dict() == clone.to_dict()
    export_csv(loaded, tmp_path / "export.csv")
    rows = list(csv.DictReader((tmp_path / "export.csv").open(encoding="utf-8-sig")))
    assert sum(r["row_type"] == "frame_sample" for r in rows) == 10
    assert json.loads(rows[0]["context_json"])["session"]["frame_attachment"]["row_fields"] == frames.ROW_FIELDS


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.__setitem__(1, rows[0][:]),
    lambda rows: rows[1].__setitem__(4, 99999999),
    lambda rows: rows[1].__setitem__(5, "nan"),
    lambda rows: rows[1].__setitem__(5, "-1"),
    lambda rows: rows[1].__setitem__(6, "bad"),
    lambda rows: rows[1].append("unexpected"),
    lambda rows: rows[1].pop(),
])
def test_corrupt_csv_rejected_with_line_number(tmp_path, mutation):
    rows = [["game.exe", 7, "0x1", "DXGI", 100000000 + i * 1000000, 100, "NA", "NA"] for i in range(2)]
    mutation(rows)
    path = write_csv(tmp_path / "bad.csv", rows)
    with pytest.raises(ValueError, match="第 3 行"):
        frames.read_presentmon(path, frames.VERSIONS[0])


def test_unknown_format_empty_and_resource_limits(tmp_path, monkeypatch):
    path = write_csv(tmp_path / "unknown.csv", extra="FrameType")
    with pytest.raises(ValueError, match="未知 CSV 表头"):
        frames.read_presentmon(path, frames.VERSIONS[0])
    path = write_csv(path, [])
    with pytest.raises(ValueError, match="没有帧"):
        frames.read_presentmon(path, frames.VERSIONS[0])
    write_csv(path)
    with pytest.raises(ValueError, match="明确声明"):
        frames.read_presentmon(path, "unknown")
    monkeypatch.setattr(frames, "MAX_ROWS", 3)
    with pytest.raises(ValueError, match="100000"):
        frames.read_presentmon(path, frames.VERSIONS[0])
    monkeypatch.setattr(frames, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="32 MiB"):
        frames.read_presentmon(path, frames.VERSIONS[0])


def test_multiple_streams_not_merged_and_pid_mismatch_rejected(tmp_path):
    history, value = session()
    rows = [["game.exe", 7, chain, "DXGI", 100000000 + i * 1000000, duration, "NA", "NA"]
            for i in range(10) for chain, duration in (("0x1", 100), ("0x2", 50))]
    rows.append(["other.exe", 8, "0x3", "DXGI", 100000000, 10, "NA", "NA"])
    store, _ = source(tmp_path, rows)
    assert len(store.streams) == 3
    with pytest.raises(ValueError, match="请选择"):
        frames.make_attachment(value, store, None, "qpc", same_boot=True)
    with pytest.raises(ValueError, match="不一致"):
        history.attach_frames(value, store, 2, mode="qpc", same_boot=True)
    clone = history.attach_frames(value, store, 1, mode="qpc", same_boot=True)
    assert clone.frame_report()["before"]["cpu_frame_interval_ms"]["coverage"] == pytest.approx(.5)
    assert clone.frame_report()["before"]["display_residency_ms"]["median_ms"] is None


def test_manual_alignment_boundary_clipping_and_no_overlap(tmp_path):
    history, value = session()
    rows = [["game.exe", 7, "0x1", "DXGI", 5000 + i * 100, 100, "NA", "NA"] for i in range(10)]
    store, _ = source(tmp_path, rows, "CPUStartTime")
    clone = history.attach_frames(value, store, 0, mode="manual", offset=-.05)
    stats = clone.frame_report()["before"]["cpu_frame_interval_ms"]
    assert stats["coverage"] == pytest.approx(.95)
    assert stats["frames_fully_inside"] == 9
    assert any("手动" in text for text in stats["quality_reasons"])
    path = tmp_path / "manual.json"
    save_session(clone, path)
    assert History().load(path).to_dict() == clone.to_dict()
    end_clip = history.attach_frames(value, store, 0, mode="manual", offset=.05)
    clipped = end_clip.frame_report()["before"]["cpu_frame_interval_ms"]
    assert clipped["coverage"] == pytest.approx(.95)
    assert clipped["frames_fully_inside"] == 9
    empty = history.attach_frames(value, store, 0, mode="manual", offset=20)
    stats = empty.frame_report()["before"]["cpu_frame_interval_ms"]
    assert stats["coverage"] == 0 and stats["median_ms"] is None
    assert "无有效窗口交集" in stats["quality_reasons"]


def test_display_intervals_use_display_latency_and_clock_breaks(tmp_path):
    history, value = session()
    value.clock["breaks"] = [{"started": 10.2, "timestamp": 10.4, "reasons": ["sleep_or_resume"]}]
    rows = [["game.exe", 7, "0x1", "DXGI", 100000000 + i * 1000000, 100, 50, 500] for i in range(10)]
    store, _ = source(tmp_path, rows)
    clone = attach(history, value, store)
    report = clone.frame_report()["before"]
    assert report["cpu_frame_interval_ms"]["coverage"] == pytest.approx(.8)
    assert report["display_residency_ms"]["coverage"] == pytest.approx(.25)
    assert report["display_residency_ms"]["median_ms"] == 50
    assert any("断点" in r for r in report["cpu_frame_interval_ms"]["quality_reasons"])


def test_qpc_alignment_requires_confirmation_and_consistent_anchors(tmp_path):
    _, value = session()
    store, _ = source(tmp_path)
    with pytest.raises(ValueError, match="同一次"):
        frames.make_attachment(value, store, 0, "qpc")
    value.clock["anchors"].append({**anchor(11), "qpc": 150000000})
    with pytest.raises(ValueError, match="不一致"):
        frames.make_attachment(value, store, 0, "qpc", same_boot=True)
    value.clock["anchors"] = []
    with pytest.raises(ValueError, match="没有 QPC"):
        frames.make_attachment(value, store, 0, "qpc", same_boot=True)


def test_log_losses_unknown_and_zero_missing_semantics(tmp_path):
    history, value = session()
    rows = [["game.exe", 7, "0x1", "DXGI", 100000000, 0, "NA", "NA"]]
    store, path = source(tmp_path, rows, log="warning: 123 ETW events were lost.")
    stats = attach(history, value, store).frame_report()["before"]["cpu_frame_interval_ms"]
    assert any("123" in r for r in stats["quality_reasons"])
    unknown = frames.read_presentmon(path, frames.VERSIONS[0])
    stats = attach(history, value, unknown).frame_report()["before"]["display_residency_ms"]
    assert stats["median_ms"] is None
    assert any("未知" in r for r in stats["quality_reasons"])


@pytest.mark.parametrize("log", ["", "unrelated output", "Started recording.",
                                 "Stopped recording.\nStarted recording.",
                                 "Started recording.\nStopped recording.\nStarted recording."])
def test_incomplete_log_never_passes_quality_gate(tmp_path, log):
    history, value = session()
    store, _ = source(tmp_path, log=log)
    stats = attach(history, value, store).frame_report()["before"]["cpu_frame_interval_ms"]
    assert stats["coverage"] == pytest.approx(1)
    assert not stats["coverage_and_log_gate_passed"]
    assert any("日志不完整" in reason for reason in stats["quality_reasons"])


@pytest.mark.parametrize("mutation", [
    lambda d: d["session"]["frame_attachment"]["alignment"].update(monotonic_origin=0),
    lambda d: d["frame_recording"]["rows"][1].__setitem__(0, 100000000),
    lambda d: d["frame_recording"]["rows"][0].__setitem__(1, -1),
    lambda d: d["session"]["frame_attachment"]["source"].update(sha256="bad"),
    lambda d: d["session"]["frame_attachment"]["source"]["units"].update(FrameTime="seconds"),
    lambda d: d["session"]["frame_attachment"]["source"]["log"].update(capture_complete="true"),
    lambda d: d["session"]["frame_attachment"]["stream"].update(pid=8),
    lambda d: d["session"]["frame_attachment"].update(long_frame_threshold_ms=0),
])
def test_corrupt_portable_attachments_rejected_atomically(tmp_path, mutation):
    history, value = session()
    store, _ = source(tmp_path)
    data = attach(history, value, store).to_dict()
    mutation(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    empty = History()
    with pytest.raises((ValueError, TypeError)):
        empty.load(path)
    assert not empty.sessions


def test_legacy_log_without_completeness_remains_readable_but_unknown(tmp_path):
    history, value = session()
    store, _ = source(tmp_path)
    data = attach(history, value, store).to_dict()
    del data["session"]["frame_attachment"]["source"]["log"]["capture_complete"]
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    stats = History().load(path).frame_report()["before"]["cpu_frame_interval_ms"]
    assert not stats["coverage_and_log_gate_passed"]


def test_old_schema_two_without_frames_still_opens(tmp_path):
    _, value = session()
    data = value.to_dict()
    data["schema"] = 2
    data.pop("frame_recording")
    data["session"].pop("frame_attachment")
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert History().load(path).frame_report() == {}


def test_dialog_real_input_parser_selection_and_confirmation(app, tmp_path):
    _, value = session()
    path = write_csv(tmp_path / "frames.csv")
    dialog = FrameImportDialog(value)
    dialog.show()
    dialog.path.setText(str(path))
    QTest.mouseClick(dialog.parse_button, Qt.MouseButton.LeftButton)
    for _ in range(100):
        QTest.qWait(20)
        if dialog.thread and not dialog.thread.isRunning():
            app.processEvents()
            break
    assert dialog.store is not None
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    dialog.stream.setFocus()
    QTest.keyClick(dialog.stream, Qt.Key.Key_Down)
    assert not ok.isEnabled()
    dialog.same_boot.setFocus()
    QTest.keyClick(dialog.same_boot, Qt.Key.Key_Space)
    assert ok.isEnabled()
    assert "CPU 帧起点间隔" in dialog.preview.toPlainText()
    assert "丢失情况未知" in dialog.preview.toPlainText()
    QTest.mouseClick(ok, Qt.MouseButton.LeftButton)
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.store.close()
    dialog.deleteLater()
