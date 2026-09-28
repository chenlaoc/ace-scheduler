import json
import sys
import time
import zipfile

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ace_scheduler import __version__
from ace_scheduler.config.models import AppConfig, ProcessRule
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.core.process_monitor import MonitorWorker
from ace_scheduler.diagnostics import export_diagnostics
from ace_scheduler.errors import ExceptionReporter
from tests.test_monitor import setup
from tests.test_interactive_flows import window
from tests.test_ui import app


def test_bundle_is_bounded_and_contains_no_paths_names_ids_tokens(tmp_path):
    secret = 'C:\\Users\\private-user\\private-game.exe token=ghp_SUPER_SECRET PID=99999'
    (tmp_path / "scheduler.log").write_text((secret + " priority WinError 5 恢复失败\n") * 4000, encoding="utf-8")
    (tmp_path / "recovery.json").write_text("PRIVATE ORIGINALS")
    (tmp_path / "handoff-test.json").write_text("PRIVATE TOKEN")
    config = AppConfig([ProcessRule("private-game.exe")], geometry="PRIVATE_GEOMETRY")
    destination = tmp_path / "diagnostics.zip"
    export_diagnostics(destination, config, CpuTopology(4, 8, tuple(range(8))), secret, tmp_path)
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"report.json", "events.txt", "README.txt"}
        raw = b"\n".join(archive.read(name) for name in archive.namelist()).decode("utf-8")
        for value in ("private-user", "private-game", "ghp_SUPER_SECRET", "99999", "PRIVATE", "C:\\Users"):
            assert value not in raw
        report = json.loads(archive.read("report.json"))
        assert report["build"]["version"] == __version__
        assert report["topology"]["logical"] == 8
        assert "winerror=5" in archive.read("events.txt").decode()
        assert sum(info.file_size for info in archive.infolist()) < 120_000


def test_export_failure_keeps_existing_file(tmp_path, monkeypatch):
    import ace_scheduler.diagnostics as diagnostics
    target = tmp_path / "keep.zip"
    target.write_bytes(b"existing archive")
    monkeypatch.setattr(diagnostics.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(OSError):
        export_diagnostics(target, AppConfig(), None, "", tmp_path)
    assert target.read_bytes() == b"existing archive"
    assert not list(tmp_path.glob("ace-diagnostics-*.tmp"))


def test_export_cancel_and_unwritable_destination_are_reported(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: ("", ""))
    window.export_diagnostics()
    assert not list(tmp_path.glob("*.zip"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(tmp_path / "missing" / "data.zip"), ""))
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    window.export_diagnostics()
    assert errors


def test_worker_unexpected_failure_stops_both_timers_and_disarms_once(app, monkeypatch):
    engine, _, _ = setup(keep=True)
    engine.arm("test.exe")
    worker = MonitorWorker(AppConfig())
    worker.engine = engine
    worker.monitor_timer, worker.enforce_timer = QTimer(worker), QTimer(worker)
    worker.monitor_timer.start(1000)
    worker.enforce_timer.start(1000)
    messages, snapshots = [], []
    worker.failed.connect(messages.append)
    worker.snapshot.connect(lambda rows, armed, count: snapshots.append(armed))
    monkeypatch.setattr(engine, "scan", lambda: (_ for _ in ()).throw(RuntimeError("injected")))
    worker.scan()
    worker.scan()
    worker.enforce()
    assert len(messages) == 1 and not engine.armed and snapshots == [set()]
    assert not worker.monitor_timer.isActive() and not worker.enforce_timer.isActive()
    assert not engine.scheduler.api.writes


def test_gui_exception_is_visible_deduplicated_and_halts_worker(window):
    halted = []
    window.halt_requested.connect(halted.append)
    previous = sys.excepthook
    reporter = ExceptionReporter(window)
    try:
        for _ in range(2):
            reporter.handle(RuntimeError, RuntimeError("injected secret"), None)
        assert len(halted) == 1
        assert window.worker_failure and "后台已停止" in window.session_badge.text()
        assert not window.policy_page.apply_button.isEnabled()
        assert window.settings_page.restart_button.isEnabled()
    finally:
        reporter.close()
    assert sys.excepthook == previous


def test_about_is_readable_without_network_and_does_not_apply(window):
    applied = []
    window.apply_many_requested.connect(applied.append)
    window.show_page(4)
    assert __version__ in window.about_page.version.text()
    assert "PySide6" in window.about_page.notices.toPlainText()
    assert window.pages.currentIndex() == 4 and not applied


def test_worker_restart_returns_to_observation_without_losing_drafts(app, tmp_path):
    from ace_scheduler.config.config_manager import ConfigManager
    from ace_scheduler.ui.main_window import MainWindow
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), read_only=True)
    def until(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert predicate()
    try:
        until(lambda: window.topology is not None)
        window.policy_page.set_preset(("sguard64.exe",), "Strong")
        window.halt_requested.emit("injected failure")
        until(lambda: bool(window.worker_failure))
        old = window.thread
        window.restart_monitor()
        until(lambda: window.thread is not old and not window.worker_failure)
        assert not old.isRunning() and not window.armed
        assert window.policy_page.rule("sguard64.exe").policy.priority == "Idle"
    finally:
        window._shutdown()
        until(lambda: not window.thread.isRunning())


def test_failed_worker_still_allows_explicit_keep_and_exit(window, monkeypatch):
    from types import SimpleNamespace
    from tests.test_interactive_flows import in_modal, click, button
    window.thread = SimpleNamespace()
    window.restore_count = 1
    window.on_worker_failure("injected")
    shutdown = []
    monkeypatch.setattr(window, "_shutdown", lambda: shutdown.append(True))
    in_modal(lambda dialog: click(button(dialog, "保留当前设置并退出")), window.close)
    assert shutdown == [True]
