"""Read-only repeated-experiment workspace; synthetic data is always visibly marked."""
import json
import sqlite3

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
                               QComboBox, QDoubleSpinBox, QSpinBox, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QHeaderView, QLineEdit, QPlainTextEdit, QFileDialog)

from ace_scheduler.core.repeated_experiments import RepeatedStudy, METRICS, DECLARATIONS
from ace_scheduler.core.repeat_demo import demo_study
from .components import label


class StudyLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path, parent):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.loaded.emit(RepeatedStudy.load(self.path) if self.path else demo_study())
        except Exception as exc:
            self.failed.emit(str(exc))


class RepeatedExperimentsDialog(QDialog):
    def __init__(self, study, parent=None):
        super().__init__(parent)
        self.study, self.loader = study, None
        self.updating = False
        self.cancel_pending = False
        self.setWindowTitle("多轮实验汇总 · 离线分析")
        self.resize(960, 670)
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.demo_button = QPushButton("载入测试示例")
        self.open_button = QPushButton("打开多轮项目…")
        self.save_button = QPushButton("保存项目…")
        self.export_button = QPushButton("导出汇总 CSV…")
        for button in (self.demo_button, self.open_button, self.save_button, self.export_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.demo_button.clicked.connect(lambda: self.load(None))
        self.open_button.clicked.connect(self.open_project)
        self.save_button.clicked.connect(lambda: self.save(False))
        self.export_button.clicked.connect(lambda: self.save(True))
        self.banner = label("每个会话的应用前／应用后为一对；每轮等权，差值 = 后 − 前。", "muted", True)
        layout.addWidget(self.banner)
        controls = QHBoxLayout()
        self.metric_picker = QComboBox()
        for key, definition in METRICS.items():
            self.metric_picker.addItem(definition[0], key)
        self.coverage = QDoubleSpinBox()
        self.coverage.setRange(.1, 100)
        self.coverage.setSuffix(" %")
        self.pairs = QSpinBox()
        self.pairs.setRange(1, 64)
        controls.addWidget(self.metric_picker, 1)
        controls.addWidget(label("最低覆盖", "muted"))
        controls.addWidget(self.coverage)
        controls.addWidget(label("有效轮数门槛", "muted"))
        controls.addWidget(self.pairs)
        layout.addLayout(controls)
        self.groups = self.table(["场景 / 版本", "来源", "有效 / 总轮", "中位差值", "MAD", "差值范围", "轮数门槛"])
        self.groups.setMinimumHeight(90)
        self.groups.setMaximumHeight(160)
        layout.addWidget(self.groups, 1)
        self.rounds = self.table(["选择", "独立轮次", "前", "后", "差值", "前 / 后覆盖", "纳入 / 排除原因"])
        self.rounds.setMinimumHeight(130)
        layout.addWidget(self.rounds, 2)
        self.rounds.itemChanged.connect(self.selection_changed)
        self.rounds.currentCellChanged.connect(self.edit_round)
        self.fields = {}
        form = QGridLayout()
        for index, (key, title) in enumerate(zip(DECLARATIONS, ("场景", "硬件标识", "应用版本", "条件（画质/路线等）"))):
            widget = QLineEdit()
            widget.setMaxLength(4000)
            self.fields[key] = widget
            row, col = divmod(index, 2)
            form.addWidget(label(title, "muted"), row, col * 2)
            form.addWidget(widget, row, col * 2 + 1)
        self.apply_declaration = QPushButton("保存本轮声明")
        self.apply_declaration.clicked.connect(self.update_declaration)
        form.addWidget(self.apply_declaration, 2, 0, 1, 4)
        layout.addLayout(form)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(80)
        layout.addWidget(self.details)
        self.metric_picker.currentIndexChanged.connect(self.settings_changed)
        self.coverage.valueChanged.connect(self.settings_changed)
        self.pairs.valueChanged.connect(self.settings_changed)
        self.install(study)

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def install(self, study):
        self.study = study
        self.updating = True
        self.metric_picker.setCurrentIndex(self.metric_picker.findData(study.metric))
        self.coverage.setValue(study.minimum_coverage * 100)
        self.pairs.setValue(study.minimum_pairs)
        self.updating = False
        self.refresh()
        if study.records:
            self.rounds.setCurrentCell(0, 1)

    def settings_changed(self):
        if self.updating:
            return
        self.study.metric = self.metric_picker.currentData()
        self.study.minimum_coverage = self.coverage.value() / 100
        self.study.minimum_pairs = self.pairs.value()
        self.refresh()

    def refresh(self):
        report = self.study.report()
        self.last_report = report
        self.updating = True
        selected_row = self.rounds.currentRow()
        self.groups.setRowCount(len(report["groups"]))
        def display(value):
            return "—" if value is None else f"{value:.4g}"
        for i, group in enumerate(report["groups"]):
            basis = group["basis"]
            values = [f"{basis['scene'] or '未声明场景'} / {basis['application_version'] or '未声明版本'}",
                      "测试数据" if basis['origin'] == 'synthetic' else "观察记录",
                      f"{group['valid_rounds']} / {group['total_rounds']}", display(group['median_delta']),
                      display(group['median_absolute_deviation']), f"{display(group['delta_min'])} ~ {display(group['delta_max'])}",
                      "达到" if group['enough_rounds'] else "不足"]
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(json.dumps(basis, ensure_ascii=False, indent=2))
                self.groups.setItem(i, j, item)
        self.groups.setColumnWidth(0, 200)
        self.row_ids = list(self.study.records)
        self.rounds.setRowCount(len(self.row_ids))
        reports = {r['session_id']: r for r in report['rounds']}
        for i, key in enumerate(self.row_ids):
            s = self.study.records[key]['session']
            r = reports.get(key)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if key in self.study.selected else Qt.CheckState.Unchecked)
            self.rounds.setItem(i, 0, check)
            coverage = " / ".join("—" if v is None else f"{v:.1%}" for v in (r['coverage_before'], r['coverage_after'])) if r else "—"
            reason = ("纳入" if r['included'] else "；".join(r['reasons'])) if r else "未选择"
            values = [f"{'[测试] ' if s['context'].get('synthetic') is True else ''}{s['label']} · {key[:8]}",
                      display(r['before']) if r else "—", display(r['after']) if r else "—", display(r['delta']) if r else "—", coverage, reason]
            for j, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.rounds.setItem(i, j, item)
        # Long labels and reasons must not push the numeric columns off the viewport.
        for table, col, width in ((self.groups, 0, 220), (self.rounds, 1, 225), (self.rounds, 6, 190)):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(col, width)
        synthetic = report['synthetic_rounds']
        self.banner.setText(f"{'测试数据：不代表真实游戏收益。 ' if synthetic else ''}已选择 {len(report['rounds'])} 轮 / {len(report['groups'])} 组 · 单位 {report['unit']}\n"
                            "每轮等权；差值 = 后 − 前，负值仅表示指标下降。MAD 表示轮次间离散程度；轮数达标不等于统计显著。")
        self.updating = False
        if 0 <= selected_row < self.rounds.rowCount():
            self.rounds.setCurrentCell(selected_row, 1)
            self.edit_round(selected_row)
        self.apply_declaration.setEnabled(bool(self.row_ids))

    def selection_changed(self, item):
        if not self.updating and item.column() == 0:
            self.study.selected = [self.row_ids[i] for i in range(self.rounds.rowCount())
                                   if self.rounds.item(i, 0).checkState() == Qt.CheckState.Checked]
            self.refresh()

    def edit_round(self, row, *args):
        if self.updating or not 0 <= row < len(self.row_ids):
            return
        key = self.row_ids[row]
        for name, widget in self.fields.items():
            widget.setText(self.study.declarations[key][name])
        r = next((r for r in self.last_report['rounds'] if r['session_id'] == key), None)
        if r and r['included']:
            relative = "基线为零，相对变化未定义" if r['relative_percent'] is None else f"相对变化 {r['relative_percent']:+.2f}%"
            reason = "纳入 · " + relative
        else:
            reason = "；".join(r['reasons']) if r else "未选择此轮。"
        self.details.setPlainText(f"本轮 {key}\n{reason}\n声明仅用于分组，不修改原会话；未知条件应留空，不能默认视为相同。")

    def update_declaration(self):
        row = self.rounds.currentRow()
        if 0 <= row < len(self.row_ids):
            self.study.declarations[self.row_ids[row]] = {key: widget.text() for key, widget in self.fields.items()}
            self.refresh()

    def load(self, path):
        if self.loader and self.loader.isRunning():
            return
        self.loader = StudyLoader(path, self)
        self.loader.loaded.connect(self.install)
        self.loader.failed.connect(lambda error: self.details.setPlainText("读取失败：" + error))
        self.loader.finished.connect(self.load_finished)
        for button in (self.demo_button, self.open_button, self.save_button, self.export_button):
            button.setEnabled(False)
        self.details.setPlainText("正在离线读取和重算；不采集、不应用策略…")
        self.loader.start()

    def load_finished(self):
        for button in (self.demo_button, self.open_button, self.save_button, self.export_button):
            button.setEnabled(True)
        if self.cancel_pending:
            self.reject()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开多轮项目", "", "JSON (*.json)")
        if path:
            self.load(path)

    def save(self, csv_format):
        path, _ = QFileDialog.getSaveFileName(self, "导出多轮分析" if csv_format else "保存多轮项目", "repeated.csv" if csv_format else "repeated.json",
                                             "CSV (*.csv)" if csv_format else "JSON (*.json)")
        if not path:
            return
        try:
            (self.study.export_csv if csv_format else self.study.save)(path)
            self.details.setPlainText("已保存：" + path)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            self.details.setPlainText("保存失败：" + str(exc))

    def reject(self):
        if self.loader and self.loader.isRunning():
            self.cancel_pending = True
            self.details.setPlainText("读取结束后关闭…")
            return
        super().reject()
