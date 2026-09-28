from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton

from ..components import GlassCard, label
from ..history_chart import HistoryChart
from .base import Page


class ExperimentPage(Page):
    def __init__(self, owner):
        super().__init__()
        intro = GlassCard()
        intro.body.addWidget(label("比较调度前后的资源占用", "sectionTitle"))
        intro.body.addWidget(label("先记录一段未调整时的数据，再应用策略。这里会保留同一进程的前后结果。", "muted", True))
        steps = QHBoxLayout()
        for text in ("01  观察基线", "02  应用策略", "03  比较变化"):
            steps.addWidget(label(text, "badge"))
        steps.addStretch()
        intro.body.addLayout(steps)
        self.body.addWidget(intro)

        history = GlassCard()
        row = QHBoxLayout()
        owner.history_title = label("等待选择进程", "sectionTitle", True)
        row.addWidget(owner.history_title, 1)
        owner.export_button = QPushButton("导出 CSV")
        owner.export_button.clicked.connect(owner.export_csv)
        owner.export_button.setEnabled(False)
        row.addWidget(owner.export_button)
        history.body.addLayout(row)
        self.chart = HistoryChart()
        self.chart.setMinimumHeight(215)
        history.body.addWidget(self.chart)
        history.body.addWidget(label("最近 60 秒 · 虚线标记最近一次应用或恢复", "muted"))
        self.body.addWidget(history)

        comparison = GlassCard("前后对照")
        grid = QGridLayout()
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(18)
        grid.addWidget(label("指标", "muted"), 0, 0)
        grid.addWidget(label("应用前 · Before", "detailValue"), 0, 1)
        grid.addWidget(label("应用后 · After", "detailValue"), 0, 2)
        self.values = {}
        for row, (field, name) in enumerate((("cpu_percent", "CPU %"), ("read_mbps", "Read MB/s"), ("write_mbps", "Write MB/s")), 1):
            grid.addWidget(label(name, "body"), row, 0)
            for column, phase in ((1, "before"), (2, "after")):
                value = label("—", "body", True)
                grid.addWidget(value, row, column)
                self.values[(field, phase)] = value
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        comparison.body.addLayout(grid)
        owner.comparison = label("还没有应用策略。先观察一段时间，应用后即可查看前后对照。", "muted", True)
        comparison.body.addWidget(owner.comparison)
        self.body.addWidget(comparison)
        self.body.addWidget(label("请在相近场景下比较，并确认操作已成功。CPU 调度不会直接限制磁盘读写速度。", "muted", True))
        self.body.addStretch()
