from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ace_scheduler.branding import APP_NAME, app_icon


class TrayController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.icon = QSystemTrayIcon(app_icon(), self)
        self.menu = QMenu(window)
        self.status = self.menu.addAction("观察模式")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.open_action = self.menu.addAction("打开窗口", window.activate_window)
        self.stop_action = self.menu.addAction("停止全部规则（保留现值）", window.stop_all_rules)
        self.restore_action = self.menu.addAction("恢复全部原设置", window.request_restore_all)
        self.menu.addSeparator()
        self.exit_action = self.menu.addAction("真正退出…", window.request_exit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_available)
        self.timer.start(5000)
        self.update()

    def available(self):
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.window.activate_window()

    def check_available(self):
        if self.window.background_hidden and not self.available():
            self.window.activate_window()
            self.window.banner.setText("系统托盘暂不可用，已重新打开窗口。")

    def update(self, error=False):
        window = self.window
        status = "操作失败 · 请查看日志" if error else (
            "只读监控" if window.read_only else f"{len(window.armed)} 条策略已启用" if window.armed else "观察模式")
        self.status.setText(status)
        self.icon.setToolTip(APP_NAME + " · " + status)
        busy = bool(window.pending_commands or window.pending_close or window.shutting_down)
        self.stop_action.setEnabled(bool(window.armed) and not busy)
        self.restore_action.setEnabled(bool(window.topology) and not window.read_only and not busy)

    def close(self):
        self.timer.stop()
        self.icon.hide()
