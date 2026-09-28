from __future__ import annotations

import copy
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHeaderView,
                               QHBoxLayout, QInputDialog, QMessageBox, QPushButton,
                               QTableWidget, QVBoxLayout, QWidget)

from ace_scheduler.config.models import ProcessRule, preset, process_name
from ..affinity_dialog import AffinityDialog
from ..components import GlassCard, Switch, label
from .base import Page

PRESET_NAMES = (("默认", "Default"), ("温和", "Mild"), ("较强", "Strong"), ("自定义", "Custom"))
PRIORITY_NAMES = (("空闲", "Idle"), ("低于正常", "Below Normal"), ("正常", "Normal"),
                  ("高于正常", "Above Normal"), ("高", "High"))


@dataclass
class RuleControls:
    select: QCheckBox
    status: QWidget
    preset: QComboBox
    priority: QComboBox
    cpu: QPushButton
    eco: Switch
    keep: Switch
    enabled: Switch


class PolicyPage(Page):
    """A rule sheet with per-row drafts and explicit, transactional batch commits."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.drafts = {}
        self.selected = {rule.key for rule in owner.config.rules if rule.enabled}
        self.controls = {}
        self.pending_keys = ()
        self.message = "编辑后统一保存或应用。"
        card = GlassCard()
        card.body.setContentsMargins(18, 18, 18, 18)
        card.body.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label("进程策略", "sectionTitle"))
        self.count = label("", "badge")
        header.addWidget(self.count)
        header.addStretch()
        self.selection_buttons = []
        for title, mode in (("全选", "all"), ("只选启用项", "enabled"), ("清空", "none")):
            button = QPushButton(title)
            button.setObjectName("quiet")
            button.clicked.connect(lambda checked=False, mode=mode: self.select_rules(mode))
            self.selection_buttons.append(button)
            header.addWidget(button)
        card.body.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(label("统一设置", "muted"))
        self.preset_buttons = {}
        for title, name in PRESET_NAMES[:3]:
            button = QPushButton(title)
            button.setToolTip(f"为勾选规则设置 {name}；保留各行的维护与监控开关")
            button.clicked.connect(lambda checked=False, name=name: self.set_preset(self.selected_keys(), name))
            self.preset_buttons[name] = button
            toolbar.addWidget(button)
        self.cpu_button = QPushButton("CPU 分配…")
        self.cpu_button.clicked.connect(lambda: self.edit_affinity(self.selected_keys()))
        toolbar.addWidget(self.cpu_button)
        self.bulk_keep = QComboBox()
        self.bulk_keep.addItem("维护方式…", None)
        self.bulk_keep.addItem("仅应用一次", False)
        self.bulk_keep.addItem("持续维护", True)
        self.bulk_keep.setAccessibleName("统一设置勾选规则的维护方式")
        self.bulk_keep.activated.connect(self.set_bulk_keep)
        toolbar.addWidget(self.bulk_keep)
        toolbar.addStretch()
        card.body.addLayout(toolbar)

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("policyTable")
        self.table.setHorizontalHeaderLabels(("批量范围 / 进程", "预设", "优先级", "CPU 分配", "EcoQoS", "持续维护", "监控"))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(68)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in enumerate((190, 96, 104, 108, 64, 76, 62)):
            self.table.setColumnWidth(index, width)
        card.body.addWidget(self.table)
        card.body.addWidget(label("左侧勾选决定批量范围；右侧「监控」决定规则是否启用。各行参数可分别调整，最后一起应用。", "muted", True))
        management = QHBoxLayout()
        self.add_button = QPushButton("＋ 添加进程")
        self.add_button.clicked.connect(self.add_rule)
        management.addWidget(self.add_button)
        self.remove_button = QPushButton("删除勾选的自定义规则")
        self.remove_button.setObjectName("quiet")
        self.remove_button.clicked.connect(self.remove_rules)
        management.addWidget(self.remove_button)
        management.addStretch()
        card.body.addLayout(management)
        self.body.addWidget(card)
        self.body.addStretch()

        self.footer = GlassCard()
        self.footer.body.setContentsMargins(20, 12, 20, 12)
        self.footer.body.setSpacing(6)
        primary = QHBoxLayout()
        self.summary = label("", "muted", True)
        primary.addWidget(self.summary, 1)
        self.save_button = QPushButton("保存勾选")
        self.save_button.clicked.connect(lambda: self.commit(False))
        self.apply_button = QPushButton("应用勾选")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(lambda: self.commit(True))
        primary.addWidget(self.save_button)
        primary.addWidget(self.apply_button)
        self.footer.body.addLayout(primary)
        secondary = QHBoxLayout()
        self.stop_button = QPushButton("停止勾选规则")
        self.stop_button.setToolTip("停止后续自动应用，当前调度值保留")
        self.stop_button.clicked.connect(lambda: self.run_command("stop", self.selected_keys()))
        self.restore_button = QPushButton("恢复勾选规则")
        self.restore_button.setToolTip("恢复勾选规则在本会话修改前的值，并停止自动应用")
        self.restore_button.clicked.connect(lambda: self.run_command("restore", self.selected_keys()))
        self.discard_button = QPushButton("撤销勾选的编辑")
        self.discard_button.clicked.connect(self.discard_selected)
        for button in (self.stop_button, self.restore_button, self.discard_button):
            button.setObjectName("quiet")
            secondary.addWidget(button)
        secondary.addStretch()
        self.footer.body.addLayout(secondary)
        self.rebuild()

    def rule(self, key):
        return self.drafts.get(key) or next(rule for rule in self.owner.config.rules if rule.key == key)

    def selected_keys(self):
        return tuple(rule.key for rule in self.owner.config.rules if rule.key in self.selected)

    def _cell(self, control):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.addWidget(control)
        if isinstance(control, Switch):
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return container

    def rebuild(self):
        self.controls.clear()
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.owner.config.rules))
        for index, base in enumerate(self.owner.config.rules):
            key = base.key
            name = QWidget()
            name_layout = QVBoxLayout(name)
            name_layout.setContentsMargins(7, 9, 3, 9)
            name_layout.setSpacing(5)
            selected = QCheckBox(base.name.replace("&", "&&"))
            selected.setToolTip(base.name + "\n勾选以加入批量操作范围")
            selected.setChecked(key in self.selected)
            selected.toggled.connect(lambda value, key=key: self.select_key(key, value))
            name_layout.addWidget(selected)
            status = label("", "muted")
            name_layout.addWidget(status)
            self.table.setCellWidget(index, 0, name)
            presets = QComboBox()
            for title, value in PRESET_NAMES:
                presets.addItem(title, value)
            presets.setAccessibleName(base.name + " 预设")
            presets.activated.connect(lambda _, key=key: self.set_preset((key,), self.controls[key].preset.currentData()))
            priority = QComboBox()
            for title, value in PRIORITY_NAMES:
                priority.addItem(title, value)
            priority.setAccessibleName(base.name + " 优先级")
            priority.activated.connect(lambda _, key=key: self.edit_policy(key, priority=self.controls[key].priority.currentData()))
            cpu = QPushButton()
            cpu.setAccessibleName(base.name + " CPU 分配")
            cpu.clicked.connect(lambda checked=False, key=key: self.edit_affinity((key,)))
            eco, keep, enabled = (Switch(base.name + " " + title) for title in ("EcoQoS", "持续维护", "监控"))
            eco.clicked.connect(lambda value, key=key: self.edit_policy(key, eco=value))
            keep.clicked.connect(lambda value, key=key: self.stage(replace(self.rule(key), keep_enforced=value)))
            enabled.clicked.connect(lambda value, key=key: self.stage(replace(self.rule(key), enabled=value)))
            controls = RuleControls(selected, status, presets, priority, cpu, eco, keep, enabled)
            self.controls[key] = controls
            for column, control in enumerate((presets, priority, cpu, eco, keep, enabled), 1):
                self.table.setCellWidget(index, column, self._cell(control))
            self.sync_row(key)
        self.table.setFixedHeight(min(620, 44 + 68 * len(self.controls)))
        self.refresh_status()

    def sync_row(self, key):
        rule, widgets = self.rule(key), self.controls[key]
        name = next((name for _, name in PRESET_NAMES[:3] if preset(name) == rule.policy), "Custom")
        widgets.preset.setCurrentIndex(widgets.preset.findData(name))
        widgets.priority.setCurrentIndex(widgets.priority.findData(rule.policy.priority))
        widgets.priority.setToolTip(rule.policy.priority)
        widgets.eco.setChecked(rule.policy.eco)
        widgets.keep.setChecked(rule.keep_enforced)
        widgets.enabled.setChecked(rule.enabled)
        topology = self.owner.topology
        spec = rule.policy.affinity
        if topology and topology.affinity_supported:
            try:
                cpus = topology.resolve(spec)
                text = f"全部 · {len(cpus)}" if spec.mode == "all" else f"最后 {spec.count} 个" if spec.mode == "last_n" else f"{spec.percentage}% · {len(cpus)}" if spec.mode == "percentage" else f"自选 · {len(cpus)}"
                widgets.cpu.setText(text)
                widgets.cpu.setToolTip("CPU " + ", ".join(map(str, cpus)) + "\n点击编辑分配")
            except ValueError as exc:
                widgets.cpu.setText("需要重选")
                widgets.cpu.setToolTip(str(exc))
        else:
            widgets.cpu.setText("不支持" if topology else "检测中…")
            widgets.cpu.setToolTip(topology.limitation if topology else "正在检测 CPU")
        widgets.cpu.setEnabled(bool(topology and topology.affinity_supported))

    def select_key(self, key, checked):
        self.selected.add(key) if checked else self.selected.discard(key)
        self.message = ""
        self.refresh_status()

    def select_rules(self, mode):
        self.selected = {rule.key for rule in self.owner.config.rules
                         if mode == "all" or (mode == "enabled" and self.rule(rule.key).enabled)}
        for key, widgets in self.controls.items():
            widgets.select.blockSignals(True)
            widgets.select.setChecked(key in self.selected)
            widgets.select.blockSignals(False)
        self.message = ""
        self.refresh_status()

    def stage(self, rule, refresh=True):
        if self.owner.pending_commands:
            return
        base = next(value for value in self.owner.config.rules if value.key == rule.key)
        if rule == base:
            self.drafts.pop(rule.key, None)
        else:
            self.drafts[rule.key] = rule
        self.sync_row(rule.key)
        self.message = ""
        if refresh:
            self.refresh_status()

    def set_preset(self, keys, name):
        if name == "Custom":
            self.message = "直接调整该行的参数，CPU 分配可点击编辑。"
            self.refresh_status()
            return
        for key in keys:
            self.stage(replace(self.rule(key), policy=preset(name)), refresh=False)
        self.refresh_status()

    def edit_policy(self, key, **changes):
        rule = self.rule(key)
        self.stage(replace(rule, policy=replace(rule.policy, **changes)))

    def set_bulk_keep(self, index):
        value = self.bulk_keep.itemData(index)
        if value is not None:
            for key in self.selected_keys():
                self.stage(replace(self.rule(key), keep_enforced=value), refresh=False)
        self.bulk_keep.setCurrentIndex(0)
        self.refresh_status()

    def edit_affinity(self, keys):
        if not keys or not self.owner.topology or not self.owner.topology.affinity_supported:
            return
        dialog = AffinityDialog(self.owner.topology, [self.rule(key) for key in keys], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for key in keys:
                self.edit_policy(key, affinity=dialog.chosen_spec)
        dialog.deleteLater()

    def discard_selected(self):
        if self.owner.pending_commands:
            return
        for key in self.selected_keys():
            self.drafts.pop(key, None)
            self.sync_row(key)
        self.message = "已撤销勾选规则的编辑。"
        self.refresh_status()

    def save_config(self, candidate):
        try:
            self.owner.manager.save(candidate)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        self.owner.config = candidate
        self.owner.config_changed.emit(copy.deepcopy(candidate))
        return True

    def commit(self, apply=False):
        keys = self.selected_keys()
        if not keys or not self.owner.topology or self.owner.pending_commands or (apply and self.owner.read_only):
            return
        try:
            for key in keys:
                if self.owner.topology.affinity_supported:
                    self.owner.topology.resolve(self.rule(key).policy.affinity)
        except ValueError as exc:
            QMessageBox.warning(self, "CPU 选择无效", self.rule(key).name + "：" + str(exc))
            return
        active = tuple(key for key in keys if self.rule(key).enabled)
        if apply and active and not self.owner.ensure_write_access():
            return
        candidate = copy.deepcopy(self.owner.config)
        candidate.rules = [self.rule(rule.key) if rule.key in keys else rule for rule in candidate.rules]
        if not self.save_config(candidate):
            return
        for key in keys:
            self.drafts.pop(key, None)
        self.message = f"已保存 {len(keys)} 条规则 · 修改项需点击应用"
        if apply and active:
            self.run_command("apply", active)
        elif apply:
            self.message = "已保存 · 勾选规则均已停用，未应用调度。"
        self.refresh_status()

    def run_command(self, kind, keys):
        if not keys or self.owner.read_only or self.owner.pending_commands or not self.owner.topology:
            return
        if kind != "stop" and not self.owner.ensure_write_access():
            return
        self.pending_keys = tuple(keys)
        self.owner.pending_commands += 1
        verb = {"apply": "应用", "stop": "停止", "restore": "恢复"}[kind]
        self.message = f"正在{verb} {len(keys)} 条规则…"
        signal = {"apply": self.owner.apply_many_requested, "stop": self.owner.stop_many_requested,
                  "restore": self.owner.restore_many_requested}[kind]
        self.refresh_status()
        signal.emit(tuple(keys))

    def command_done(self):
        self.pending_keys = ()
        self.message = "批量处理完成 · 各实例的实际结果见运行概览和日志。"
        self.refresh_status()

    def refresh_status(self):
        keys = self.selected_keys()
        pending = bool(self.owner.pending_commands or self.owner.pending_close or self.owner.shutting_down or self.owner.handoff_waiting or self.owner.restarting)
        if hasattr(self.owner, "restore_all_button"):
            self.owner.restore_all_button.setEnabled(bool(self.owner.topology and not self.owner.read_only and not pending))
            self.owner.monitor_interval.setEnabled(not pending)
            self.owner.enforce_interval.setEnabled(not pending)
            settings = getattr(self.owner, "settings_page", None)
            if settings:
                settings.restart_button.setEnabled(bool(self.owner.worker_failure) and not pending)
                settings.force_restore_button.setEnabled(not self.owner.read_only and not pending and not self.owner.worker_failure)
                settings.abandon_button.setEnabled(not self.owner.read_only and not pending)
                settings.close_behavior.setEnabled(not pending)
        active = sum(self.rule(key).enabled for key in keys)
        self.count.setText(f"已勾选 {len(keys)} / {len(self.controls)}")
        summary = f"勾选 {len(keys)} 条 · 待保存 {len(self.drafts)} 条"
        if len(keys) != active:
            summary += f" · {len(keys) - active} 条停用，应用时跳过"
        if self.drafts.keys() - self.selected:
            summary += "\n含未勾选的编辑，本次将保留。"
        if self.message:
            summary += "\n" + self.message
        self.summary.setText(summary)
        self.apply_button.setText(f"应用勾选 ({active})")
        self.save_button.setEnabled(bool(keys and self.owner.topology and not pending))
        self.apply_button.setEnabled(bool(keys and self.owner.topology and not pending and not self.owner.read_only))
        if self.owner.worker_failure:
            self.apply_button.setEnabled(False)
            self.owner.restore_all_button.setEnabled(False)
        for button in (self.stop_button, self.restore_button):
            button.setEnabled(bool(keys and self.owner.topology and not pending and not self.owner.read_only))
        self.discard_button.setEnabled(bool(set(keys) & self.drafts.keys()) and not pending)
        self.remove_button.setEnabled(any(not self.rule(key).builtin for key in keys) and not pending)
        self.add_button.setEnabled(not pending)
        self.table.setEnabled(not pending)
        for button in (*self.preset_buttons.values(), self.bulk_keep):
            button.setEnabled(bool(keys) and not pending)
        self.cpu_button.setEnabled(bool(keys and self.owner.topology and self.owner.topology.affinity_supported and not pending))
        for button in self.selection_buttons:
            button.setEnabled(not pending)
        saved = {rule.key: rule for rule in self.owner.config.rules}
        for key, widgets in self.controls.items():
            current = "策略已启用" if key in self.owner.armed else "仅监控" if saved[key].enabled else "已停用"
            if key in self.drafts:
                current = "待保存 · " + current
            if key in self.pending_keys:
                current = "正在处理…"
            widgets.status.setText(current)

    def on_ready(self):
        for key in self.controls:
            self.sync_row(key)
        self.refresh_status()

    def add_rule(self):
        value, ok = QInputDialog.getText(self, "添加用户态进程", "精确进程文件名（不区分大小写）：")
        if not ok:
            return
        try:
            name = process_name(value)
            if any(rule.key == name.casefold() for rule in self.owner.config.rules):
                raise ValueError("该进程规则已存在")
            if len(self.owner.config.rules) >= 256:
                raise ValueError("最多支持 256 条规则")
        except ValueError as exc:
            QMessageBox.warning(self, "无法添加", str(exc))
            return
        candidate = copy.deepcopy(self.owner.config)
        candidate.rules.append(ProcessRule(name))
        if self.save_config(candidate):
            self.selected.add(name.casefold())
            self.rebuild()

    def remove_rules(self):
        keys = {key for key in self.selected_keys() if not self.rule(key).builtin}
        if not keys or self.owner.pending_commands:
            return
        candidate = copy.deepcopy(self.owner.config)
        candidate.rules = [rule for rule in candidate.rules if rule.key not in keys]
        if self.save_config(candidate):
            for key in keys:
                self.drafts.pop(key, None)
                self.selected.discard(key)
            self.rebuild()
