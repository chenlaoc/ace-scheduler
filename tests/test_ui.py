import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, AffinitySpec
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def test_dynamic_ui_semantic_preset_and_empty_selection(app, tmp_path, monkeypatch):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.on_ready(CpuTopology(12, 24, tuple(range(24))))
    page = window.policy_page
    requests = []
    window.apply_many_requested.connect(requests.append)
    page.select_rules("none")
    page.controls["sguard64.exe"].select.click()
    page.preset_buttons["Strong"].click()
    assert page.rule("sguard64.exe").policy.affinity.mode == "last_n"
    page.commit(True)
    assert requests == [("sguard64.exe",)]
    assert window.config.rules[0].policy.affinity.cpus == ()
    window.on_command_done()
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    page.edit_policy("sguard64.exe", affinity=AffinitySpec("custom"))
    page.commit(True)
    assert errors and len(requests) == 1
    window.close()


def test_readonly_and_processor_group_warning(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), read_only=True, start_worker=False)
    window.on_ready(CpuTopology(None, 128, (), 2))
    assert not window.policy_page.apply_button.isEnabled()
    assert "仅支持单 Processor Group" in window.banner.text()
    # The editor remains accessible to select "unchanged" on unsupported machines.
    assert window.policy_page.cpu_button.isEnabled()
    assert all(row.cpu.isEnabled() for row in window.policy_page.controls.values())
    window.close()


@pytest.mark.parametrize("scenario", ["pending", "partial"])
def test_pending_and_partial_status_are_visible_in_process_table(app, tmp_path, monkeypatch, scenario):
    from ace_scheduler.core.recovery import RecoveryJournal
    from ace_scheduler.ui.process_table import ProcessTable
    from tests.test_monitor import setup
    engine, _, clock = setup(keep=True)
    if scenario == "pending":
        engine.scheduler.journal = RecoveryJournal(tmp_path / "recovery.json")
        def fail(*args):
            raise OSError("confirm failed")
        monkeypatch.setattr(engine.scheduler.journal, "confirm", fail)
    else:
        engine.scheduler.topology = CpuTopology(None, 128, (), 2)
    engine.arm("test.exe")
    table = ProcessTable()
    try:
        for _ in range(2):
            table.update_rows(engine.scan())
            status = table.item(0, 11).text()
            assert ("待确认" if scenario == "pending" else "部分成功") in status
            assert "已验证" not in status
            assert status in table.item(0, 11).toolTip()
            clock[0] += 60
            engine.enforce()
    finally:
        table.deleteLater()


def test_worker_start_and_asynchronous_clean_shutdown(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), read_only=True)
    loop = QEventLoop()
    window.worker.ready.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert window.topology is not None
    assert not window.armed
    loop = QEventLoop()
    window.thread.finished.connect(loop.quit)
    window.close()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert not window.thread.isRunning()
    assert window.allow_close


def test_close_waits_for_pending_apply_acknowledgement(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtGui import QCloseEvent
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.thread = SimpleNamespace()
    window.pending_commands = 1
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.close_after_command and not window.shutting_down
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    window.on_command_done()
    assert closed and window.pending_commands == 0
    window.thread = None


def test_navigation_and_preview_never_apply_until_requested(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.on_ready(CpuTopology(8, 16, tuple(range(16))))
    requests = []
    window.apply_many_requested.connect(requests.append)
    window.show()
    for index in (1, 2, 3, 0, 1):
        window.nav_buttons[index].click()
        app.processEvents()
        assert window.pages.currentIndex() == index
        assert window.policy_page.footer.isVisible() == (index == 1)
        assert window.process_picker.isVisible() == (index == 0)
    window.policy_page.preset_buttons["Strong"].click()
    window.on_snapshot([], set(), 0)
    assert requests == []
    assert window.policy_page.drafts and "待保存 5" in window.policy_page.summary.text()
    assert all(rule.policy.affinity.mode == "last_n" for rule in window.policy_page.drafts.values())
    window.policy_page.apply_button.click()
    assert requests == [tuple(rule.key for rule in window.config.rules)]
    assert not window.policy_page.drafts
    assert "正在应用" in window.policy_page.summary.text()
    window.close()


def test_observed_instance_survives_refresh_and_is_shared_across_pages(app, tmp_path):
    from ace_scheduler.core.process_metrics import Metrics, ProcessIdentity
    from ace_scheduler.core.process_monitor import ProcessRow
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    first = ProcessIdentity(10, 100, "SGuard64.exe")
    second = ProcessIdentity(20, 200, "SGuard64.exe")
    row_a = ProcessRow(first.name, first.pid, first, Metrics(1, 1, 2, 3))
    row_b = ProcessRow(second.name, second.pid, second, Metrics(1, 4, 5, 6))
    window.on_snapshot([row_a, row_b], set(), 0)
    window.table.selectRow(1)
    assert window.selected_identity == second
    assert window.process_picker.currentData() == second
    window.show_page(2)
    window.on_snapshot([row_b, row_a], set(), 0)
    assert window.selected_identity == second
    assert window.table.selected_identity() == second
    assert window.process_picker.currentData() == second
    assert window.overview_page.metrics["cpu_percent"].value_label.text() == "4.00"
    window.process_picker.setCurrentIndex(1)
    assert window.selected_identity == first
    assert window.table.selected_identity() == first
    window.on_snapshot([row_b], set(), 0)
    assert window.selected_identity == second
    window.on_snapshot([], set(), 0)
    assert window.selected_identity is None
    assert not window.export_button.isEnabled()
    assert window.overview_page.metrics["cpu_percent"].value_label.text() == "—"
    window.close()


def test_small_window_keeps_actions_and_dynamic_cpu_selection_reachable(app, tmp_path):
    from PySide6.QtCore import QPoint
    from ace_scheduler.ui.affinity_dialog import AffinityDialog
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.on_ready(CpuTopology(32, 64, tuple(range(64))))
    window.resize(1040, 700)
    window.show_page(1)
    window.show()
    app.processEvents()
    page = window.policy_page
    footer = page.footer
    assert footer.isVisible()
    assert window.rect().contains(footer.mapTo(window, footer.rect().bottomRight()))
    assert page.apply_button.isVisible()
    assert page.table.horizontalScrollBar().maximum() == 0
    page.preset_buttons["Strong"].click()
    dialog = AffinityDialog(window.topology, [page.rule("sguard64.exe")], page)
    dialog.show()
    app.processEvents()
    chip = dialog.editor.checks[63]
    assert chip.isChecked()
    dialog.editor.area.ensureWidgetVisible(chip)
    app.processEvents()
    assert dialog.editor.area.viewport().rect().contains(chip.mapTo(dialog.editor.area.viewport(), QPoint(10, 10)))
    chip.click()
    with pytest.raises(ValueError):
        dialog.editor.value()
    dialog.close()
    window.show_page(0)
    app.processEvents()
    assert window.overview_page.metric_columns == 2
    assert window.table.horizontalScrollBar().maximum() == 0
    window.resize(1280, 860)
    app.processEvents()
    assert window.overview_page.metric_columns == 4
    window.close()
