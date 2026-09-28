"""Native tray/window lifecycle check; no monitor worker or scheduling writes."""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.ui.theme import apply_palette


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/tray-native"))
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_palette(app)
    window = MainWindow(AppConfig(close_to_tray=True), ConfigManager(output / "config.json"),
                        read_only=True, start_worker=False, enable_tray=True)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.on_ready(CpuTopology.detect())
    window.show()
    QTest.qWait(150)
    available = QSystemTrayIcon.isSystemTrayAvailable()
    result = {"tray_available": available, "no_worker": window.thread is None, "read_only": window.read_only}
    try:
        if available:
            assert window.tray.icon.isVisible()
            window.close()
            assert not window.isVisible() and window.background_hidden
            window.tray.open_action.trigger()
            QTest.qWait(100)
            assert window.isVisible() and not window.background_hidden
            result["hide_and_reopen"] = True
            window.show_page(3)
            window.grab().save(str(output / "settings.png"))
            window.tray.menu.popup(window.mapToGlobal(window.rect().center()))
            QTest.qWait(100)
            window.tray.menu.grab().save(str(output / "tray-menu.png"))
            window.tray.menu.hide()
        window.tray.exit_action.trigger()
        assert not window.isVisible() and not window.tray.icon.isVisible()
        result["explicit_exit"] = True
    finally:
        window.allow_close = True
        window.close()
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
