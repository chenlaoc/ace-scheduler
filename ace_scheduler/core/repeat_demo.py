"""Deterministic synthetic studies. No processes, clocks, ETW or device APIs are queried."""
from dataclasses import asdict
import csv
from pathlib import Path
import tempfile

from .experiment import History
from .frame_recording import VERSIONS, read_presentmon
from .process_metrics import ProcessIdentity, Metrics
from .repeated_experiments import RepeatedStudy
from .disk_recording import FIELDS


def demo_sessions():
    sessions = []
    device = {"id": "fixture-disk", "name": "SYNTHETIC DISK", "number": 0, "instance": "fixture",
              "volumes": ["TEST:"], "fingerprint": "synthetic-device-identity", "uncertainty": []}
    original = {"priority": 32, "affinity": [0, 1, 2, 3], "eco": {"control": 0, "state": 0}, "errors": {}}
    applied = {**original, "priority": 64}
    # Three independent eligible pairs, then incomplete coverage, interruption, and version mismatch.
    cases = [(16, "完整轮次 1", "1.0"), (20, "完整轮次 2", "1.0"), (24, "完整轮次 3", "1.0"),
             (16, "低覆盖轮次", "1.0"), (16, "中断轮次", "1.0"), (12, "其他应用版本", "2.0")]
    with tempfile.TemporaryDirectory(prefix="ace-repeat-demo-") as temporary:
        folder = Path(temporary)
        for index, (after_ms, title, version) in enumerate(cases):
            start = 10 + index * 20
            history = History()
            history.disk_packet({"devices": [device], "timestamp": start, "rows": []})
            history.selected_disk = device["id"]
            history.clock_update({"anchor": {"monotonic": start, "utc_unix": 1700000000 + start,
                "qpc": start * 10000000, "qpc_frequency": 10000000, "pairing_error_seconds": .0001,
                "awake_seconds": start, "source": "synthetic", "utc_accuracy": "synthetic"}, "breaks": []})
            identity = ProcessIdentity(700 + index, 100 + index, "synthetic-game.exe")
            session = history.begin(identity, start, baseline_seconds=4, transition_seconds=1, after_seconds=4, utc_offset=1700000000)
            session.set_scene("合成场景：固定测试路线", "测试数据；固定条件；不代表真实设备或游戏")
            def add_sample(t, before):
                state = original if before else applied
                factor = 1 if before else after_ms / 20
                history.add(identity, Metrics(t, cpu_percent=10 * factor, read_mbps=2 * factor, write_mbps=1,
                                               sample_seconds=1), state)
                values = dict.fromkeys(FIELDS, 0)
                values.update(read_B_s=2_000_000 * factor, write_B_s=1_000_000,
                              read_iops=20, write_iops=10, read_latency_s=.002 * factor,
                              write_latency_s=.001, queue_mean=.5 * factor, queue_current=1)
                history.disk_packet({"devices": None, "timestamp": t, "rows": [{"device_id": device["id"], "timestamp": t,
                    "seconds": 1, "values": values, "errors": {}, "gap": None}]})
            for t in range(start + 1, start + 5):
                add_sample(t, True)
            result = {"ok": True, "status": "合成回读成功", "operations": [{"field": "priority", "ok": True,
                      "changed": True, "untouched": False, "requested": 64, "original": 32, "actual": 64,
                      "verified": True, "recovery_pending": False, "message": "仅合成记录，无系统调用"}]}
            history.operation({"kind": "apply", "timestamp": start + 4, "ended": start + 4,
                "identity": asdict(identity), "before": original, "after": applied, "result": result,
                "policy": {"priority": "Idle", "affinity": {"mode": "unchanged"}, "eco": "unchanged"},
                "context": {"synthetic": True, "app_version": "synthetic-scheduler", "topology": {"available": [0, 1, 2, 3], "groups": 1},
                            "sampling": {"monitor_interval": 1}, "target": {"version": version}}})
            for t in range(start + 6, start + 10):
                if index == 4 and t == start + 8:
                    session.finish(t, "合成中断：窗口未完成")
                    break
                if index == 3 and t > start + 7:
                    session.advance(t)
                    continue
                add_sample(t, False)
            path = folder / f"frames-{index}.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Application", "ProcessID", "SwapChainAddress", "PresentRuntime", "CPUStartQPC", "FrameTime", "DisplayedTime", "DisplayLatency"])
                for left, length, ms in ((start, 4000, 20), (start + 5, 2000 if index == 3 else 4000, after_ms)):
                    elapsed = 0
                    while elapsed < length:
                        duration = min(ms, length - elapsed)
                        writer.writerow([identity.name, identity.pid, "0x01", "DXGI", left * 10000000 + elapsed * 10000, duration, duration, 0])
                        elapsed += duration
            log = folder / "capture.log"
            log.write_text("SYNTHETIC FIXTURE ONLY\nStarted recording.\nStopped recording.\n", encoding="utf-8")
            store = read_presentmon(path, VERSIONS[0], log)
            clone = history.attach_frames(session, store, 0, mode="qpc", same_boot=True)
            clone.label = "测试数据 · " + title
            sessions.append(clone)
    return sessions


def demo_study():
    sessions = demo_sessions()
    try:
        study = RepeatedStudy(sessions)
    finally:
        for session in sessions:
            session._disk_store.close()
            session._frame_store.close()
    for declaration in study.declarations.values():
        declaration["hardware"] = "SYNTHETIC CPU / GPU / RAM：仅测试用"
    return study
