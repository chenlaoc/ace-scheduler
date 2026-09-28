from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from ace_scheduler.config.models import PRIORITIES


def number(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


class ProcessTable(QTableWidget):
    HEADERS = ("Process", "PID", "CPU %", "RAM MB", "Read MB/s", "Write MB/s", "Total Read GB",
               "Total Write GB", "Priority", "Affinity", "EcoQoS", "Status")

    def __init__(self):
        super().__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().setDefaultSectionSize(47)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate((165, 72, 65, 75, 90, 90, 105, 105, 100, 120, 90, 230)):
            self.setColumnWidth(column, width)
        self.rows = []
        self.set_detailed(False)

    def set_detailed(self, enabled):
        for column in (3, 6, 7, 8, 9, 10):
            self.setColumnHidden(column, not enabled)
        mode = QHeaderView.ResizeMode.Interactive if enabled else QHeaderView.ResizeMode.Stretch
        for column, width in ((0, 165), (11, 230)):
            self.horizontalHeader().setSectionResizeMode(column, mode)
            if enabled:
                self.setColumnWidth(column, width)

    def update_rows(self, rows):
        selected = self.selected_identity()
        self.blockSignals(True)
        self.clearSelection()
        self.setCurrentCell(-1, -1)
        self.rows = rows
        self.setRowCount(len(rows))
        priority_names = {value: key for key, value in PRIORITIES.items()}
        for index, row in enumerate(rows):
            metrics, state = row.metrics, row.state
            data = [row.name, str(row.pid)]
            data.extend(number(getattr(metrics, field, None)) for field in
                        ("cpu_percent", "ram_mb", "read_mbps", "write_mbps", "total_read_gb", "total_write_gb"))
            data.extend([priority_names.get(state.priority, f"0x{state.priority:X}" if state.priority else "—"),
                         ",".join(map(str, state.affinity)) if state.affinity else "—",
                         state.eco.label if state.eco is not None else "未知", row.status])
            for column, value in enumerate(data):
                item = self.item(index, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.setItem(index, column, item)
                item.setText(value)
                item.setToolTip(value + ("\n" + "\n".join(state.errors.values()) if column >= 8 and state.errors else ""))
                if 1 <= column <= 7:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if selected is not None and row.identity == selected:
                self.selectRow(index)
        self.setFixedHeight(min(276, max(95, 44 + len(rows) * 47)))
        self.blockSignals(False)

    def select_identity(self, identity):
        self.blockSignals(True)
        for index, row in enumerate(self.rows):
            if row.identity == identity:
                self.selectRow(index)
                break
        self.blockSignals(False)

    def selected_identity(self):
        index = self.currentRow()
        return self.rows[index].identity if 0 <= index < len(self.rows) else None
