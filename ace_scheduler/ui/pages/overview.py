from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton

from ..components import GlassCard, MetricCard, label
from ..history_chart import HistoryChart
from ..process_table import ProcessTable
from .base import Page


class OverviewPage(Page):
    def __init__(self, owner):
        super().__init__()
        self.metric_grid = QGridLayout()
        self.metric_grid.setSpacing(14)
        self.metric_columns = 0
        self.metrics = {}
        for key, title, unit, color in (("cpu_percent", "CPU 占用", "%", "#087cfa"),
                                         ("read_mbps", "读取速率", "MB/s", "#19a780"),
                                         ("write_mbps", "写入速率", "MB/s", "#9c7aea"),
                                         ("ram_mb", "进程内存", "MB", "#eea04d")):
            card = MetricCard(title, unit, color)
            self.metrics[key] = card
        self.body.addLayout(self.metric_grid)
        self.reflow_metrics()

        processes = GlassCard()
        header = QHBoxLayout()
        header.addWidget(label("实时进程", "sectionTitle"))
        self.count = label("等待发现", "badge")
        header.addWidget(self.count)
        header.addStretch()
        details = QPushButton("显示全部指标")
        details.setObjectName("quiet")
        details.setCheckable(True)
        header.addWidget(details)
        processes.body.addLayout(header)
        owner.table = ProcessTable()
        owner.table.setMinimumHeight(95)
        owner.table.setMaximumHeight(276)
        owner.table.itemSelectionChanged.connect(owner.select_table_process)
        processes.body.addWidget(owner.table)
        details.toggled.connect(owner.table.set_detailed)
        details.toggled.connect(lambda value: details.setText("收起扩展指标" if value else "显示全部指标"))
        self.empty = label("还没有找到匹配的进程。游戏或目标程序启动后，数据会显示在这里。", "muted", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(85)
        processes.body.addWidget(self.empty)
        self.body.addWidget(processes)

        history = GlassCard()
        row = QHBoxLayout()
        row.addWidget(label("资源趋势", "sectionTitle"))
        row.addStretch()
        row.addWidget(label("最近 60 秒", "muted"))
        history.body.addLayout(row)
        owner.chart = HistoryChart()
        history.body.addWidget(owner.chart)
        self.body.addWidget(history)

        details = GlassCard()
        info = QGridLayout()
        info.setHorizontalSpacing(22)
        self.details = {}
        for column, (key, title) in enumerate((("priority", "当前优先级"), ("affinity", "CPU 分配"),
                                               ("eco", "EcoQoS"), ("total_read_gb", "累计读取"),
                                               ("total_write_gb", "累计写入"))):
            info.addWidget(label(title, "muted"), 0, column)
            value = label("—", "detailValue")
            info.addWidget(value, 1, column)
            self.details[key] = value
        details.body.addLayout(info)
        self.body.addWidget(details)
        self.body.addWidget(label("数据来自所选进程，其中的 I/O 不能作为 SSD 实际读取量。", "muted"))
        self.body.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow_metrics()

    def reflow_metrics(self):
        columns = 4 if self.width() >= 900 else 2
        if columns == self.metric_columns:
            return
        self.metric_columns = columns
        while self.metric_grid.count():
            self.metric_grid.takeAt(0)
        for index, card in enumerate(self.metrics.values()):
            self.metric_grid.addWidget(card, index // columns, index % columns)
