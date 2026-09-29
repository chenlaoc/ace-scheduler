from __future__ import annotations

import copy
from dataclasses import asdict
import time
import sqlite3

from PySide6.QtCore import QByteArray, QThread, Qt, Signal, Slot, QSize, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from ace_scheduler.config.models import ECO_MODES, PRIORITIES
from ace_scheduler.branding import APP_NAME, app_icon
from ace_scheduler.core.experiment import ACTIVE, STATE_LABELS, History, summary, save_session, export_csv
from ace_scheduler.core.process_monitor import MonitorWorker
from ace_scheduler.core.clock import ClockTracker
from .disk_capture import DiskCapture
from .frame_import import FrameImportDialog, describe_frames
from .repeated_experiments import RepeatedExperimentsDialog
from ace_scheduler.core.repeated_experiments import RepeatedStudy
from .process_table import number
from .components import Backdrop, icon, label
from .theme import apply_palette
from .window_chrome import FramelessMainWindow, TitleBar
from .pages.overview import OverviewPage
from .pages.policy import PolicyPage
from .pages.experiment import ExperimentPage
from .pages.settings import SettingsPage
from .pages.about import AboutPage


class MainWindow(FramelessMainWindow):
    config_changed = Signal(object)
    apply_many_requested = Signal(object)
    stop_many_requested = Signal(object)
    restore_many_requested = Signal(object)
    restore_requested = Signal(object)
    stop_requested = Signal()
    force_restore_requested = Signal()
    abandon_requested = Signal()
    suspend_requested = Signal()
    worker_stopped = Signal()
    halt_requested = Signal(str)

    def __init__(self, config, manager, read_only=False, start_worker=True, enable_tray=False, write_enabled=True):
        super().__init__()
        self.config = config
        self.theme_controller = apply_palette(QApplication.instance(), config.theme)
        self.manager = manager
        self.read_only = read_only
        self.write_enabled = write_enabled and not read_only
        self.elevation_request = None
        self.handoff_waiting = False
        self.worker_failure = ""
        self.restarting = False
        self.topology = None
        self.history = History()
        self.restore_count = 0
        self.armed = set()
        self.selected_identity = None
        self.selected_experiment = None
        self.experiment_identity = None
        self.thread = None
        self.pending_close = False
        self.pending_restore = False
        self.pending_commands = 0
        self.close_after_command = False
        self.shutting_down = False
        self.allow_close = False
        self.close_dialog_active = False
        self.background_hidden = False
        self.explicit_exit = False
        self.latest_snapshot = None
        self.tray = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1040, 680)
        self._build()
        self.disk_capture = DiskCapture(self)
        self.disk_capture.packet.connect(self.on_disk_packet)
        self.disk_capture.stopped.connect(self.on_disk_stopped)
        self.clock_tracker = ClockTracker()
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self.record_clock)
        self.theme_controller.changed.connect(self.refresh_theme_icons)
        self.refresh_theme_icons(self.theme_controller.effective)
        if enable_tray:
            from .tray import TrayController
            self.tray = TrayController(self)
        if config.geometry:
            self.restoreGeometry(QByteArray.fromBase64(config.geometry.encode("ascii", errors="ignore")))
        if start_worker:
            self.start_monitor()

    def start_monitor(self):
        self.record_clock()
        self.clock_timer.start()
        self.worker_failure = ""
        self.thread = QThread(self)
        self.worker = MonitorWorker(copy.deepcopy(self.config), self.manager.path.with_name("recovery.json"),
                                    self.read_only, self.write_enabled)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.ready.connect(self.on_ready)
        self.worker.snapshot.connect(self.on_snapshot)
        self.worker.log_line.connect(self.log.appendPlainText)
        self.worker.recorded.connect(self.on_record)
        self.worker.restore_done.connect(self.on_restore_done)
        self.worker.command_done.connect(self.on_command_done)
        self.config_changed.connect(self.worker.configure)
        self.apply_many_requested.connect(self.worker.apply_rules)
        self.stop_many_requested.connect(self.worker.stop_rules)
        self.restore_many_requested.connect(self.worker.restore_rules)
        self.restore_requested.connect(self.worker.restore)
        self.stop_requested.connect(self.worker.finish_session)
        self.suspend_requested.connect(self.worker.stop)
        self.force_restore_requested.connect(self.worker.force_restore)
        self.abandon_requested.connect(self.worker.abandon_recovery)
        self.worker.recovery_notice.connect(self.on_recovery_notice)
        self.worker.finish_failed.connect(self.on_finish_failed)
        self.worker.failed.connect(self.on_worker_failure)
        self.halt_requested.connect(self.worker.halt)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    def _build(self):
        root = Backdrop()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(18, 18, 22, 18)
        shell.setSpacing(26)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(202)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 25, 14, 22)
        side.setSpacing(9)
        identity = QHBoxLayout()
        identity.setSpacing(9)
        mark = label("")
        mark.setPixmap(app_icon().pixmap(QSize(52, 52)))
        mark.setFixedSize(52, 52)
        mark.setAccessibleName("ACE Scheduler Logo")
        identity.addWidget(mark)
        wordmark = QVBoxLayout()
        wordmark.setSpacing(0)
        brand = label("ACE", "pageTitle")
        brand.setStyleSheet("font-size: 29px; color: #2675c8;")
        wordmark.addWidget(brand)
        wordmark.addWidget(label("SCHEDULER", "eyebrow"))
        identity.addLayout(wordmark)
        identity.addStretch()
        side.addLayout(identity)
        side.addSpacing(30)
        side.addWidget(label("工作空间", "muted"))
        self.nav_buttons = []
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (text, symbol) in enumerate((("运行概览", "overview"), ("调度策略", "policy"),
                                                ("实验对照", "experiment"), ("设置与日志", "settings"), ("关于", "settings"))):
            button = QPushButton("  " + text)
            button.setIcon(icon(symbol))
            button.setIconSize(QSize(21, 21))
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self.show_page(page))
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addStretch()
        side.addWidget(label("本机设备", "eyebrow"))
        self.cpu_label = label("正在检测 CPU…", "body", True)
        side.addWidget(self.cpu_label)
        self.cpu_counts = label("—", "muted", True)
        side.addWidget(self.cpu_counts)
        side.addSpacing(16)
        self.session_badge = label("●  只读监控" if self.read_only else "●  观察模式", "badge")
        side.addWidget(self.session_badge)
        side.addWidget(label("只管理用户态调度", "muted"))
        shell.addWidget(sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 6, 0, 0)
        content.setSpacing(14)
        self.title_bar = TitleBar(self)
        header = self.title_bar.row
        text = QVBoxLayout()
        text.setSpacing(5)
        self.page_title = label("运行概览", "pageTitle")
        self.page_description = label("查看进程的资源占用和当前调度设置。", "muted")
        text.addWidget(self.page_title)
        text.addWidget(self.page_description)
        header.addLayout(text, 1)
        self.process_picker = QComboBox()
        self.process_picker.setMinimumWidth(230)
        self.process_picker.setMaximumWidth(320)
        self.process_picker.addItem("等待发现进程", None)
        self.process_picker.setAccessibleName("选择观察的进程实例")
        self.process_picker.currentIndexChanged.connect(self.select_picker_process)
        header.addWidget(self.process_picker)
        self.title_bar.finish()
        content.addWidget(self.title_bar)
        self.banner = label("只读模式 · 调度操作已禁用" if self.read_only else "正在观察，点击应用后才会调整设置。", "muted", True)
        self.banner.setObjectName("banner")
        content.addWidget(self.banner)
        self.pages = QStackedWidget()
        self.overview_page = OverviewPage(self)
        self.policy_page = PolicyPage(self)
        self.experiment_page = ExperimentPage(self)
        self.settings_page = SettingsPage(self)
        self.about_page = AboutPage(self)
        for page in (self.overview_page, self.policy_page, self.experiment_page, self.settings_page, self.about_page):
            self.pages.addWidget(page)
        content.addWidget(self.pages, 1)
        content.addWidget(self.policy_page.footer)
        shell.addLayout(content, 1)
        self.statusBar().hide()
        self.show_page(0)

    def show_page(self, index):
        titles = (("运行概览", "查看进程的资源占用和当前调度设置。"),
                  ("调度策略", "单独修改每行参数，也可以勾选多行一起设置。"),
                  ("实验对照", "比较应用策略前后的 CPU 占用和进程 I/O。"),
                  ("设置与日志", "设置采样间隔、处理恢复记录，或查看操作日志。"),
                  ("关于", "版本信息、本地数据与第三方许可。"))
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.page_title.setText(titles[index][0])
        self.page_description.setText(titles[index][1])
        self.process_picker.setVisible(index == 0)
        self.policy_page.footer.setVisible(index == 1)

    def select_table_process(self):
        identity = self.table.selected_identity()
        if identity is None:
            return
        self.selected_identity = identity
        index = self.process_picker.findData(identity)
        if index >= 0:
            self.process_picker.blockSignals(True)
            self.process_picker.setCurrentIndex(index)
            self.process_picker.blockSignals(False)
        self.refresh_history()

    def select_picker_process(self, index):
        self.selected_identity = self.process_picker.itemData(index)
        if hasattr(self, "table"):
            self.table.select_identity(self.selected_identity)
            self.refresh_history()

    @Slot(object)
    def on_ready(self, topology):
        if hasattr(self, "exception_reporter"):
            self.exception_reporter.reported = False
        self.topology = topology
        physical = topology.physical if topology.physical is not None else "未知"
        self.cpu_label.setText(topology.name)
        self.cpu_counts.setText(f"{physical} 核心 / {topology.logical} 逻辑 CPU\n{topology.groups} Processor Group")
        if topology.limitation:
            self.banner.setText(self.banner.text() + "\n" + topology.limitation)
        self.policy_page.on_ready()

    def persist(self, candidate=None):
        candidate = candidate if candidate is not None else self.config
        try:
            self.manager.save(candidate)
            self.config = candidate
            self.config_changed.emit(copy.deepcopy(candidate))
            return True
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False

    def intervals_changed(self, *_):
        candidate = copy.deepcopy(self.config)
        candidate.monitor_interval = self.monitor_interval.currentData()
        candidate.enforce_interval = self.enforce_interval.currentData()
        if not self.persist(candidate):
            self.monitor_interval.setCurrentIndex(self.monitor_interval.findData(self.config.monitor_interval))
            self.enforce_interval.setCurrentIndex(self.enforce_interval.findData(self.config.enforce_interval))

    def close_preference_changed(self, *_):
        candidate = copy.deepcopy(self.config)
        candidate.close_to_tray = self.settings_page.close_behavior.currentData()
        self.persist(candidate)
        self.settings_page.close_behavior.setCurrentIndex(int(self.config.close_to_tray))

    def theme_preference_changed(self, *_):
        candidate = copy.deepcopy(self.config)
        candidate.theme = self.settings_page.theme_choice.currentData()
        if self.persist(candidate):
            self.theme_controller.set_mode(candidate.theme)
        self.settings_page.theme_choice.setCurrentIndex(
            self.settings_page.theme_choice.findData(self.config.theme))

    @Slot(str)
    def refresh_theme_icons(self, effective):
        for button, symbol in zip(self.nav_buttons, ("overview", "policy", "experiment", "settings", "settings")):
            button.setIcon(icon(symbol, "#a9c3e2" if effective == "dark" else "#66809e"))

    def activate_window(self):
        self.background_hidden = False
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        if self.latest_snapshot:
            self._render_snapshot(*self.latest_snapshot)

    def request_exit(self):
        self.explicit_exit = True
        self.activate_window()
        self.close()

    def stop_all_rules(self):
        if self.armed:
            self.policy_page.run_command("stop", tuple(self.armed))

    def ensure_write_access(self):
        if self.read_only or self.handoff_waiting or self.worker_failure or self.restarting:
            return False
        if self.write_enabled:
            return True
        if self.elevation_request:
            self.elevation_request()
        else:
            self.banner.setText("修改调度设置需要管理员权限，请以管理员身份重新打开。")
        return False

    @Slot(str)
    def on_worker_failure(self, message):
        self.worker_failure = message
        self.armed.clear()
        self.pending_commands = 0
        self.pending_restore = self.pending_close = self.close_after_command = False
        self.activate_window()
        self.banner.setText(message + "。恢复记录已保留，请到“设置与日志”重启后台后再试。")
        self.session_badge.setText("●  后台已停止")
        self.policy_page.refresh_status()
        if self.tray:
            self.tray.update(True)

    def restart_monitor(self):
        if not self.worker_failure or self.restarting or self.handoff_waiting or self.shutting_down:
            return
        self.restarting = True
        self.banner.setText("正在重启后台，暂不应用策略…")
        if self.thread and self.thread.isRunning():
            self.suspend_requested.emit()
        else:
            self._thread_finished()

    def export_diagnostics(self):
        if QMessageBox.question(self, "导出诊断", "诊断 ZIP 会包含程序版本和构建号、Windows 版本、CPU 拓扑数量、去掉进程名称的配置摘要，以及脱敏后的事件日志。\n\n不会包含原始配置、恢复记录、个人路径、PID 或令牌。文件只保存到你选择的位置，不会上传。") != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存诊断 ZIP", "ACE-Scheduler-diagnostics.zip", "ZIP (*.zip)")
        if not path:
            return
        from ace_scheduler.diagnostics import export_diagnostics
        try:
            export_diagnostics(path, self.config, self.topology, self.log.toPlainText(), self.manager.path.parent,
                               read_only=self.read_only, worker_failed=self.worker_failure)
            self.banner.setText("诊断 ZIP 已保存，没有上传。")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "诊断导出失败", str(exc))

    def request_restore_all(self):
        if self.read_only or not self.topology or self.pending_commands or self.pending_close or self.shutting_down:
            return
        if not self.ensure_write_access():
            return
        self.pending_restore = True
        self.pending_commands += 1
        self.policy_page.message = "正在恢复全部原设置…"
        self.policy_page.refresh_status()
        self.restore_requested.emit(None)

    @Slot(str)
    def on_recovery_notice(self, message):
        self.banner.setText(message)
        self.settings_page.recovery_status.setText(message)

    @Slot(str)
    def on_finish_failed(self, message):
        self.shutting_down = False
        self.setEnabled(True)
        self.banner.setText(message)
        self.policy_page.refresh_status()
        QMessageBox.warning(self, "退出未完成", message)

    def resolve_recovery(self, force=False):
        if self.read_only or not self.topology or self.pending_commands or self.pending_close or self.shutting_down:
            return
        if force and not self.ensure_write_access():
            return
        message = ("将为记录中的原进程恢复修改前的设置。这会覆盖其他程序改过的值，也会恢复上次写入未确认的项目。\n\n确定覆盖并恢复？" if force else
                   "将停止全部规则，保留进程当前的设置。所有恢复记录（包括损坏的记录）都会归档，之后无法再用它们恢复。\n\n确定放弃这些记录？")
        if QMessageBox.question(self, "处理恢复记录", message) != QMessageBox.StandardButton.Yes:
            return
        self.pending_commands += 1
        self.policy_page.refresh_status()
        if force:
            self.pending_restore = True
            self.force_restore_requested.emit()
        else:
            self.abandon_requested.emit()

    @Slot(object, object, int)
    def on_snapshot(self, rows, armed, originals):
        self.armed, self.restore_count = armed, originals
        identities = {row.identity for row in rows if row.identity}
        for row in rows:
            if row.identity and row.metrics:
                self.history.add(row.identity, row.metrics, asdict(row.state))
        # Bound history for exited processes: keep only the most recent 16 instances.
        all_ids = list(self.history.samples)
        keep = identities | set(all_ids[-16:])
        self.history.retain(keep)
        self.latest_snapshot = (rows, armed, originals)
        if self.tray:
            self.tray.update(bool(self.worker_failure) or any("失败" in row.status or "冲突" in row.status for row in rows))
        if self.background_hidden:
            return
        self._render_snapshot(rows, armed, originals)

    def _render_snapshot(self, rows, armed, originals):
        self.table.update_rows(rows)
        valid = [row.identity for row in rows if row.identity]
        if self.selected_identity not in valid:
            self.selected_identity = valid[0] if valid else None
        self.process_picker.blockSignals(True)
        self.process_picker.clear()
        for identity in valid:
            self.process_picker.addItem(f"{identity.name}  ·  {identity.pid}", identity)
        if not valid:
            self.process_picker.addItem("等待发现进程", None)
        else:
            self.process_picker.setCurrentIndex(valid.index(self.selected_identity))
        self.process_picker.blockSignals(False)
        self.table.select_identity(self.selected_identity)
        self.table.setVisible(bool(rows))
        self.overview_page.empty.setVisible(not rows)
        self.overview_page.count.setText(f"{len(rows)} 个运行中")
        self.session_badge.setText("●  只读监控" if self.read_only else (f"●  {len(armed)} 条策略已启用" if armed else "●  观察模式"))
        if self.worker_failure:
            self.session_badge.setText("●  后台已停止")
        self.refresh_policy_status()
        self.refresh_history()

    def refresh_policy_status(self):
        self.policy_page.refresh_status()

    @Slot(object, float, str)
    def on_applied(self, identity, timestamp, status):
        session = self.history.mark(identity, timestamp, status)
        if session:
            self.selected_experiment = session.id
        self.refresh_history()

    @Slot(object)
    def on_record(self, event):
        session = self.history.consume(event)
        if session and self.selected_experiment is None:
            self.selected_experiment = session.id
        if not self.background_hidden and event["kind"] not in ("sample", "tick"):
            self.refresh_experiment()

    def refresh_history(self):
        identity = self.selected_identity
        row = next((row for row in self.table.rows if row.identity == identity), None) if identity else None
        metrics = row.metrics if row else None
        notes = {"cpu_percent": "占整机 CPU 的比例", "read_mbps": "进程级读取", "write_mbps": "进程级写入", "ram_mb": "当前工作集"}
        for field, card in self.overview_page.metrics.items():
            card.set_value(number(getattr(metrics, field, None), 1 if field == "ram_mb" else 2),
                           notes[field] if identity else "等待进程数据")
        values = self.overview_page.details
        state = row.state if row else None
        priority_names = {v: k for k, v in PRIORITIES.items()}
        values["priority"].setText(priority_names.get(state.priority, "未知") if state else "—")
        affinity = state.affinity if state else None
        values["affinity"].setText(f"{len(affinity)} 个 CPU" if affinity else "—")
        values["affinity"].setToolTip(", ".join(map(str, affinity)) if affinity else "")
        values["eco"].setText(state.eco.label if state and state.eco else "未知" if identity else "—")
        for field in ("total_read_gb", "total_write_gb"):
            value = getattr(metrics, field, None)
            values[field].setText(number(value) + " GB" if value is not None else "—")
        samples = list(self.history.samples.get(identity, []))
        experiment = self.history.experiments.get(identity)
        self.chart.set_data(samples, experiment.marker if experiment else None)
        self.refresh_experiment()

    def current_experiment(self):
        return self.history.sessions.get(self.selected_experiment)

    def select_experiment(self, index):
        value = self.experiment_page.picker.itemData(index)
        self.selected_experiment = value[1] if value and value[0] == "session" else None
        self.experiment_identity = value[1] if value and value[0] == "instance" else None
        self.refresh_experiment()

    def refresh_experiment(self, *_):
        if not hasattr(self, "experiment_page"):
            return
        page = self.experiment_page
        live = {r.identity for r in (self.latest_snapshot[0] if self.latest_snapshot else []) if r.identity}
        mode = page.filter.currentData()
        entries = []
        if mode != "sessions":
            for identity in self.history.samples:
                running = identity in live
                if (mode == "running" and not running) or (mode == "ended" and running):
                    continue
                entries.append((f"{'运行中' if running else '已结束'} · {identity.name} · {identity.pid}", ("instance", identity)))
        for session in reversed(list(self.history.sessions.values())):
            running = session.identity in live and not session.imported
            if (mode == "running" and not running) or (mode == "ended" and running):
                continue
            entries.append((f"{session.id[:8]} · {session.identity.name} · {STATE_LABELS[session.state]}"
                            + (" · 已打开快照" if session.imported else ""), ("session", session.id)))
        selected = ("session", self.selected_experiment) if self.selected_experiment else ("instance", self.experiment_identity or self.selected_identity)
        values = [value for _, value in entries]
        if selected not in values:
            selected = values[0] if values else None
        page.picker.blockSignals(True)
        page.picker.clear()
        for title, value in entries:
            page.picker.addItem(title, value)
        if selected:
            page.picker.setCurrentIndex(values.index(selected))
        page.picker.blockSignals(False)
        self.selected_experiment = selected[1] if selected and selected[0] == "session" else None
        self.experiment_identity = selected[1] if selected and selected[0] == "instance" else None
        session = self.current_experiment()
        identity = session.identity if session else self.experiment_identity
        self.refresh_recording_details(session)
        page.begin_button.setEnabled(identity in live and not self.worker_failure)
        page.finish_button.setEnabled(bool(session and not session.imported and session.state in ACTIVE))
        page.remove_button.setEnabled(bool(session and (session.imported or session.state not in ACTIVE)))
        page.save_button.setEnabled(session is not None)
        self.export_button.setEnabled(session is not None)
        self.history_title.setText(f"{identity.name} · PID {identity.pid}" if identity else "选择实例或打开已保存实验")
        for value in self.experiment_page.values.values():
            value.setText("—")
        if not session:
            samples = list(self.history.samples.get(identity, []))
            page.chart.set_data(samples)
            page.events.setPlainText("")
            self.comparison.setText(self.history.warning or "选择运行中实例开始基线记录，或应用策略自动新建会话。已结束实例的会话可以继续查看与导出。")
            return
        page.chart.set_data(session.samples, session.marker, (session.started, session.after_end or session.baseline_end))
        for phase, window in session.windows().items():
            if phase == "transition":
                continue
            for field in ("cpu_percent", "read_mbps", "write_mbps"):
                page.values[(field, phase)].setText(summary(session.samples, field, *window))
        text = f"{session.id[:8]} · {STATE_LABELS[session.state]} · 基线设置{'已核验' if session.baseline_verified else '未核验'}"
        if session.issues:
            text += "\n" + "；".join(session.issues)
        restores = [event for event in session.events if event["kind"] == "restore"]
        if restores:
            text += "\n最近恢复：" + restores[-1].get("result", {}).get("status", "未知")
        if self.history.warning:
            text += "\n" + self.history.warning
        self.comparison.setText(text + "\n峰值为采样区间平均速率的最大值；缺失不按零计算。")
        def setting(name, value):
            if value is None:
                return "未知"
            if name == "priority":
                return {v: k for k, v in PRIORITIES.items()}.get(value, str(value)) if isinstance(value, (int, str)) else "未知"
            if name == "affinity":
                return "CPU " + ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else "未知"
            if isinstance(value, bool):
                return "开启" if value else "显式关闭"
            if name == "eco" and isinstance(value, dict):
                return "系统管理" if not value.get("control", 0) & 1 else "开启" if value.get("state", 0) & 1 else "显式关闭"
            return str(value)
        policy = session.context.get("requested_policy") or {}
        affinity_policy = policy.get("affinity", {})
        mode = affinity_policy.get("mode")
        affinity_text = {"unchanged": "不修改", "all": "全部", "last_n": f"最后 {affinity_policy.get('count', '?')} 个",
                         "percentage": f"{affinity_policy.get('percentage', '?')}%", "custom": "自选 CPU"}.get(mode, "未知")
        details = f"请求策略：Priority {'不修改' if policy.get('priority') == 'unchanged' else policy.get('priority', '未知')} · Affinity {affinity_text} · EcoQoS {ECO_MODES.get(policy.get('eco'), '未知')}"
        details += "\n基线实际值：" + " · ".join(f"{name} {setting(name, session.baseline_state.get(name))}" for name in ("priority", "affinity", "eco"))
        details += "\n目标文件版本：" + (session.context.get("target", {}).get("version") or "未能读取")
        names = {"apply": "应用", "next_apply": "再次应用", "restore": "恢复", "maintenance": "维护",
                 "scene_marker": "场景标记", "clock_break": "时钟断点", "disk_stopped": "磁盘记录停止",
                 "observed_change": "设置变化", "process_ended": "实例结束", "sampling_reset": "采样重置",
                 "worker_failed": "后台失败", "rule_stopped": "规则停止", "manual_stop": "手动结束",
                 "sampling_changed": "采样配置变更", "counter_reset": "计数器回退", "recording_stopped": "停止记录",
                 "recovery_abandoned": "放弃恢复记录", "new_session": "新建会话"}
        for event in session.events:
            result = event.get("result", {})
            details += f"\n{event['timestamp'] - session.started:.2f}s · {names.get(event['kind'], event['kind'])} · " + result.get("status", event.get("reason", ""))
            for op in result.get("operations", []):
                details += f"\n  {op['field']}：{op.get('message', '')}"
                if not op.get("untouched") and not op.get("skipped"):
                    details += f" · 操作前 {setting(op['field'], op.get('original'))} → 回读 {setting(op['field'], op.get('actual'))}"
        page.events.setPlainText(details)

    def begin_experiment(self):
        session = self.current_experiment()
        identity = session.identity if session else self.experiment_identity
        if identity is None:
            return
        page = self.experiment_page
        if len(page.scene_notes.toPlainText()) > 4000:
            QMessageBox.warning(self, "场景备注过长", "备注最多 4000 字，请缩短后再开始记录。")
            return
        session = self.history.begin(identity, time.monotonic(), baseline_seconds=page.baseline.value(),
                                     transition_seconds=page.transition.value(), after_seconds=page.after.value())
        if session:
            session.set_scene(page.scene_name.text(), page.scene_notes.toPlainText())
            self.selected_experiment = session.id
            page.filter.setCurrentIndex(0)
        self.refresh_experiment()

    def record_clock(self):
        self.history.clock_update(self.clock_tracker.sample())

    def toggle_disk_capture(self, enabled):
        if enabled:
            self.experiment_page.disk_status.setText("正在探测物理磁盘…")
            self.disk_capture.start()
        else:
            self.disk_capture.stop()

    def select_disk(self, *_):
        key = self.experiment_page.disk_picker.currentData()
        self.history.selected_disk = key if key in self.history.disk_devices else None

    def on_disk_packet(self, packet):
        devices_changed = packet.get("devices") is not None and {d["id"]: d for d in packet["devices"]} != self.history.disk_devices
        try:
            self.history.disk_packet(packet)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            self.disk_capture.stop("磁盘记录失败：" + str(exc))
            return
        page = self.experiment_page
        if devices_changed:
            page.disk_picker.blockSignals(True)
            page.disk_picker.clear()
            page.disk_picker.addItem("选择设备（用于新会话）", None)
            for device in self.history.disk_devices.values():
                page.disk_picker.addItem(f"磁盘 {device['number']} · {device['name']} · {', '.join(device['volumes']) or '盘符未知'}", device["id"])
            page.disk_picker.setCurrentIndex(max(0, page.disk_picker.findData(self.history.selected_disk)))
            page.disk_picker.blockSignals(False)
        page.disk_status.setText(f"正在记录 · {len(self.history.disk_devices)} 个设备 · {self.history.disk_store.count} 条 · 最近采集 {packet['cost_seconds'] * 1000:.1f} ms；选择仅用于新会话")
        self.refresh_recording_details(self.current_experiment())

    def on_disk_stopped(self, reason):
        self.history.disk_stop(reason, time.monotonic())
        self.history.disk_devices.clear()
        page = self.experiment_page
        page.disk_enabled.blockSignals(True)
        page.disk_enabled.setChecked(False)
        page.disk_enabled.blockSignals(False)
        page.disk_picker.clear()
        page.disk_status.setText(reason)
        self.refresh_recording_details(self.current_experiment())

    def save_scene(self):
        session = self.current_experiment()
        if session:
            try:
                session.set_scene(self.experiment_page.scene_name.text(), self.experiment_page.scene_notes.toPlainText())
                self.experiment_page.clock_status.setText("场景信息已保存在会话中；退出前请保存 JSON。")
            except ValueError as exc:
                QMessageBox.warning(self, "无法保存场景", str(exc))

    def mark_scene(self):
        session = self.current_experiment()
        if session:
            try:
                self.record_clock()
                session.scene_marker(time.monotonic(), self.experiment_page.marker.currentText())
                self.refresh_experiment()
            except ValueError as exc:
                QMessageBox.warning(self, "无法记录标记", str(exc))

    def refresh_recording_details(self, session):
        page = self.experiment_page
        page.frame_import.setEnabled(bool(session and (session.imported or session.state not in ACTIVE)))
        frame_key = session.id if session else "none"
        if page.frame_session_id != frame_key:
            page.frame_session_id = frame_key
            page.frame_details.setPlainText(describe_frames(session))
        key = session.id if session else None
        if page.editing_session != key:
            page.editing_session = key
            page.scene_name.setText(session.scene_name if session else "")
            page.scene_notes.setPlainText(session.scene_notes if session else "")
        editable = not session or not session.imported
        page.scene_name.setReadOnly(not editable)
        page.scene_notes.setReadOnly(not editable)
        page.scene_save.setEnabled(bool(session and editable))
        page.marker_button.setEnabled(bool(session and editable and session.state in ACTIVE))
        if session:
            page.clock_status.setText(f"时钟锚点 {len(session.clock['anchors'])} · 断点 {len(session.clock['breaks'])} · " +
                                     ("本机时钟配对；UTC 绝对准确度未测量" if session.clock["anchors"] else "只有固定 UTC 偏移，近似对齐"))
        else:
            page.clock_status.setText("场景标记只记录时间，不应用调度策略。")
        if not session or not session.disk_selection:
            page.disk_details.setPlainText("当前会话未选择磁盘。启用采集并选择设备后，开始新的基线记录。")
            return
        selection = session.disk_selection
        device = selection["device"]
        lines = [f"会话设备：{device['name']} · {', '.join(device.get('volumes', [])) or '盘符未知'}",
                 "；".join(device.get("uncertainty", []))]
        if selection.get("stop_reason"):
            lines.append(selection["stop_reason"])
        for phase, fields in session.disk_report().items():
            lines.append("应用前" if phase == "before" else "应用后")
            for field, title, scale, unit in (("read_B_s", "读取", 1e-6, "MB/s"), ("write_B_s", "写入", 1e-6, "MB/s"),
                    ("read_iops", "读 IOPS", 1, "次/s"), ("write_iops", "写 IOPS", 1, "次/s"),
                    ("read_latency_s", "读延迟", 1000, "ms/请求"), ("write_latency_s", "写延迟", 1000, "ms/请求"),
                    ("queue_mean", "平均队列", 1, "请求")):
                value = fields[field]
                rendered = f"{value['mean'] * scale:.3f} {unit}" if value["mean"] is not None else "—"
                lines.append(f"  {title} {rendered} · 有效覆盖 {value['coverage']:.0%}")
            current = fields["queue_current"]
            lines.append(f"  当前队列采样最大值 {current['instantaneous_max']} · {current['instantaneous_points']} 个瞬时点")
        page.disk_details.setPlainText("\n".join(filter(None, lines)))

    def finish_experiment(self):
        session = self.current_experiment()
        if session and not session.imported:
            session.event({"kind": "manual_stop", "timestamp": time.monotonic(), "reason": "手动结束记录"})
            session.finish(time.monotonic(), "手动结束记录")
            self.refresh_experiment()

    def remove_experiment(self):
        session = self.current_experiment()
        if session and QMessageBox.question(self, "移除实验", "移除本次运行中的记录？请先保存 JSON；已保存的文件不会删除。") == QMessageBox.StandardButton.Yes:
            self.history.remove(session.id)
            self.selected_experiment = None
            self.refresh_experiment()

    def show_repeated_experiments(self):
        sessions = [s for s in self.history.sessions.values() if s.state not in ACTIVE]
        try:
            study = RepeatedStudy(sessions)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法创建多轮项目", str(exc))
            return
        dialog = RepeatedExperimentsDialog(study, self)
        dialog.exec()
        dialog.deleteLater()

    def import_frames(self):
        session = self.current_experiment()
        if not session:
            return
        dialog = FrameImportDialog(session, self)
        if dialog.exec() != FrameImportDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        try:
            clone = self.history.attach_frames(session, dialog.store, dialog.stream.currentData(), **dialog.options())
            self.selected_experiment = clone.id
            self.experiment_page.filter.setCurrentIndex(0)
            self.refresh_experiment()
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            dialog.store.close()
            QMessageBox.warning(self, "导入失败", str(exc))
        finally:
            dialog.deleteLater()

    def open_experiment(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开实验快照", "", "JSON (*.json)")
        if not path:
            return
        try:
            session = self.history.load(path)
            self.selected_experiment = session.id
            self.experiment_page.filter.setCurrentIndex(0)
            self.refresh_experiment()
        except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def save_experiment(self):
        self._export_experiment(False)

    def export_csv(self):
        self._export_experiment(True)

    def _export_experiment(self, csv_format):
        session = self.current_experiment()
        if not session:
            return
        extension = "csv" if csv_format else "json"
        path, _ = QFileDialog.getSaveFileName(self, "保存实验", f"{session.identity.name}-{session.id[:8]}.{extension}", f"{extension.upper()} (*.{extension})")
        if not path:
            return
        try:
            (export_csv if csv_format else save_session)(session, path)
            self.log.appendPlainText(time.strftime("%H:%M:%S ") + "已导出 " + path)
        except (OSError, ValueError, OverflowError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    @Slot(bool)
    def on_restore_done(self, ok):
        if self.pending_restore:
            self.pending_restore = False
            self.banner.setText("已恢复全部原设置。" if ok else "有些设置未能恢复，请查看日志后重试。")
            self.on_command_done()
            return
        if self.pending_close:
            self.pending_close = False
            self.policy_page.refresh_status()
            if ok:
                self._shutdown()
            else:
                QMessageBox.warning(self, "恢复未全部成功", "有些设置未能恢复。请查看日志后重试，也可以保留当前设置并退出。")

    @Slot()
    def on_command_done(self):
        self.pending_commands = max(0, self.pending_commands - 1)
        self.policy_page.command_done()
        if self.close_after_command and not self.pending_commands:
            self.close_after_command = False
            self.close()

    def closeEvent(self, event):
        if self.handoff_waiting or self.restarting:
            event.ignore()
            return
        if not self.allow_close and not self.explicit_exit and self.config.close_to_tray:
            if self.tray and self.tray.available():
                event.ignore()
                self.background_hidden = True
                self.hide()
                return
            self.banner.setText("系统托盘暂不可用，关闭窗口将进入退出流程。")
        if self.allow_close or self.thread is None:
            self.clock_timer.stop()
            self.disk_capture.close()
            if self.tray:
                self.tray.close()
            event.accept()
            return
        event.ignore()
        if self.pending_close or self.shutting_down or self.close_dialog_active:
            return
        if self.pending_commands:
            self.close_after_command = True
            self.banner.setText("操作还在进行，完成后会继续退出…")
            return
        if not self.read_only and (self.restore_count or self.armed):
            box = QMessageBox(self)
            box.setWindowTitle("退出 " + APP_NAME)
            box.setText("仍有恢复记录或已启用的规则。退出前，要恢复原设置还是保留当前设置？")
            restore = box.addButton("恢复原设置并退出", QMessageBox.ButtonRole.AcceptRole)
            keep = box.addButton("保留当前设置并退出", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(restore)
            self.close_dialog_active = True
            try:
                box.exec()
            finally:
                self.close_dialog_active = False
            if box.clickedButton() == restore:
                if not self.ensure_write_access():
                    return
                self.pending_close = True
                self.policy_page.refresh_status()
                self.restore_requested.emit(None)
                return
            if box.clickedButton() != keep:
                self.explicit_exit = False
                return
        self._shutdown()

    def _shutdown(self):
        self.shutting_down = True
        self.config.geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        try:
            self.manager.save(self.config)
        except OSError as exc:
            self.log.appendPlainText("保存窗口设置失败：" + str(exc))
        self.setEnabled(False)
        self.banner.setText("正在停止后台监控…")
        if self.thread and self.thread.isRunning():
            self.stop_requested.emit()
        else:
            self.allow_close = True
            self.close()

    @Slot()
    def _thread_finished(self):
        if self.restarting:
            self.restarting = False
            self.start_monitor()
            self.banner.setText("后台已重启，当前只观察。需要继续调整时，请再次点击应用。")
            return
        if self.handoff_waiting:
            self.worker_stopped.emit()
            return
        if not self.shutting_down:
            self.on_worker_failure("后台线程意外结束，维护已停止")
            return
        self.allow_close = True
        self.close()
