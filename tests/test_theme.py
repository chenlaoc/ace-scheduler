import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QMessageBox

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.ui.affinity_dialog import AffinityDialog
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.ui.theme import apply_palette, is_dark
from tests.test_ui import app


@pytest.mark.parametrize("mode", ["system", "light", "dark"])
def test_theme_roundtrip_and_old_config(tmp_path, mode):
    manager = ConfigManager(tmp_path / "config.json")
    manager.save(AppConfig(theme=mode))
    assert manager.load()[0].theme == mode
    original = AppConfig().to_dict()
    original.pop("theme")
    assert AppConfig.parse(original).theme == "system"
    original["theme"] = "future-theme"
    recovered = AppConfig.parse(original)
    assert recovered.theme == "system" and recovered.rules == AppConfig().rules


def test_theme_switch_preserves_drafts_selection_and_monitor_state(app, tmp_path, monkeypatch):
    manager = ConfigManager(tmp_path / "config.json")
    window = MainWindow(AppConfig(theme="light"), manager, start_worker=False)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    window.policy_page.set_preset(("sguard64.exe",), "Strong")
    window.armed = {"sguard64.exe"}
    drafts = copy.deepcopy(window.policy_page.drafts)
    selection = window.policy_page.selected.copy()
    writes = []
    window.apply_many_requested.connect(writes.append)
    window.restore_requested.connect(writes.append)
    window.show()
    app.processEvents()
    before = window.centralWidget().grab().toImage().pixelColor(0, 0)
    combo = window.settings_page.theme_choice
    combo.setCurrentIndex(combo.findData("dark"))
    combo.activated.emit(combo.currentIndex())
    app.processEvents()
    assert manager.load()[0].theme == "dark" and is_dark(window)
    assert window.centralWidget().grab().toImage().pixelColor(0, 0).lightness() < before.lightness()
    assert not writes and window.armed == {"sguard64.exe"}
    assert window.policy_page.drafts == drafts and window.policy_page.selected == selection
    assert window.config.rules[0].policy == preset("Default")
    dialog = AffinityDialog(window.topology, [window.config.rules[0]], window)
    assert is_dark(dialog) and dialog.palette().color(QPalette.ColorRole.Text).lightness() > 128
    dialog.close()

    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
    monkeypatch.setattr(manager, "save", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    combo.setCurrentIndex(combo.findData("light"))
    combo.activated.emit(combo.currentIndex())
    assert errors and combo.currentData() == "dark" and is_dark(window)
    assert window.config.theme == "dark"
    window.close()

    reopened = MainWindow(manager.load()[0], manager, start_worker=False)
    assert is_dark(reopened) and reopened.settings_page.theme_choice.currentData() == "dark"
    reopened.close()
    apply_palette(app, "system")


def test_system_notifications_follow_only_in_adaptive_mode(app):
    controller = apply_palette(app, "system")
    hints = app.styleHints()
    try:
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        app.processEvents()
        assert controller.effective == "dark"
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        app.processEvents()
        assert controller.effective == "light"
        controller.set_mode("dark")
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        app.processEvents()
        assert controller.effective == "dark"
        controller.set_mode("light")
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        app.processEvents()
        assert controller.effective == "light"
        controller.set_mode("system")
        assert controller.effective == "dark"
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Unknown)
        app.processEvents()
        assert controller.effective == "light"
    finally:
        controller.system_changed(hints.colorScheme())
        controller.set_mode("system")
        app.processEvents()
