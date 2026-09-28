from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPlainTextEdit, QPushButton

from ace_scheduler.config.models import INTERVALS
from ..components import GlassCard, label, setting_row
from .base import Page


class SettingsPage(Page):
    def __init__(self, owner):
        super().__init__()
        timing = GlassCard("监控偏好")
        owner.monitor_interval = QComboBox()
        owner.enforce_interval = QComboBox()
        for combo, value in ((owner.monitor_interval, owner.config.monitor_interval),
                             (owner.enforce_interval, owner.config.enforce_interval)):
            for seconds in sorted(set(INTERVALS) | {value}):
                combo.addItem(f"{seconds} 秒", seconds)
            combo.setCurrentIndex(combo.findData(value))
            combo.activated.connect(owner.intervals_changed)
            combo.setMinimumWidth(110)
        timing.body.addWidget(setting_row("数据采样", "更新进程列表和资源指标的间隔", owner.monitor_interval))
        timing.body.addWidget(setting_row("策略维护", "启用持续维护时，检查调度设置的间隔", owner.enforce_interval))
        self.body.addWidget(timing)

        session = GlassCard("会话管理")
        owner.restore_all_button = QPushButton("恢复全部原设置")
        owner.restore_all_button.setEnabled(False)
        owner.restore_all_button.clicked.connect(owner.request_restore_all)
        session.body.addWidget(setting_row("结束当前实验", "恢复本会话修改过的进程，并停止自动应用", owner.restore_all_button))
        self.body.addWidget(session)

        logs = GlassCard()
        row = QHBoxLayout()
        row.addWidget(label("活动日志", "sectionTitle"))
        row.addStretch()
        row.addWidget(label("最近 500 条", "muted"))
        clear = QPushButton("清空显示")
        clear.setObjectName("quiet")
        row.addWidget(clear)
        logs.body.addLayout(row)
        owner.log = QPlainTextEdit()
        owner.log.setReadOnly(True)
        owner.log.setMaximumBlockCount(500)
        owner.log.setMinimumHeight(235)
        clear.clicked.connect(owner.log.clear)
        logs.body.addWidget(owner.log)
        self.body.addWidget(logs)
        self.body.addWidget(label("配置自动保存在当前用户的 AppData。CPU % 按整机归一化，MB / GB 使用十进制。", "muted", True))
        self.body.addStretch()
