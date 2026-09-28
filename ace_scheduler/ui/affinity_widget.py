from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QScrollArea

from ace_scheduler.config.models import AffinitySpec
from .components import label


class AffinityWidget(QWidget):
    changed = Signal()

    def __init__(self, topology, parent=None):
        super().__init__(parent)
        self.topology = topology
        self.spec = AffinitySpec()
        self.checks = {}
        self.loading = False
        self.columns = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        presets = QHBoxLayout()
        presets.setSpacing(5)
        for text, spec in (("全部", AffinitySpec()), ("清空", AffinitySpec("custom")),
                           ("最后 1 个", AffinitySpec("last_n", count=1)),
                           ("最后 2 个", AffinitySpec("last_n", count=2)),
                           ("25%", AffinitySpec("percentage", percentage=25)),
                           ("50%", AffinitySpec("percentage", percentage=50))):
            button = QPushButton(text)
            button.setObjectName("quiet")
            button.setStyleSheet("padding: 5px 7px; font-size: 12px;")
            button.clicked.connect(lambda checked=False, value=spec: self.choose(value))
            button.setEnabled(topology.affinity_supported)
            presets.addWidget(button)
        layout.addLayout(presets)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setMinimumHeight(100)
        self.area.setMaximumHeight(210)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName("pageBody")
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setSpacing(7)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for cpu in topology.available:
            button = QPushButton(f"CPU {cpu}")
            button.setObjectName("cpuChip")
            button.setCheckable(True)
            button.setAccessibleName(f"逻辑处理器 {cpu}")
            button.toggled.connect(self.manual)
            self.checks[cpu] = button
        self.area.setWidget(self.container)
        layout.addWidget(self.area)
        self.description = label("", "muted", True)
        layout.addWidget(self.description)
        self.area.setVisible(topology.affinity_supported)
        self.reflow()
        self.set_spec(self.spec)

    def resizeEvent(self, event):
        self.reflow()
        super().resizeEvent(event)

    def reflow(self):
        columns = max(3, min(10, (self.width() - 8) // 74))
        if columns == self.columns:
            return
        self.columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for pos, button in enumerate(self.checks.values()):
            self.grid.addWidget(button, pos // columns, pos % columns)
        rows = (len(self.checks) + columns - 1) // columns
        self.area.setFixedHeight(min(210, max(94, rows * 43 + 4)))

    def choose(self, spec):
        self.set_spec(spec)
        self.changed.emit()

    def set_spec(self, spec):
        self.loading = True
        self.spec = spec
        try:
            ids = self.topology.resolve(spec) if self.topology.affinity_supported else ()
            message = f"已选择 {len(ids)} / {len(self.topology.available)} 个逻辑处理器"
            invalid = set(spec.cpus) - set(self.topology.available)
            if spec.mode == "custom" and invalid:
                message += f" · 已过滤无效 ID {sorted(invalid)}"
        except ValueError as exc:
            ids, message = (), str(exc)
        for cpu, button in self.checks.items():
            button.setChecked(cpu in ids)
        self.description.setText(message if self.topology.affinity_supported else self.topology.limitation)
        self.loading = False

    def manual(self):
        if self.loading:
            return
        ids = tuple(cpu for cpu, button in self.checks.items() if button.isChecked())
        self.spec = AffinitySpec("custom", cpus=ids)
        self.description.setText(f"已选择 {len(ids)} 个逻辑处理器 · 自定义" if ids else "至少选择一个 CPU 才能应用")
        self.changed.emit()

    def value(self):
        if self.topology.affinity_supported:
            self.topology.resolve(self.spec)
        return self.spec
