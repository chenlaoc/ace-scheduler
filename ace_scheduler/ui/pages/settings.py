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
        self.close_behavior = QComboBox()
        self.close_behavior.addItem("退出应用", False)
        self.close_behavior.addItem("隐藏到托盘", True)
        self.close_behavior.setCurrentIndex(int(owner.config.close_to_tray))
        self.close_behavior.activated.connect(owner.close_preference_changed)
        timing.body.addWidget(setting_row("关闭按钮", "隐藏后采样和已启用的维护继续；从托盘菜单可真正退出", self.close_behavior))
        self.body.addWidget(timing)

        session = GlassCard("会话管理")
        owner.restore_all_button = QPushButton("恢复全部原设置")
        owner.restore_all_button.setEnabled(False)
        owner.restore_all_button.clicked.connect(owner.request_restore_all)
        session.body.addWidget(setting_row("恢复调度", "恢复当前及未完成会话；遇到冲突会保留现值并报告", owner.restore_all_button))
        self.recovery_status = label("启动只观察。异常结束后重新打开，可在这里恢复原设置。", "muted", True)
        session.body.addWidget(self.recovery_status)
        recovery_actions = QHBoxLayout()
        self.force_restore_button = QPushButton("覆盖冲突并恢复…")
        self.force_restore_button.clicked.connect(lambda: owner.resolve_recovery(True))
        self.abandon_button = QPushButton("保留现值并放弃记录…")
        self.abandon_button.clicked.connect(lambda: owner.resolve_recovery(False))
        recovery_actions.addWidget(self.force_restore_button)
        recovery_actions.addWidget(self.abandon_button)
        recovery_actions.addStretch()
        session.body.addLayout(recovery_actions)
        self.restart_button = QPushButton("重新启动后台")
        self.restart_button.clicked.connect(owner.restart_monitor)
        session.body.addWidget(setting_row("后台状态", "后台发生异常时停止维护；重启后只观察", self.restart_button))
        self.diagnostics_button = QPushButton("导出诊断 ZIP…")
        self.diagnostics_button.clicked.connect(owner.export_diagnostics)
        session.body.addWidget(setting_row("本地诊断", "导出脱敏摘要与事件日志，不会上传", self.diagnostics_button))
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
