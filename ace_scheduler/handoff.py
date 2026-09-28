"""Two-phase whole-app handoff. Files carry UI state, never executable commands.

The source holds transfer.lock until completion. claim.lock serializes granting
ownership with cancellation; instance.lock still gates every monitor owner.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

import psutil
from PySide6.QtCore import QLockFile, QObject, QTimer, Slot

from ace_scheduler.config.models import AppConfig, ProcessRule
from ace_scheduler.windows.elevation import request_elevation


def atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix="handoff-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def own_identity():
    process = psutil.Process()
    return {"pid": process.pid, "created": process.create_time(), "user": process.username()}


def check_identity(identity):
    process = psutil.Process(identity["pid"])
    if abs(process.create_time() - identity["created"]) > .00001 or process.username() != identity["user"]:
        raise ValueError("接管进程身份已变化")
    if identity["user"] != psutil.Process().username():
        raise ValueError("接管只支持同一 Windows 用户；不能换用另一管理员账户")


class HandoffFiles:
    def __init__(self, directory, token):
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("接管令牌无效")
        self.directory, self.token = Path(directory), token

    def path(self, part):
        if part not in ("offer", "ready", "release", "claimed"):
            raise ValueError("接管消息类型无效")
        return self.directory / f"handoff-{self.token}-{part}.json"

    def write(self, part, data):
        atomic_json(self.path(part), data)

    def read(self, part):
        path = self.path(part)
        if path.stat().st_size > 1_000_000:
            raise ValueError("接管状态过大")
        return json.loads(path.read_text(encoding="utf-8"))

    def cleanup(self):
        for part in ("offer", "ready", "release", "claimed"):
            self.path(part).unlink(missing_ok=True)

    def offer(self):
        offer = self.read("offer")
        if offer["version"] != 1 or not 0 <= time.time() - offer["created"] < 120:
            raise ValueError("接管请求已过期或版本不支持")
        check_identity(offer["parent"])
        config = AppConfig.parse(offer["config"])
        raw = offer["drafts"]
        if not isinstance(raw, list) or len(raw) > 256:
            raise ValueError("接管草稿无效")
        drafts = [ProcessRule.parse(item) for item in raw]
        keys = {rule.key for rule in config.rules}
        if any(rule.key not in keys for rule in drafts) or len({r.key for r in drafts}) != len(drafts):
            raise ValueError("接管草稿规则无效")
        selected = offer["selected"]
        if not isinstance(selected, list) or any(key not in keys for key in selected):
            raise ValueError("接管选择无效")
        if type(offer["page"]) is not int or not 0 <= offer["page"] <= 4:
            raise ValueError("接管页面无效")
        return offer, config, {rule.key: rule for rule in drafts}


class ElevationHandoff(QObject):
    def __init__(self, window, lock, instance, launcher=request_elevation):
        super().__init__(window)
        self.window, self.lock, self.instance = window, lock, instance
        self.launcher = launcher
        self.directory = window.manager.path.parent
        self.transfer = QLockFile(str(self.directory / "transfer.lock"))
        self.transfer.setStaleLockTime(0)
        self.claim = QLockFile(str(self.directory / "claim.lock"))
        self.claim.setStaleLockTime(0)
        self.files = None
        self.released = False
        self.stopping = False
        self.failure_message = ""
        self.had_worker = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        window.worker_stopped.connect(self.release)

    def start(self):
        window = self.window
        if window.pending_commands or window.pending_close or window.shutting_down or window.handoff_waiting:
            return
        if not self.transfer.tryLock(0):
            window.banner.setText("另一个权限接管正在进行，请稍后再试。")
            return
        self.files = HandoffFiles(self.directory, secrets.token_hex(32))
        self.released = self.stopping = False
        self.failure_message = ""
        self.had_worker = bool(window.thread and window.thread.isRunning())
        window.handoff_waiting = True
        window.setEnabled(False)
        window.banner.setText("正在请求管理员权限；取消将保留此窗口和全部编辑。")
        try:
            config = window.config.to_dict()
            config["geometry"] = bytes(window.saveGeometry().toBase64()).decode("ascii")
            self.files.write("offer", {"version": 1, "created": time.time(), "parent": own_identity(),
                                      "config": config, "drafts": [asdict(rule) for rule in window.policy_page.drafts.values()],
                                      "selected": list(window.policy_page.selected), "page": window.pages.currentIndex()})
            self.launcher(["--handoff", self.files.token])
            self.deadline = time.monotonic() + 20
            self.timer.start()
        except Exception as exc:
            self.fail("提权取消或启动失败：" + str(exc))

    @Slot()
    def poll(self):
        try:
            if self.failure_message:
                self.fail(self.failure_message)
                return
            if self.files.path("claimed").exists():
                check_identity(self.files.read("claimed"))
                self.timer.stop()
                self.files.cleanup()
                self.transfer.unlock()
                self.window.handoff_waiting = False
                self.window.allow_close = True
                self.window.close()
                return
            if time.monotonic() > self.deadline:
                self.fail("提权接管超时；原窗口及草稿已保留。")
                return
            if not self.stopping and self.files.path("ready").exists():
                check_identity(self.files.read("ready"))
                self.stopping = True
                if self.window.thread and self.window.thread.isRunning():
                    self.window.suspend_requested.emit()
                else:
                    self.release()
        except Exception as exc:
            self.fail("提权接管失败：" + str(exc))

    @Slot()
    def release(self):
        if not self.window.handoff_waiting or self.released:
            return
        if self.failure_message:
            self.fail(self.failure_message)
            return
        try:
            if self.instance:
                self.instance.close()
            self.lock.unlock()
            self.released = True
            self.files.write("release", {"granted": True})
        except Exception as exc:
            self.fail("交接锁失败：" + str(exc))

    def fail(self, message):
        if self.stopping and self.window.thread and self.window.thread.isRunning():
            self.failure_message = message
            self.timer.start()
            return
        self.timer.stop()
        # The child holds this only around the final lock/claim transaction.
        if not self.claim.tryLock(1000):
            self.deadline = time.monotonic() + 2
            self.timer.start()
            return
        try:
            if self.files and self.files.path("claimed").exists():
                try:
                    check_identity(self.files.read("claimed"))
                except (OSError, ValueError, KeyError, TypeError, psutil.Error):
                    pass
                else:
                    self.failure_message = ""
                    self.timer.start()
                    return
            if self.files:
                self.files.cleanup()
            if self.released and not self.lock.tryLock(0):
                message += " 当前实例锁已由其他窗口持有；此窗口保留草稿供查看。"
                self.window.read_only = True
            elif self.released:
                from ace_scheduler.instance import InstanceServer
                self.instance = InstanceServer(self.directory, self)
                self.instance.activated.connect(self.window.activate_window)
            self.transfer.unlock()
            self.failure_message = ""
            self.window.handoff_waiting = False
            self.window.setEnabled(True)
            self.window.banner.setText(message)
            self.window.policy_page.refresh_status()
            if self.stopping and self.had_worker and not self.window.read_only:
                self.window.start_monitor()
        finally:
            self.claim.unlock()


def prepare_child(directory, token, lock, app, window_factory):
    """Prepare UI before asking the old process to relinquish its monitor/lock."""
    files = HandoffFiles(directory, token)
    offer, config, drafts = files.offer()
    window = window_factory(config)
    window.policy_page.drafts = drafts
    window.policy_page.selected = set(offer["selected"])
    window.policy_page.rebuild()
    window.show_page(offer["page"])
    files.write("ready", own_identity())
    claim = QLockFile(str(directory / "claim.lock"))
    claim.setStaleLockTime(0)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        # No worker exists until both permission and single-instance ownership are established.
        files.offer()
        if files.path("release").exists() and claim.tryLock(0):
            try:
                files.offer()
                if lock.tryLock(0):
                    try:
                        files.write("claimed", own_identity())
                        return window
                    except Exception:
                        lock.unlock()
                        raise
            finally:
                claim.unlock()
        app.processEvents()
        time.sleep(.02)
    window.close()
    raise OSError("原实例未交接锁；管理员实例未启动监控")
