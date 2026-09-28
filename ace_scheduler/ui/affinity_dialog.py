from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from ace_scheduler.config.models import AffinitySpec
from .affinity_widget import AffinityWidget
from .components import GlassCard, label


class AffinityDialog(QDialog):
    """Edits CPU assignment only; cancelling never changes the rule drafts."""
    def __init__(self, topology, rules, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CPU 分配")
        self.resize(680, 460)
        self.setMinimumWidth(570)
        self.chosen_spec = None
        specs = {rule.policy.affinity for rule in rules}
        self.mixed = len(specs) > 1
        self.edited = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        card = GlassCard("CPU 分配", rules[0].name if len(rules) == 1 else f"统一调整 {len(rules)} 条规则的 CPU 分配")
        card.setToolTip("\n".join(rule.name for rule in rules))
        self.hint = label("所选规则的 CPU 分配不同，请先选择一种分配方式。" if self.mixed else "这里只修改 CPU 分配，各行的其他参数保持不变。", "muted", True)
        card.body.addWidget(self.hint)
        self.editor = AffinityWidget(topology)
        self.editor.set_spec(AffinitySpec() if self.mixed else rules[0].policy.affinity)
        card.body.addWidget(self.editor)
        layout.addWidget(card)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText("更新草稿")
        self.confirm.setObjectName("primary")
        self.confirm.setEnabled(not self.mixed)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.editor.changed.connect(self.mark_edited)
        layout.addWidget(buttons)

    def mark_edited(self):
        self.edited = True
        self.confirm.setEnabled(True)

    def accept(self):
        if self.mixed and not self.edited:
            return
        try:
            self.chosen_spec = self.editor.value()
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        super().accept()
