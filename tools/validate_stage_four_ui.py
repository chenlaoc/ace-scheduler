"""Real Qt input on a read-only window monitoring this disposable Python process."""
import json
import argparse
from pathlib import Path
import time

import psutil
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, ProcessRule
from ace_scheduler.core.experiment import History, save_session, export_csv
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hidden-seconds", type=int, default=10)
    args = parser.parse_args()
    if not 10 <= args.hidden_seconds <= 600:
        parser.error("hidden-seconds must be 10..600")
    folder = Path("artifacts/stage-four/ui-validation")
    folder.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    process = psutil.Process()
    window = MainWindow(AppConfig(rules=[ProcessRule(process.name())]), ConfigManager(folder / "config.json"), read_only=True)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show_page(2)
    window.show()
    page = window.experiment_page
    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(25)
    heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
    heartbeat.start()
    def click(widget, checkbox=False):
        page.ensureWidgetVisible(widget)
        app.processEvents()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(8, widget.height() // 2) if checkbox else widget.rect().center())
        app.processEvents()
    def choose(combo, index):
        page.ensureWidgetVisible(combo)
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=QPoint(combo.width() - 10, combo.height() // 2))
        QTest.keyClick(combo.view(), Qt.Key.Key_Home)
        for _ in range(index):
            QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        app.processEvents()
    def observe(seconds):
        ticks.clear()
        start, cpu = time.perf_counter(), process.cpu_times()
        memory, handles = [], []
        for _ in range(seconds * 10):
            QTest.qWait(100)
            memory.append(process.memory_info().rss)
            handles.append(process.num_handles())
        end_cpu = process.cpu_times()
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        return {"seconds": time.perf_counter() - start,
                "cpu_seconds": end_cpu.user + end_cpu.system - cpu.user - cpu.system,
                "rss_range": [min(memory), max(memory)], "handles_range": [min(handles), max(handles)],
                "gui_tick_p95_ms": sorted(gaps)[int(len(gaps) * .95)] * 1000,
                "gui_tick_max_ms": max(gaps) * 1000}
    result = {}
    try:
        result["collection_off"] = observe(10)
        identity = ProcessIdentity(process.pid, process.create_time(), process.name())
        index = next((i for i in range(page.picker.count()) if page.picker.itemData(i) == ("instance", identity)), -1)
        assert index >= 0
        choose(page.picker, index)
        click(page.disk_enabled, True)
        QTest.qWait(2500)
        assert window.history.disk_devices
        choose(page.disk_picker, 1)
        page.scene_name.setFocus()
        QTest.keyClicks(page.scene_name, "File-load validation")
        click(page.begin_button)
        session = window.current_experiment()
        assert session and session.disk_selection
        click(page.marker_button)
        assert session.events[-1]["kind"] == "scene_marker"
        result["collection_on"] = observe(10)
        window.hide()
        result["collection_on_hidden"] = observe(args.hidden_seconds)
        window.show()
        for theme in ("light", "dark"):
            window.theme_controller.set_mode(theme)
            for width, height in ((1280, 860), (1040, 700)):
                window.resize(width, height)
                app.processEvents()
                for name, widget in (("scene", page.scene_name), ("disk", page.disk_details)):
                    page.ensureWidgetVisible(widget)
                    app.processEvents()
                    window.grab().save(str(folder / f"{theme}-{width}-{name}.png"))
        click(page.finish_button)
        path = folder / "session.json"
        save_session(session, path)
        export_csv(session, folder / "session.csv")
        reopened = History().load(path)
        assert reopened.to_dict() == session.to_dict()
        assert not window.armed and session.marker is None
        result.update({"read_only": window.read_only, "armed_rules": len(window.armed),
                       "disk_samples": len(session.disk_rows()), "scene": session.scene_name,
                       "events": [e["kind"] for e in session.events], "roundtrip_equal": True,
                       "qpc_anchors": len(session.clock["anchors"]), "device_pixel_ratio": window.devicePixelRatioF()})
    finally:
        heartbeat.stop()
        window.close()
        deadline = time.monotonic() + 5
        while window.thread and window.thread.isRunning() and time.monotonic() < deadline:
            QTest.qWait(50)
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
