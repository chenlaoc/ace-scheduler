from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import time

from PySide6.QtCore import QByteArray, QThread, Qt, Signal, Slot, QSize
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from ace_scheduler.config.models import PRIORITIES
from ace_scheduler.branding import APP_NAME, app_icon
from ace_scheduler.core.experiment import History, summary
from ace_scheduler.core.process_monitor import MonitorWorker
from .process_table import number
from .components import Backdrop, icon, label
from .theme import STYLE
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
        if enable_tray:
            from .tray import TrayController
            self.tray = TrayController(self)
        if config.geometry:
            self.restoreGeometry(QByteArray.fromBase64(config.geometry.encode("ascii", errors="ignore")))
        if start_worker:
            self.start_monitor()

    def start_monitor(self):
        self.worker_failure = ""
        self.thread = QThread(self)
        self.worker = MonitorWorker(copy.deepcopy(self.config), self.manager.path.with_name("recovery.json"),
                                    self.read_only, self.write_enabled)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.ready.connect(self.on_ready)
        self.worker.snapshot.connect(self.on_snapshot)
        self.worker.log_line.connect(self.log.appendPlainText)
        self.worker.applied.connect(self.on_applied)
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
        self.page_description = label("掌握进程的实时资源变化。", "muted")
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
        self.banner = label("只读模式 · 调度操作已禁用" if self.read_only else "观察模式 · 点击应用后，策略才会生效", "muted", True)
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
        self.setStyleSheet(STYLE)
        self.statusBar().hide()
        self.show_page(0)

    def show_page(self, index):
        titles = (("运行概览", "掌握进程的实时资源变化。"),
                  ("调度策略", "直接逐行调整，或勾选多个进程统一配置。"),
                  ("实验对照", "观察调度前后，CPU 与 I/O 如何变化。"),
                  ("设置与日志", "调整监控节奏，查看每一次操作。"),
                  ("关于", "版本信息、本地数据与第三方许可。"))
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.page_title.setText(titles[index][0])
        self.page_description.setText(titles[index][1])
        self.process_picker.setVisible(index in (0, 2))
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
            self.banner.setText("调度写入需要管理员权限；请以管理员身份重新打开。")
        return False

    @Slot(str)
    def on_worker_failure(self, message):
        self.worker_failure = message
        self.armed.clear()
        self.pending_commands = 0
        self.pending_restore = self.pending_close = self.close_after_command = False
        self.activate_window()
        self.banner.setText(message + "。恢复记录保留，可在设置中重新启动后台后重试。")
        self.session_badge.setText("●  后台已停止")
        self.policy_page.refresh_status()
        if self.tray:
            self.tray.update(True)

    def restart_monitor(self):
        if not self.worker_failure or self.restarting or self.handoff_waiting or self.shutting_down:
            return
        self.restarting = True
        self.banner.setText("正在重新启动后台；策略将保持未启用。")
        if self.thread and self.thread.isRunning():
            self.suspend_requested.emit()
        else:
            self._thread_finished()

    def export_diagnostics(self):
        if QMessageBox.question(self, "导出诊断", "将导出版本/构建、Windows 版本、CPU 拓扑数量、无进程名称的配置摘要和脱敏事件日志。\n不含原始配置、恢复记录、个人路径、PID 或令牌，也不会上传。") != QMessageBox.StandardButton.Yes:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存诊断 ZIP", "ACE-Scheduler-diagnostics.zip", "ZIP (*.zip)")
        if not path:
            return
        from ace_scheduler.diagnostics import export_diagnostics
        try:
            export_diagnostics(path, self.config, self.topology, self.log.toPlainText(), self.manager.path.parent,
                               read_only=self.read_only, worker_failed=self.worker_failure)
            self.banner.setText("诊断 ZIP 已保存到所选位置，未上传。")
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
        message = ("将对仍属于原实例的记录恢复原值，包括被外部修改或写入未确认的字段。确认覆盖？" if force else
                   "将停止全部规则、保留当前调度值，并归档所有恢复记录（包括损坏记录）。之后不能再用这些记录恢复。")
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
                self.history.add(row.identity, row.metrics)
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
        self.history.mark(identity, timestamp, status)
        self.refresh_history()

    def refresh_history(self):
        identity = self.selected_identity
        row = next((row for row in self.table.rows if row.identity == identity), None) if identity else None
        metrics = row.metrics if row else None
        notes = {"cpu_percent": "整机归一化", "read_mbps": "进程级读取", "write_mbps": "进程级写入", "ram_mb": "当前工作集"}
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
        self.export_button.setEnabled(identity is not None)
        samples = list(self.history.samples.get(identity, []))
        experiment = self.history.experiments.get(identity)
        for chart in (self.chart, self.experiment_page.chart):
            chart.set_data(samples, experiment.marker if experiment else None)
        self.history_title.setText(f"{identity.name} · {identity.pid}" if identity else "等待选择进程")
        for value in self.experiment_page.values.values():
            value.setText("—")
        if identity is None:
            self.comparison.setText("发现进程后，在右上角选择观察对象。")
            return
        if not experiment:
            for field in ("cpu_percent", "read_mbps", "write_mbps"):
                self.experiment_page.values[(field, "before")].setText(summary(samples, field))
            self.comparison.setText("正在积累基线。应用策略后，将冻结操作前最多 60 秒的数据。")
            return
        after = [sample for sample in samples if sample.timestamp > experiment.marker]
        for field in ("cpu_percent", "read_mbps", "write_mbps"):
            self.experiment_page.values[(field, "before")].setText(summary(experiment.before, field))
            self.experiment_page.values[(field, "after")].setText(summary(after, field))
        self.comparison.setText("最近操作：" + experiment.label + " · 应用前后各取最多 60 秒，有效样本按时长加权。")

    def export_csv(self):
        identity = self.selected_identity
        if not identity:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出进程 I/O 实验", f"{identity.name}-{identity.pid}.csv", "CSV (*.csv)")
        if not path:
            return
        experiment = self.history.experiments.get(identity)
        samples = {sample.timestamp: sample for sample in self.history.samples.get(identity, [])}
        if experiment:
            samples.update({sample.timestamp: sample for sample in experiment.before})
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["process", "pid", "process_created_unix", "sample_utc_approx", "phase", "operation_result",
                                 "sample_seconds", "cpu_percent_machine", "read_MB_s", "write_MB_s", "ram_MB",
                                 "total_read_GB", "total_write_GB", "read_count", "write_count"])
                offset = time.time() - time.monotonic()
                for timestamp, sample in sorted(samples.items()):
                    phase = "observe" if not experiment else ("before" if timestamp <= experiment.marker else "after")
                    writer.writerow([identity.name, identity.pid, identity.created,
                                     datetime.fromtimestamp(timestamp + offset, timezone.utc).isoformat(), phase,
                                     experiment.label if experiment else "", sample.sample_seconds, sample.cpu_percent,
                                     sample.read_mbps, sample.write_mbps, sample.ram_mb, sample.total_read_gb,
                                     sample.total_write_gb, sample.read_count, sample.write_count])
            self.log.appendPlainText(time.strftime("%H:%M:%S ") + "已导出 " + path)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    @Slot(bool)
    def on_restore_done(self, ok):
        if self.pending_restore:
            self.pending_restore = False
            self.banner.setText("已恢复全部原设置。" if ok else "恢复未全部成功，请查看日志后重试。")
            self.on_command_done()
            return
        if self.pending_close:
            self.pending_close = False
            self.policy_page.refresh_status()
            if ok:
                self._shutdown()
            else:
                QMessageBox.warning(self, "恢复未全部成功", "部分进程拒绝恢复。请查看日志；可以重试或选择保留设置退出。")

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
            self.banner.setText("系统托盘不可用，本次关闭将按正常退出流程处理。")
        if self.allow_close or self.thread is None:
            if self.tray:
                self.tray.close()
            event.accept()
            return
        event.ignore()
        if self.pending_close or self.shutting_down or self.close_dialog_active:
            return
        if self.pending_commands:
            self.close_after_command = True
            self.banner.setText("等待正在执行的应用操作完成后退出…")
            return
        if not self.read_only and (self.restore_count or self.armed):
            box = QMessageBox(self)
            box.setWindowTitle("退出 " + APP_NAME)
            box.setText("本会话修改过的进程仍在运行。请选择退出时如何处理调度设置。")
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
            self.banner.setText("后台已重新启动；当前只观察，策略需再次明确应用。")
            return
        if self.handoff_waiting:
            self.worker_stopped.emit()
            return
        if not self.shutting_down:
            self.on_worker_failure("后台线程意外结束，维护已停止")
            return
        self.allow_close = True
        self.close()
