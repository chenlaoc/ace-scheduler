"""Asynchronous helper supervision. Never runs PDH in the GUI/maintenance thread."""
import json
from pathlib import Path
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


class DiskCapture(QObject):
    packet = Signal(object)
    stopped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)
        self.folder = None
        self.active = False
        self.sequence = -1
        self.last_received = 0

    def start(self):
        if self.active or self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.folder = tempfile.TemporaryDirectory(prefix="ace-disk-mailbox-")
        self.path = Path(self.folder.name) / "snapshot.json"
        self.sequence, self.last_received, self.active = -1, time.monotonic(), True
        args = ["--disk-helper", str(self.path)]
        if not getattr(sys, "frozen", False):
            args = ["-m", "ace_scheduler", *args]
        self.process.start(sys.executable, args)
        self.timer.start()

    def poll(self):
        if not self.active:
            return
        try:
            if self.path.exists():
                if self.path.stat().st_size > 262144:
                    raise ValueError("磁盘采集快照超过容量")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("error"):
                    raise ValueError(data["error"])
                if data["sequence"] != self.sequence:
                    if self.sequence >= 0 and data["sequence"] != self.sequence + 1:
                        for row in data["rows"]:
                            row["seconds"], row["gap"] = None, "mailbox_gap"
                            row["values"] = dict.fromkeys(row["values"])
                    self.sequence = data["sequence"]
                    self.last_received = time.monotonic()
                    self.packet.emit(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.stop("磁盘采集读取失败：" + str(exc))
            return
        if time.monotonic() - self.last_received > 8:
            self.stop("磁盘采集超时；已停止采集，缺失区间保留")

    def stop(self, reason="已停止磁盘采集"):
        if not self.active:
            return
        self.active = False
        self.timer.stop()
        self.process.kill()  # This is only the helper created by this object.
        self.stopped.emit(reason)

    def _error(self, error):
        if self.active:
            self.stop("磁盘采集程序不可用：" + self.process.errorString())
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._cleanup()

    def _finished(self, *args):
        if self.active:
            reason = "磁盘采集程序已退出；请重新启用"
            try:
                if self.path.exists() and self.path.stat().st_size <= 262144:
                    reason = json.loads(self.path.read_text(encoding="utf-8")).get("error") or reason
            except (OSError, ValueError):
                pass
            self.stop(reason)
        self._cleanup()

    def _cleanup(self):
        if self.folder:
            self.folder.cleanup()
            self.folder = None

    def close(self):
        self.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.waitForFinished(1000)
        self._cleanup()
