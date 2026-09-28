"""User-event regression scenarios; dialogs are driven, not replaced by canned answers."""
import copy
import csv
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialogButtonBox, QFileDialog, QInputDialog,
                               QLineEdit, QMessageBox, QPushButton)

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, AffinitySpec, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_metrics import Metrics, ProcessIdentity
from ace_scheduler.core.process_monitor import MonitorWorker, ProcessRow
from ace_scheduler.ui.main_window import MainWindow
from tests.test_ui import app


@pytest.fixture
def window(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    window.show()
    app.processEvents()
    yield window
    window.thread = None
    window.close()
    window.deleteLater()
    app.processEvents()


def click(widget):
    point = QPoint(8, widget.height() // 2) if isinstance(widget, QCheckBox) else widget.rect().center()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point)
    QApplication.processEvents()


def button(parent, text):
    return next(value for value in parent.findChildren(QPushButton) if value.text() == text)


def choose(combo, value):
    """Use a popup and keyboard activation instead of emitting application signals."""
    target = combo.findData(value)
    assert target >= 0
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=QPoint(combo.width() - 10, combo.height() // 2))
    QApplication.processEvents()
    view = combo.view()
    assert view.isVisible()
    QTest.keyClick(view, Qt.Key.Key_Home)
    for _ in range(target):
        QTest.keyClick(view, Qt.Key.Key_Down)
    QTest.keyClick(view, Qt.Key.Key_Return)
    QApplication.processEvents()


def in_modal(action, trigger):
    errors = []
    def run():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog is not None
            action(dialog)
        except BaseException as exc:
            errors.append(exc)
            if dialog:
                dialog.reject()
    QTimer.singleShot(20, run)
    trigger()
    assert not errors, str(errors)


def warning_close(dialog):
    assert isinstance(dialog, QMessageBox)
    click(dialog.button(QMessageBox.StandardButton.Ok))


@pytest.mark.parametrize("size", [(1280, 860), (1040, 700)])
def test_overview_metrics_toggle_preserves_monitoring(window, size):
    from ace_scheduler.errors import ExceptionReporter

    window.resize(*size)
    identity = ProcessIdentity(123, 100, "test.exe")
    armed = {identity.name}
    window.on_snapshot([ProcessRow(identity.name, identity.pid, identity, Metrics(10, 1, 2, 3))], armed, 1)
    window.table.selectRow(0)
    initial_badge = window.session_badge.text()
    toggle = button(window.overview_page, "显示全部指标")
    halted = []
    window.halt_requested.connect(halted.append)
    reporter = ExceptionReporter(window)
    try:
        for sample, expanded in enumerate((True, False, True, False), start=11):
            click(toggle)
            assert not reporter.reported and not window.worker_failure and not halted
            assert toggle.text() == ("收起扩展指标" if expanded else "显示全部指标")
            assert toggle.isChecked() is expanded
            for column in (3, 6, 7, 8, 9, 10):
                assert window.table.isColumnHidden(column) is not expanded
            window.on_snapshot([ProcessRow(identity.name, identity.pid, identity,
                                           Metrics(sample, sample, 2, 3))], armed, 1)
            assert window.table.item(0, 2).text() == f"{sample:.2f}"
            assert window.table.selected_identity() == identity
            assert window.selected_identity == identity
            assert window.armed == armed and window.restore_count == 1
            assert window.session_badge.text() == initial_badge
    finally:
        reporter.close()


def test_row_keyboard_edit_and_partial_save_survive_navigation(window):
    click(window.nav_buttons[1])
    page = window.policy_page
    a, b, *_ = page.selected_keys()
    choose(page.controls[a].preset, "Strong")
    choose(page.controls[b].priority, "Below Normal")
    click(page.controls[b].eco)
    click(page.controls[b].keep)
    assert page.rule(a).policy == preset("Strong")
    assert page.rule(b).policy.priority == "Below Normal" and page.rule(b).keep_enforced
    click(page.selection_buttons[2])
    assert not page.save_button.isEnabled()
    click(page.controls[a].select)
    click(page.save_button)
    saved, warning = window.manager.load()
    assert not warning and saved.rules[0].policy == preset("Strong")
    assert saved.rules[1].policy == preset("Default")
    assert set(page.drafts) == {b}
    click(window.nav_buttons[2])
    window.on_snapshot([], set(), 0)
    click(window.nav_buttons[1])
    assert page.rule(b).keep_enforced
    click(page.selection_buttons[0])
    click(page.discard_button)
    assert not page.drafts and page.rule(b).policy == preset("Default")


def test_cpu_modal_invalid_confirm_then_valid_and_escape(window):
    click(window.nav_buttons[1])
    page = window.policy_page
    def edit(dialog):
        click(button(dialog.editor, "清空"))
        click(dialog.confirm)
        assert dialog.isVisible() and dialog.chosen_spec is None
        assert "至少" in dialog.hint.text()
        click(button(dialog.editor, "最后 2 个"))
        click(dialog.confirm)
    in_modal(edit, lambda: click(page.cpu_button))
    assert all(page.rule(key).policy.affinity == AffinitySpec("last_n", count=2) for key in page.selected_keys())
    before = copy.deepcopy(page.drafts)
    def cancel(dialog):
        click(button(dialog.editor, "50%"))
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    in_modal(cancel, lambda: click(page.cpu_button))
    assert page.drafts == before


def test_mixed_cpu_modal_and_bulk_maintenance(window):
    click(window.nav_buttons[1])
    page = window.policy_page
    a = page.selected_keys()[0]
    choose(page.controls[a].preset, "Strong")
    def edit(dialog):
        assert dialog.mixed and not dialog.confirm.isEnabled()
        click(button(dialog.editor, "25%"))
        assert dialog.confirm.isEnabled()
        click(dialog.confirm)
    in_modal(edit, lambda: click(page.cpu_button))
    choose(page.bulk_keep, True)
    assert all(page.rule(key).keep_enforced for key in page.selected_keys())
    assert page.rule(a).policy.priority == "Idle"
    assert page.rule(page.selected_keys()[1]).policy.priority == "Normal"


def test_select_enabled_respects_draft_and_add_cancel_keeps_config(window):
    click(window.nav_buttons[1])
    page = window.policy_page
    key = page.selected_keys()[0]
    before = copy.deepcopy(window.config)
    click(page.controls[key].enabled)
    drafts = copy.deepcopy(page.drafts)
    click(page.selection_buttons[1])
    assert len(page.selected) == 4 and key not in page.selected
    assert window.config == before and page.drafts == drafts
    in_modal(lambda dialog: QTest.keyClick(dialog, Qt.Key.Key_Escape), lambda: click(page.add_button))
    assert window.config == before and page.drafts == drafts


@pytest.mark.parametrize("name,expected", [("demo.exe", "success"), ("SGUARD64.EXE", "duplicate"),
                                             (r"C:\bad.exe", "invalid"), ("driver.sys", "invalid")])
def test_add_rule_modal_validation(window, name, expected):
    click(window.nav_buttons[1])
    page = window.policy_page
    initial = len(window.config.rules)
    def edit(dialog):
        assert isinstance(dialog, QInputDialog)
        field = dialog.findChild(QLineEdit)
        click(field)
        QTest.keyClicks(field, name)
        if expected != "success":
            QTimer.singleShot(20, lambda: warning_close(QApplication.activeModalWidget()))
        click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok))
    in_modal(edit, lambda: click(page.add_button))
    assert len(window.config.rules) == initial + (expected == "success")
    if expected == "success":
        assert page.rule(name).name == name
        click(page.selection_buttons[0])
        click(page.remove_button)
        assert len(window.config.rules) == initial
        assert all(rule.builtin for rule in window.config.rules)


def test_save_error_dialog_keeps_drafts_and_avoids_apply(window, monkeypatch):
    click(window.nav_buttons[1])
    page = window.policy_page
    click(page.preset_buttons["Strong"])
    before = copy.deepcopy(window.config)
    requests = []
    window.apply_many_requested.connect(requests.append)
    monkeypatch.setattr(window.manager, "save", lambda _: (_ for _ in ()).throw(OSError("Injected disk full")))
    in_modal(warning_close, lambda: click(page.apply_button))
    assert window.config == before and len(page.drafts) == 5
    assert not requests and window.pending_commands == 0


def test_settings_save_failure_rolls_back_ui_memory_and_future_save(window, monkeypatch):
    click(window.nav_buttons[3])
    original = copy.deepcopy(window.config)
    window.manager.save(original)
    requests = []
    window.config_changed.connect(requests.append)
    real_save = window.manager.save
    monkeypatch.setattr(window.manager, "save", lambda _: (_ for _ in ()).throw(OSError("Injected disk full")))
    in_modal(warning_close, lambda: choose(window.monitor_interval, 5))
    assert window.config == original
    assert window.monitor_interval.currentData() == original.monitor_interval
    assert not requests
    monkeypatch.setattr(window.manager, "save", real_save)
    choose(window.enforce_interval, 10)
    saved, _ = window.manager.load()
    assert saved.monitor_interval == original.monitor_interval
    assert saved.enforce_interval == 10 and len(requests) == 1


def test_settings_restore_all_busy_guard_and_ack(window):
    click(window.nav_buttons[3])
    requests = []
    window.restore_requested.connect(requests.append)
    click(window.restore_all_button)
    click(window.restore_all_button)
    assert requests == [None]
    assert window.pending_commands == 1 and not window.restore_all_button.isEnabled()
    assert not window.policy_page.apply_button.isEnabled()
    window.on_restore_done(True)
    assert window.pending_commands == 0 and window.restore_all_button.isEnabled()


def test_restore_all_failure_then_queued_exit_retry_waits_for_new_result(window, monkeypatch):
    click(window.nav_buttons[3])
    window.thread = SimpleNamespace()
    window.restore_count = 1
    requests, shutdown = [], []
    window.restore_requested.connect(requests.append)
    monkeypatch.setattr(window, "_shutdown", lambda: shutdown.append(True))
    click(window.restore_all_button)
    click(window.title_bar.close_button)
    assert window.close_after_command
    in_modal(lambda dialog: click(button(dialog, "恢复原设置并退出")), lambda: window.on_restore_done(False))
    assert requests == [None, None]
    assert window.pending_close and not shutdown
    assert not window.restore_all_button.isEnabled()
    window.on_restore_done(True)
    assert shutdown and not window.pending_close


def test_batch_apply_repeat_click_then_close_waits_for_ack(window):
    click(window.nav_buttons[1])
    page = window.policy_page
    requests = []
    window.apply_many_requested.connect(requests.append)
    click(page.preset_buttons["Strong"])
    click(page.apply_button)
    click(page.apply_button)
    assert len(requests) == 1 and window.pending_commands == 1
    assert not window.restore_all_button.isEnabled()
    window.thread = SimpleNamespace()
    click(window.title_bar.close_button)
    assert window.isVisible() and window.close_after_command
    window.thread = None
    window.on_command_done()
    assert not window.isVisible() and window.pending_commands == 0


@pytest.mark.parametrize("choice", ["cancel", "keep", "restore", "failed_restore"])
def test_close_confirmation_real_dialog(window, monkeypatch, choice):
    window.thread = SimpleNamespace()
    window.restore_count = 1
    shutdown = []
    requests = []
    monkeypatch.setattr(window, "_shutdown", lambda: shutdown.append(True))
    window.restore_requested.connect(requests.append)
    text = {"cancel": "取消", "keep": "保留当前设置并退出", "restore": "恢复原设置并退出",
            "failed_restore": "恢复原设置并退出"}[choice]
    in_modal(lambda dialog: click(button(dialog, text)), lambda: click(window.title_bar.close_button))
    if choice == "cancel":
        assert not shutdown and not requests and window.isVisible()
    elif choice == "keep":
        assert shutdown and not requests
    else:
        assert requests == [None] and window.pending_close and not shutdown
        if choice == "failed_restore":
            in_modal(warning_close, lambda: window.on_restore_done(False))
            assert not shutdown and not window.pending_close and window.isVisible()
        else:
            window.on_restore_done(True)
            assert shutdown


@pytest.mark.parametrize("engine", [None, SimpleNamespace(armed={}, restore=lambda _: (_ for _ in ()).throw(RuntimeError("injected")))])
def test_restore_worker_failure_always_completes(engine, monkeypatch):
    worker = MonitorWorker(AppConfig())
    worker.engine = engine
    monkeypatch.setattr(worker, "scan", lambda: None)
    completed = []
    worker.restore_done.connect(completed.append)
    worker.restore(None)
    assert completed == [False]


def test_export_actual_file_dialog_before_after_cancel_and_failure(window, tmp_path, monkeypatch):
    click(window.nav_buttons[2])
    assert not window.export_button.isEnabled()
    identity = ProcessIdentity(123, 100, "test.exe")
    window.on_snapshot([ProcessRow(identity.name, identity.pid, identity, Metrics(10, 1, 2, 3, sample_seconds=1))], set(), 0)
    window.on_applied(identity, 10.5, "test operation")
    window.on_snapshot([ProcessRow(identity.name, identity.pid, identity, Metrics(11, 4, 5, 6, sample_seconds=1))], set(), 0)
    # Force Qt's file dialog so it can be driven by Qt events on all runners.
    original = QFileDialog.getSaveFileName
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: original(*args, options=QFileDialog.Option.DontUseNativeDialog))
    output = tmp_path / "experiment.csv"
    def save(dialog):
        assert isinstance(dialog, QFileDialog)
        field = dialog.findChild(QLineEdit, "fileNameEdit")
        click(field)
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(field, str(output))
        QTest.keyClick(field, Qt.Key.Key_Return)
    in_modal(save, lambda: click(window.export_button))
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["phase"] for row in rows] == ["before", "after"]
    assert [row["cpu_percent_machine"] for row in rows] == ["1", "4"]
    original_bytes = output.read_bytes()
    in_modal(lambda dialog: QTest.keyClick(dialog, Qt.Key.Key_Escape), lambda: click(window.export_button))
    assert output.read_bytes() == original_bytes
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path), "CSV"))
    in_modal(warning_close, lambda: click(window.export_button))


def test_log_cap_clear_and_keyboard_caption(window):
    click(window.nav_buttons[3])
    for index in range(600):
        window.log.appendPlainText(f"line {index}")
    assert window.log.document().blockCount() == 500
    assert window.log.toPlainText().startswith("line 100")
    click(button(window.settings_page, "清空显示"))
    assert not window.log.toPlainText()
    window.title_bar.maximize_button.setFocus()
    QTest.keyClick(window.title_bar.maximize_button, Qt.Key.Key_Space)
    assert window.isMaximized()
    QTest.keyClick(window.title_bar.maximize_button, Qt.Key.Key_Space)
    assert not window.isMaximized()
