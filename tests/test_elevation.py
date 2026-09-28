import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from PySide6.QtCore import QLockFile

from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.config.models import AppConfig, ProcessRule, preset
from ace_scheduler.core.cpu_topology import CpuTopology
from ace_scheduler.handoff import ElevationHandoff, HandoffFiles, own_identity
from ace_scheduler.ui.main_window import MainWindow
from tests.test_ui import app


def make_window(tmp_path):
    window = MainWindow(AppConfig(), ConfigManager(tmp_path / "config.json"),
                        start_worker=False, write_enabled=False)
    window.on_ready(CpuTopology(4, 8, tuple(range(8))))
    window.policy_page.set_preset(("sguard64.exe",), "Strong")
    window.show_page(1)
    window.show()
    return window


def test_uac_cancel_keeps_original_draft_and_never_applies(app, tmp_path):
    window = make_window(tmp_path)
    lock = QLockFile(str(tmp_path / "instance.lock"))
    assert lock.tryLock(0)
    def cancel(args):
        raise OSError("cancelled")
    handoff = ElevationHandoff(window, lock, None, launcher=cancel)
    window.elevation_request = handoff.start
    before = copy.deepcopy(window.policy_page.drafts)
    applied = []
    window.apply_many_requested.connect(applied.append)
    window.policy_page.commit(True)
    assert not applied and not window.pending_commands
    assert window.isVisible() and window.isEnabled() and not window.handoff_waiting
    assert before == window.policy_page.drafts and window.config.rules[0].policy == preset("Default")
    assert "cancelled" in window.banner.text()
    assert not list(tmp_path.glob("handoff-*.json"))
    competitor = QLockFile(str(tmp_path / "instance.lock"))
    assert not competitor.tryLock(0)
    window.close()
    lock.unlock()


def test_timeout_preserves_source_and_revokes_late_child(app, tmp_path):
    window = make_window(tmp_path)
    lock = QLockFile(str(tmp_path / "instance.lock"))
    assert lock.tryLock(0)
    handoff = ElevationHandoff(window, lock, None, launcher=lambda _: None)
    handoff.start()
    files = handoff.files
    competitor = QLockFile(str(tmp_path / "transfer.lock"))
    assert not competitor.tryLock(0)
    handoff.deadline = 0
    handoff.poll()
    assert window.isEnabled() and window.isVisible() and window.policy_page.drafts
    with pytest.raises(OSError):
        files.offer()
    assert competitor.tryLock(0)
    competitor.unlock()
    window.close()
    lock.unlock()


def test_real_two_process_handoff_carries_drafts_and_never_arms(app, tmp_path):
    """Use a normal child to verify the protocol; deliberately never request real UAC."""
    window = make_window(tmp_path)
    lock = QLockFile(str(tmp_path / "instance.lock"))
    assert lock.tryLock(0)
    children = []
    output = tmp_path / "child-result.json"
    script = '''
import json, sys, time
from pathlib import Path
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication
from ace_scheduler.config.config_manager import ConfigManager
from ace_scheduler.ui.main_window import MainWindow
from ace_scheduler.handoff import prepare_child
app = QApplication([])
directory = Path(sys.argv[1])
lock = QLockFile(str(directory / "instance.lock"))
window = prepare_child(directory, sys.argv[2], lock, app,
    lambda config: MainWindow(config, ConfigManager(directory / "config.json"), start_worker=False))
result = {"draft": window.policy_page.rule("sguard64.exe").policy.priority,
          "saved": window.config.rules[0].policy.priority, "page": window.pages.currentIndex(),
          "armed": list(window.armed), "worker": window.thread is not None}
Path(sys.argv[3]).write_text(json.dumps(result))
# Keep the identified child alive until the parent consumes its acknowledgement.
deadline = time.monotonic() + 10
while list(directory.glob("handoff-*-claimed.json")) and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(.02)
window.close()
lock.unlock()
'''
    def launch(args):
        children.append(subprocess.Popen([sys.executable, "-c", script, str(tmp_path), args[1], str(output)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=subprocess.CREATE_NO_WINDOW))
    handoff = ElevationHandoff(window, lock, None, launcher=launch)
    try:
        handoff.start()
        deadline = time.monotonic() + 15
        while not window.allow_close and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        stdout, stderr = children[0].communicate(timeout=10)
        assert children[0].returncode == 0, (stdout, stderr)
        assert window.allow_close and not window.isVisible()
        data = json.loads(output.read_text())
        assert data == {"draft": "Idle", "saved": "Normal", "page": 1, "armed": [], "worker": False}
        assert not list(tmp_path.glob("handoff-*.json"))
    finally:
        handoff.timer.stop()
        lock.unlock()
        window.handoff_waiting = False
        window.close()


@pytest.mark.parametrize("change", ["expired", "identity", "version", "draft"])
def test_handoff_rejects_invalid_offer(tmp_path, change):
    files = HandoffFiles(tmp_path, "a" * 64)
    payload = {"version": 1, "created": time.time(), "parent": own_identity(), "config": AppConfig().to_dict(),
               "drafts": [], "selected": [], "page": 0}
    if change == "expired":
        payload["created"] -= 121
    elif change == "identity":
        payload["parent"]["created"] += 1
    elif change == "version":
        payload["version"] = 2
    else:
        payload["drafts"] = [asdict(ProcessRule("unknown.exe"))]
    files.write("offer", payload)
    with pytest.raises(ValueError):
        files.offer()


def test_admin_or_monitor_only_never_requests_uac(app, tmp_path):
    normal = make_window(tmp_path)
    requests = []
    normal.elevation_request = lambda: requests.append(True)
    normal.write_enabled = True
    assert normal.ensure_write_access() and not requests
    normal.read_only = True
    assert not normal.ensure_write_access() and not requests
    normal.close()


def test_failed_handoff_after_release_restarts_only_source_observer(app, tmp_path):
    window = make_window(tmp_path)
    window.start_monitor()
    lock = QLockFile(str(tmp_path / "instance.lock"))
    assert lock.tryLock(0)
    handoff = ElevationHandoff(window, lock, None, launcher=lambda _: None)
    original_thread = window.thread
    try:
        handoff.start()
        handoff.files.write("ready", own_identity())
        handoff.poll()
        deadline = time.monotonic() + 5
        while not handoff.released and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert handoff.released and not original_thread.isRunning()
        handoff.fail("injected child failure")
        assert window.thread is not original_thread
        assert window.isEnabled() and window.policy_page.drafts and not window.handoff_waiting
        assert not window.write_enabled and not window.armed
        competitor = QLockFile(str(tmp_path / "instance.lock"))
        assert not competitor.tryLock(0)
    finally:
        handoff.timer.stop()
        window.handoff_waiting = False
        window._shutdown()
        deadline = time.monotonic() + 5
        while window.thread.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert not window.thread.isRunning()
        if handoff.instance:
            handoff.instance.close()
        lock.unlock()


def test_ordinary_main_starts_without_uac_in_isolated_profile(tmp_path):
    script = '''
import json, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ace_scheduler.windows import elevation
elevation.is_admin = lambda: False
def forbidden(*args):
    raise AssertionError("Startup must never ask for UAC")
elevation.request_elevation = forbidden
original = QApplication.exec
def execute(app):
    def inspect():
        from ace_scheduler.ui.main_window import MainWindow
        window = next(w for w in app.topLevelWidgets() if isinstance(w, MainWindow))
        Path(output).write_text(json.dumps({"readonly": window.read_only,
            "writes": window.write_enabled, "armed": list(window.armed),
            "ready": window.topology is not None}))
        window.request_exit()
    QTimer.singleShot(700, inspect)
    QTimer.singleShot(8000, app.quit)
    return original()
QApplication.exec = execute
from ace_scheduler.main import main
output = sys.argv[1]
# main's parser must see ordinary startup arguments only.
sys.argv = ["ace_scheduler"]
raise SystemExit(main())
'''
    output = tmp_path / "normal.json"
    env = dict(os.environ, APPDATA=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", script, str(output)], env=env, capture_output=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(output.read_text()) == {"readonly": False, "writes": False, "armed": [], "ready": True}
