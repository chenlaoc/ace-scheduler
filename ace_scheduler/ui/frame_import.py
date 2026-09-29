"""Offline import dialog. Parsing runs away from the GUI/monitor thread."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
                               QPushButton, QFileDialog, QComboBox, QCheckBox, QDoubleSpinBox,
                               QPlainTextEdit, QDialogButtonBox)

from ace_scheduler.core.frame_recording import VERSIONS, read_presentmon, make_attachment, frame_report
from .components import label


def describe_frames(session):
    if not session or not session.frame_attachment:
        return "尚未导入帧文件。选择已结束的会话或打开 JSON 后，可离线导入 PresentMon CSV。"
    a = session.frame_attachment
    s, align = a["stream"], a["alignment"]
    lines = [f"{s['application']} · PID {s['pid']} · 交换链 {s['swap_chain']} · {s['runtime']}",
             f"声明版本 {a['source']['version_declared']} · {s['rows']} 条 · SHA-256 {a['source']['sha256'][:16]}…",
             "QPC 对齐（用户确认同机同次启动）" if align['mode'] == 'qpc' else "手动近似对齐（误差未知）",
             f"长帧 > {a['long_frame_threshold_ms']:g} ms · 覆盖率门槛 {a['minimum_coverage']:.0%}"]
    if align['error_seconds'] is not None:
        lines.append(f"锚点配对误差上界 {align['error_seconds'] * 1000:.3f} ms（不含外部采集误差）")
    for phase, fields in session.frame_report().items():
        lines.append("\n" + ("基线" if phase == "before" else "实验段"))
        for metric, stats in fields.items():
            name = "CPU 帧起点间隔" if metric == "cpu_frame_interval_ms" else "显示驻留时长"
            lines.append(f"{name}：覆盖 {stats['coverage']:.1%} · 完整帧 {stats['frames_fully_inside']} · 缺口 {stats['gap_count']}")
            if stats['median_ms'] is not None:
                lines.append(f"  中位 {stats['median_ms']:.3f} / P95 {stats['p95_ms']:.3f} / P99 {stats['p99_ms']:.3f} ms · 长帧 {stats['long_frames']}")
            lines.append("  " + ("；".join(stats['quality_reasons']) if stats['quality_reasons'] else "覆盖与日志检查通过；不代表调度收益"))
    lines.append("\n统计仅代表已收到、完整落入窗口的帧；覆盖率用区间交集计算。显示驻留时长不等于 CPU 间隔。")
    lines.append("CSV 不含进程创建时间；PID/名称匹配不能独立证明实例身份。原始字段与完整来源保存在 JSON。")
    return "\n".join(lines)


class ParseThread(QThread):
    parsed = Signal(object)
    failed = Signal(str)

    def __init__(self, path, version, log_path, parent):
        super().__init__(parent)
        self.path, self.version, self.log_path = path, version, log_path

    def run(self):
        try:
            result = read_presentmon(self.path, self.version, self.log_path or None, self.isInterruptionRequested)
            self.parsed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class FrameImportDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session, self.store, self.thread = session, None, None
        self.cancel_pending = False
        self.setWindowTitle("导入 PresentMon · 创建只读分析副本")
        self.resize(720, 630)
        layout = QVBoxLayout(self)
        layout.addWidget(label("导入已有文件，不启动采集或修改调度。支持最多 32 MiB / 100000 帧。", "muted", True))
        form = QFormLayout()
        self.path, self.log_path = QLineEdit(), QLineEdit()
        self.browse_buttons = []
        self.path.setPlaceholderText("选择 CSV")
        self.log_path.setPlaceholderText("可选；没有日志时，事件丢失情况记为未知")
        for title, widget, pattern in (("帧文件", self.path, "CSV (*.csv)"), ("采集日志", self.log_path, "日志 (*.log *.txt);;所有文件 (*)")):
            row = QHBoxLayout()
            row.addWidget(widget)
            button = QPushButton("浏览…")
            self.browse_buttons.append(button)
            button.clicked.connect(lambda checked=False, w=widget, p=pattern: self.browse(w, p))
            row.addWidget(button)
            form.addRow(title, row)
        self.version = QComboBox()
        self.version.addItems(VERSIONS)
        form.addRow("工具版本（用户声明）", self.version)
        self.parse_button = QPushButton("读取文件")
        self.parse_button.clicked.connect(self.parse)
        form.addRow(self.parse_button)
        self.stream = QComboBox()
        self.stream.setMinimumContentsLength(20)
        self.stream.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form.addRow("进程 / 交换链", self.stream)
        self.mode = QComboBox()
        self.mode.addItem("QPC 锚点（需要同机同次启动）", "qpc")
        self.mode.addItem("相对时间 · 手动近似校准", "manual")
        form.addRow("时间对齐", self.mode)
        self.same_boot = QCheckBox("确认 CSV 与会话来自同一电脑、同一次系统启动")
        form.addRow(self.same_boot)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1_000_000, 1_000_000)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" 秒")
        form.addRow("首帧相对会话开始（手动）", self.offset)
        self.threshold, self.coverage = QDoubleSpinBox(), QDoubleSpinBox()
        self.threshold.setRange(.001, 10000)
        self.threshold.setDecimals(3)
        self.threshold.setValue(33.333)
        self.threshold.setSuffix(" ms")
        self.coverage.setRange(.1, 100)
        self.coverage.setValue(95)
        self.coverage.setSuffix(" %")
        row = QHBoxLayout()
        row.addWidget(self.threshold)
        row.addWidget(label("最低覆盖", "muted"))
        row.addWidget(self.coverage)
        form.addRow("长帧阈值", row)
        layout.addLayout(form)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(110)
        layout.addWidget(self.preview, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建分析副本")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for widget in (self.stream, self.mode):
            widget.currentIndexChanged.connect(self.update_preview)
        for widget in (self.threshold, self.coverage, self.offset):
            widget.valueChanged.connect(self.update_preview)
        self.same_boot.toggled.connect(self.update_preview)
        for widget in (self.path, self.log_path):
            widget.textChanged.connect(self.invalidate)
        self.version.currentIndexChanged.connect(self.invalidate)
        self.update_preview()

    def browse(self, widget, pattern):
        path, _ = QFileDialog.getOpenFileName(self, "选择已有采集文件", "", pattern)
        if path:
            widget.setText(path)

    def invalidate(self):
        if self.store:
            self.store.close()
            self.store = None
        self.stream.clear()
        self.update_preview()

    def parse(self):
        if self.thread and self.thread.isRunning():
            return
        self.invalidate()
        self.thread = ParseThread(self.path.text(), self.version.currentText(), self.log_path.text(), self)
        self.thread.parsed.connect(self.on_parsed)
        self.thread.failed.connect(self.preview.setPlainText)
        self.thread.finished.connect(self.on_finished)
        for widget in (self.path, self.log_path, self.version, self.parse_button, *self.browse_buttons):
            widget.setEnabled(False)
        self.preview.setPlainText("正在读取并校验文件…")
        self.thread.start()

    def on_parsed(self, store):
        self.store = store
        self.stream.addItem("请选择数据流", None)
        for index, s in enumerate(store.streams):
            self.stream.addItem(f"{s['application']} · PID {s['pid']} · {s['swap_chain']} · {s['runtime']} · {s['rows']} 帧", index)
        self.mode.setCurrentIndex(0 if store.metadata['time_column'] == 'CPUStartQPC' else 1)

    def on_finished(self):
        for widget in (self.path, self.log_path, self.version, self.parse_button, *self.browse_buttons):
            widget.setEnabled(True)
        if self.cancel_pending:
            self.reject()
        elif self.store:
            self.update_preview()

    def options(self):
        return {"mode": self.mode.currentData(), "offset": self.offset.value(), "same_boot": self.same_boot.isChecked(),
                "threshold": self.threshold.value(), "coverage": self.coverage.value() / 100}

    def update_preview(self):
        self.offset.setEnabled(self.mode.currentData() == 'manual')
        self.same_boot.setEnabled(self.mode.currentData() == 'qpc')
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(False)
        if not self.store:
            self.preview.setPlainText("先读取文件；未知格式、乱序、重复时间戳及损坏行将明确拒绝。")
            return
        try:
            import copy
            attachment = make_attachment(self.session, self.store, self.stream.currentData(), **self.options())
            preview = copy.copy(self.session)
            preview.frame_attachment = attachment
            preview._frame_store, preview._frame_stream = self.store, self.stream.currentData()
            self.preview.setPlainText(describe_frames(preview))
            ok.setEnabled(not self.thread or not self.thread.isRunning())
        except (ValueError, TypeError, KeyError) as exc:
            self.preview.setPlainText(str(exc))

    def accept(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()

    def reject(self):
        if self.thread and self.thread.isRunning():
            self.cancel_pending = True
            self.thread.requestInterruption()
            self.preview.setPlainText("正在取消导入…")
            return
        if self.store:
            self.store.close()
            self.store = None
        super().reject()
