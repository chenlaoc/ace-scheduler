"""Offline Qt input and screenshot acceptance; never starts capture or monitoring."""
import argparse
import csv
import json
from pathlib import Path
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.core.experiment import History, save_session, export_csv
from ace_scheduler.core.frame_recording import read_presentmon, VERSIONS
from ace_scheduler.core.process_metrics import ProcessIdentity
from ace_scheduler.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-folder", type=Path)
    args = parser.parse_args()
    folder = Path("artifacts/stage-four/frame-import-validation")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "fixture.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Application", "ProcessID", "SwapChainAddress", "PresentRuntime", "CPUStartTime", "FrameTime", "DisplayedTime", "DisplayLatency"])
        for i in range(100):
            writer.writerow(["fixture.exe", 7, "0x01", "DXGI", i * 10, 10, "NA" if i % 2 else 10, 0])
    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppConfig(), ConfigManager(folder / "config.json"), read_only=True, start_worker=False)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    original = window.history.begin(ProcessIdentity(7, 100, "fixture.exe"), 10, baseline_seconds=1, utc_offset=1700000000)
    original.finish(11, "synthetic fixture")
    window.selected_experiment = original.id
    window.show_page(2)
    window.show()
    window.refresh_experiment()
    page = window.experiment_page
    stage, errors = [0], []
    def drive():
        dialog = app.activeModalWidget()
        if not dialog:
            return
        try:
            if stage[0] == 0:
                dialog.path.setText(str(path.resolve()))
                QTest.mouseClick(dialog.parse_button, Qt.MouseButton.LeftButton)
                stage[0] = 1
            elif stage[0] == 1 and dialog.store and not dialog.thread.isRunning():
                stage[0] = 2
                dialog.stream.setFocus()
                QTest.keyClick(dialog.stream, Qt.Key.Key_Down)
                assert dialog.mode.currentData() == "manual"
                assert "近似" in dialog.preview.toPlainText()
                dialog.grab().save(str(folder / "import-dialog.png"))
                QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        except Exception as exc:
            errors.append(repr(exc))
            dialog.reject()
    timer = QTimer()
    timer.timeout.connect(drive)
    timer.start(30)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: app.activeModalWidget().reject() if app.activeModalWidget() else None)
    timeout.start(15000)
    try:
        page.ensureWidgetVisible(page.frame_import)
        app.processEvents()
        QTest.mouseClick(page.frame_import, Qt.MouseButton.LeftButton)
        timer.stop()
        timeout.stop()
        assert not errors, errors
        clone = window.current_experiment()
        assert clone.id != original.id and clone.imported and not original.frame_attachment
        assert not window.armed and window.thread is None and not page.disk_enabled.isChecked()
        for theme in ("light", "dark"):
            window.theme_controller.set_mode(theme)
            for width, height in ((1280, 860), (1040, 700)):
                window.resize(width, height)
                app.processEvents()
                QTest.qWait(50)
                page.verticalScrollBar().setValue(page.frame_import.mapTo(page.content, QPoint(0, 0)).y() - 60)
                app.processEvents()
                window.grab().save(str(folder / f"{theme}-{width}.png"))
        save_session(clone, folder / "fixture-session.json")
        assert History().load(folder / "fixture-session.json").to_dict() == clone.to_dict()
        result = {"input_flow": "import button -> async parse -> explicit stream -> analysis copy",
                  "original_unchanged": True, "scheduling_armed": len(window.armed), "monitor_started": False,
                  "fixture": clone.frame_report()}
        if args.sample_folder:
            manifest = json.loads((args.sample_folder / "manifest.json").read_text(encoding="utf-8"))
            name = next(n for n in manifest["sessions"] if n.startswith(f"session-{manifest['target_pid']}-"))
            history = History()
            target = history.load(args.sample_folder / name)
            started = time.perf_counter()
            store = read_presentmon(args.sample_folder / "frames.csv", VERSIONS[0], args.sample_folder / "presentmon.log")
            selected = next(i for i, s in enumerate(store.streams) if s['pid'] == target.identity.pid)
            analysis = history.attach_frames(target, store, selected, mode="qpc", same_boot=True)
            result["existing_sample_parse_and_attach_seconds"] = time.perf_counter() - started
            result["existing_sample"] = analysis.frame_report()
            save_session(analysis, folder / "existing-sample-analysis.json")
            assert History().load(folder / "existing-sample-analysis.json").to_dict() == analysis.to_dict()
            export_csv(analysis, folder / "existing-sample-export.csv")
        (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"success": True, "screenshots": 5, "result": str(folder / 'result.json')}, ensure_ascii=False))
    finally:
        timer.stop()
        timeout.stop()
        window.close()


if __name__ == "__main__":
    main()
