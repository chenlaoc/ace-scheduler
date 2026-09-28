import copy
import json
import subprocess
import sys
import time

from PySide6.QtCore import QLockFile
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSystemTrayIcon

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.instance import InstanceServer, activate_existing
from ace_scheduler.ui.main_window import MainWindow
from tests.test_ui import app


def activation_client(directory, timeout=2000):
    code = "from pathlib import Path; import sys; from PySide6.QtCore import QCoreApplication; from ace_scheduler.instance import activate_existing; app=QCoreApplication([]); sys.exit(0 if activate_existing(Path(sys.argv[1]), int(sys.argv[2])) else 2)"
    return subprocess.Popen([sys.executable, "-c", code, str(directory), str(timeout)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def test_same_user_activation_and_lock_still_exclusive(app, tmp_path):
    lock = QLockFile(str(tmp_path / "instance.lock"))
    second_lock = QLockFile(str(tmp_path / "instance.lock"))
    assert lock.tryLock(0) and not second_lock.tryLock(0)
    server = InstanceServer(tmp_path)
    activated, results = [], []
    server.activated.connect(lambda: activated.append(True))
    client = activation_client(tmp_path)
    try:
        while client.poll() is None:
            app.processEvents()
            time.sleep(.01)
        assert client.returncode == 0 and activated == [True], (client.communicate(), activated)
        assert not second_lock.tryLock(0)
    finally:
        server.close()
        lock.unlock()


def test_activation_rejects_wrong_token(app, tmp_path):
    server = InstanceServer(tmp_path)
    activated, results = [], []
    server.activated.connect(lambda: activated.append(True))
    data = json.loads(server.path.read_text())
    data["token"] = "0" * 64
    server.path.write_text(json.dumps(data))
    client = activation_client(tmp_path, 400)
    try:
        while client.poll() is None:
            app.processEvents()
            time.sleep(.01)
        assert client.returncode == 2 and not activated
    finally:
        server.close()


def test_server_close_releases_idle_connections_and_is_idempotent(app, tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtNetwork import QLocalSocket
    from shiboken6 import isValid
    server = InstanceServer(tmp_path)
    client = QLocalSocket()
    client.connectToServer(server.name)
    assert client.waitForConnected(500)
    deadline = time.monotonic() + 2
    while not server.connections and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert server.connections
    connection = next(iter(server.connections))
    server.close()
    server.close()
    assert not server.connections and not server.path.exists()
    assert not connection.timer.isActive()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(connection) and not isValid(server)
    server.close()
    client.abort()


def test_hidden_window_returns_and_tray_loss_has_visible_fallback(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    window = MainWindow(AppConfig(close_to_tray=True), ConfigManager(tmp_path / "config.json"),
                        start_worker=False, enable_tray=True)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    window.show()
    window.close()
    assert window.background_hidden and not window.isVisible()
    window.activate_window()
    assert window.isVisible() and not window.background_hidden
    window.close()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    window.tray.check_available()
    assert window.isVisible() and "托盘" in window.banner.text()
    window.request_exit()
    assert not window.isVisible() and not window.tray.icon.isVisible()


def test_tray_stop_queues_command_without_restore_and_restore_queues_separately(app, tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"), start_worker=False)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    stopped, restored = [], []
    window.stop_many_requested.connect(stopped.append)
    window.restore_requested.connect(restored.append)
    window.armed = {"sguard64.exe"}
    window.stop_all_rules()
    assert stopped == [("sguard64.exe",)] and not restored
    window.on_command_done()
    window.request_restore_all()
    assert restored == [None]
    window.on_restore_done(True)
    window.close()


def test_close_preference_default_and_roundtrip():
    assert not AppConfig.parse({}).close_to_tray
    assert AppConfig.parse(AppConfig(close_to_tray=True).to_dict()).close_to_tray


def test_readonly_worker_never_restores_or_applies_saved_records(tmp_path):
    from ace_scheduler.core.process_monitor import MonitorWorker, MonitorEngine
    from ace_scheduler.core.scheduler import Scheduler
    from ace_scheduler.core.process_metrics import ProcessIdentity
    from ace_scheduler.config.models import preset
    from tests.fakes import FakeApi
    scheduler = Scheduler(FakeApi(), CpuTopology(4, 8, tuple(range(8))))
    key = ProcessIdentity(1, 1, "test.exe")
    scheduler.apply(key, preset("Strong"))
    saved = copy.deepcopy(scheduler.originals)
    count = len(scheduler.api.writes)
    worker = MonitorWorker(AppConfig(), read_only=True)
    worker.engine = MonitorEngine(scheduler, AppConfig())
    worker.restore(None)
    worker.force_restore()
    worker.apply_rules(("test.exe",))
    worker.abandon_recovery()
    assert scheduler.originals == saved and len(scheduler.api.writes) == count


def test_ordinary_worker_can_abandon_records_but_cannot_write_processes(tmp_path):
    from ace_scheduler.core.process_monitor import MonitorWorker, MonitorEngine
    from ace_scheduler.core.recovery import RecoveryJournal
    from ace_scheduler.core.scheduler import Scheduler
    from ace_scheduler.core.process_metrics import ProcessIdentity
    from ace_scheduler.config.models import preset
    from tests.fakes import FakeApi
    scheduler = Scheduler(FakeApi(), CpuTopology(4, 8, tuple(range(8))), RecoveryJournal(tmp_path / "recovery.json"))
    key = ProcessIdentity(1, 1, "test.exe")
    scheduler.apply(key, preset("Strong"))
    count = len(scheduler.api.writes)
    worker = MonitorWorker(AppConfig(), write_enabled=False)
    worker.engine = MonitorEngine(scheduler, AppConfig())
    worker.restore(None)
    worker.apply_rules(("test.exe",))
    assert scheduler.originals and len(scheduler.api.writes) == count
    worker.abandon_recovery()
    assert not scheduler.originals and len(scheduler.api.writes) == count
