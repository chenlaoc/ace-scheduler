"""Application-wide themes, including custom painted controls and dialogs."""
import re

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def is_dark(widget):
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


def apply_palette(app, mode="system"):
    controller = getattr(app, "theme_controller", None)
    if controller is None:
        app.setStyle("Fusion")
        controller = app.theme_controller = ThemeController(app)
    controller.set_mode(mode)
    return controller


class ThemeController(QObject):
    changed = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self.mode = "system"
        self.effective = "light"
        self.applied = False
        self.system_scheme = app.styleHints().colorScheme()
        app.styleHints().colorSchemeChanged.connect(self.system_changed)

    @property
    def app(self):
        return QApplication.instance()

    def system_changed(self, scheme):
        self.system_scheme = scheme
        # Qt delivers the native palette change after the scheme signal.
        # Reapply our palette on the next event-loop turn.
        QTimer.singleShot(0, lambda: self.apply(force=True))

    def set_mode(self, mode):
        if mode not in ("system", "light", "dark"):
            raise ValueError("未知主题")
        self.mode = mode
        self.apply()

    def apply(self, force=False):
        dark = self.mode == "dark" or (self.mode == "system" and self.system_scheme == Qt.ColorScheme.Dark)
        effective = "dark" if dark else "light"
        if self.applied and effective == self.effective and not force:
            return
        changed = not self.applied or self.effective != effective
        self.effective = effective
        self.applied = True
        palette = QPalette()
        colors = {"Window": ("#edf2fa", "#131d2c"), "WindowText": ("#18263d", "#e3ebf7"),
                  "Base": ("#ffffff", "#1b293d"), "AlternateBase": ("#f6f8fc", "#223248"),
                  "Text": ("#18263d", "#e3ebf7"), "Button": ("#f5f8fd", "#25364e"),
                  "ButtonText": ("#18263d", "#e3ebf7"), "Highlight": ("#087cfa", "#2588ed"),
                  "HighlightedText": ("#ffffff", "#ffffff"), "ToolTipBase": ("#ffffff", "#223248"),
                  "ToolTipText": ("#18263d", "#e3ebf7"), "PlaceholderText": ("#738197", "#a5b7cf"),
                  "Mid": ("#cad6e6", "#405574")}
        for name, pair in colors.items():
            palette.setColor(getattr(QPalette.ColorRole, name), QColor(pair[int(dark)]))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#71829a" if dark else "#919cad"))
        self.app.setPalette(palette)
        if changed:
            self.app.setStyleSheet(stylesheet(dark))
        self.changed.emit(self.effective)


STYLE = """
QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 13px; color: #18263d; }
QMainWindow, QWidget#page, QWidget#pageBody, QStackedWidget, QScrollArea#pageScroll,
QScrollArea#pageScroll > QWidget > QWidget { background: transparent; border: none; }
QLabel { background: transparent; border: none; }
QLabel[role="eyebrow"] { color: #718299; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel[role="pageTitle"] { font-size: 29px; font-weight: 650; letter-spacing: -1px; color: #182438; }
QLabel[role="sectionTitle"] { font-size: 17px; font-weight: 600; }
QLabel[role="muted"] { color: #738197; font-size: 12px; }
QLabel[role="body"] { color: #5d6b80; font-size: 13px; }
QLabel[role="metric"] { font-size: 35px; font-weight: 600; letter-spacing: -1px; }
QLabel[role="detailValue"] { font-size: 14px; font-weight: 600; }
QLabel[role="badge"] { color: #15745e; background: rgba(34, 168, 130, 22); border: 1px solid rgba(34, 168, 130, 28); border-radius: 13px; padding: 5px 11px; font-size: 11px; font-weight: 600; }
QLabel#banner { color: #54708e; padding: 7px 14px; background: rgba(255,255,255,100); border-radius: 12px; font-size: 12px; }
QFrame#glassCard { background: rgba(255,255,255,204); border: 1px solid rgba(255,255,255,245); border-radius: 22px; }
QFrame#sidebar { background: rgba(248,251,255,170); border: 1px solid rgba(255,255,255,235); border-radius: 26px; }
QFrame#inset { background: rgba(229,237,246,100); border: none; border-radius: 16px; }
QFrame#separator { background: rgba(66,94,130,20); border: none; max-height: 1px; }
QPushButton { background: rgba(255,255,255,205); border: 1px solid rgba(182,197,216,105); border-radius: 16px; padding: 8px 15px; min-height: 18px; font-weight: 500; }
QPushButton:hover { background: #ffffff; border-color: #accee9; }
QPushButton:pressed { background: #e6effc; }
QPushButton:disabled { color: #99a6b8; background: rgba(231,238,247,155); border-color: transparent; }
QPushButton#primary { color: white; background: #087cfa; border-color: #087cfa; font-weight: 600; padding: 9px 24px; }
QPushButton#primary:hover { background: #006de5; }
QPushButton#primary:disabled { color: #eef5ff; background: #aac7e7; border-color: #aac7e7; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #64809e; }
QPushButton#quiet:hover { background: rgba(224,235,249,160); color: #087cfa; }
QPushButton#nav { background: transparent; text-align: left; padding: 12px 16px; border: 1px solid transparent; border-radius: 18px; color: #718198; font-size: 14px; font-weight: 500; }
QPushButton#nav:hover { background: rgba(255,255,255,135); }
QPushButton#nav:checked { color: #087cfa; background: rgba(255,255,255,235); border-color: white; font-weight: 600; }
QPushButton#preset { text-align: left; padding: 14px 16px; border-radius: 17px; background: rgba(239,244,251,160); border-color: transparent; font-size: 13px; }
QPushButton#preset:checked { background: #eaf3ff; border: 1px solid #81b9fb; color: #087cfa; }
QPushButton#cpuChip { min-height: 18px; padding: 8px 6px; border-radius: 12px; background: rgba(235,241,249,145); border-color: transparent; font-size: 12px; }
QPushButton#cpuChip:checked { background: #e4f0ff; color: #087cfa; border-color: #94c3fb; font-weight: 600; }
QComboBox { background: rgba(245,248,253,230); border: 1px solid #e0e7f1; border-radius: 12px; padding: 8px 30px 8px 13px; min-height: 18px; }
QComboBox:hover { border-color: #b3cfed; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: white; border: 1px solid #dae4f1; padding: 5px; selection-background-color: #e8f2ff; selection-color: #087cfa; }
QListWidget#ruleList { background: transparent; border: none; outline: none; }
QListWidget#ruleList::item { padding: 13px 8px; margin: 3px 0; border-radius: 13px; color: #506079; }
QListWidget#ruleList::item:selected { background: #eaf2ff; color: #087cfa; }
QListWidget#ruleList::item:hover { background: #f1f6fe; }
QTableWidget { background: transparent; alternate-background-color: rgba(243,247,252,130); border: none; gridline-color: transparent; selection-background-color: #eaf3ff; selection-color: #1765b8; outline: none; }
QTableWidget::item { padding: 8px 10px; border: none; }
QTableWidget#policyTable::item { padding: 0; }
QTableWidget#policyTable QComboBox { font-size: 12px; padding: 7px 21px 7px 9px; border-radius: 11px; min-height: 18px; }
QTableWidget#policyTable QComboBox::drop-down { width: 18px; }
QTableWidget#policyTable QPushButton { font-size: 12px; padding: 7px 6px; border-radius: 11px; }
QTableWidget#policyTable QCheckBox { background: transparent; font-size: 12px; spacing: 6px; }
QTableWidget#policyTable QLabel[role="muted"] { font-size: 11px; padding-left: 20px; }
QHeaderView { background: transparent; }
QHeaderView::section { background: transparent; color: #8390a2; font-size: 11px; font-weight: 500; border: none; padding: 10px 10px; text-align: left; }
QTableCornerButton::section { background: transparent; border: none; }
QPlainTextEdit { background: rgba(243,247,252,165); border: 1px solid rgba(211,224,239,130); border-radius: 16px; padding: 12px; color: #566c88; font-family: 'Cascadia Code', 'Consolas', 'Microsoft YaHei UI'; font-size: 12px; }
QLineEdit { background: white; border: 1px solid #cddded; padding: 8px; border-radius: 10px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #cad6e6; border-radius: 3px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 7px; }
QScrollBar::handle:horizontal { background: #cad6e6; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { color: #243752; background: #ffffff; border: 1px solid #dae4f1; padding: 6px; }
"""


# Keep geometry identical between themes; only replace the light color tokens.
DARK_COLORS = {
    "#18263d": "#e3ebf7", "#182438": "#f0f5ff", "#718299": "#a5b7cf",
    "#738197": "#a5b7cf", "#5d6b80": "#b8c8dd", "#15745e": "#77dfbf",
    "#54708e": "#b2c9e5", "#ffffff": "#26374f", "white": "#26374f",
    "#accee9": "#6d91b9", "#e6effc": "#324966", "#99a6b8": "#8192ab",
    "#087cfa": "#77b7ff", "#006de5": "#1673d1", "#eef5ff": "#96aac3",
    "#aac7e7": "#324b6a", "#64809e": "#a9c3e2", "#718198": "#a5b7cf",
    "#eaf3ff": "#293f5c", "#81b9fb": "#588bc4", "#e4f0ff": "#294766",
    "#94c3fb": "#6799d1", "#e0e7f1": "#3a4f6b", "#b3cfed": "#7196be",
    "#dae4f1": "#405776", "#e8f2ff": "#304d6e", "#506079": "#b8c8dd",
    "#eaf2ff": "#293f5c", "#f1f6fe": "#293f5c", "#1765b8": "#b9dbff",
    "#8390a2": "#a7b9cf", "#566c88": "#b3c7e0", "#cddded": "#405776",
    "#cad6e6": "#435a79", "#243752": "#e3ebf7",
    "rgba(34, 168, 130, 22)": "rgba(34, 168, 130, 35)",
    "rgba(34, 168, 130, 28)": "rgba(80, 195, 158, 60)",
    "rgba(255,255,255,100)": "rgba(38,56,81,180)",
    "rgba(255,255,255,204)": "rgba(27,41,61,232)",
    "rgba(255,255,255,245)": "rgba(102,130,163,65)",
    "rgba(248,251,255,170)": "rgba(25,38,57,220)",
    "rgba(255,255,255,235)": "rgba(53,76,106,190)",
    "rgba(229,237,246,100)": "rgba(40,59,85,180)",
    "rgba(66,94,130,20)": "rgba(131,159,193,45)",
    "rgba(255,255,255,205)": "rgba(39,57,82,220)",
    "rgba(182,197,216,105)": "rgba(94,125,160,100)",
    "rgba(231,238,247,155)": "rgba(34,49,70,180)",
    "rgba(224,235,249,160)": "rgba(49,72,102,200)",
    "rgba(255,255,255,135)": "rgba(48,68,95,190)",
    "rgba(239,244,251,160)": "rgba(36,53,76,200)",
    "rgba(235,241,249,145)": "rgba(36,53,76,200)",
    "rgba(245,248,253,230)": "rgba(30,46,67,240)",
    "rgba(243,247,252,130)": "rgba(35,52,75,160)",
    "rgba(243,247,252,165)": "rgba(23,36,54,225)",
    "rgba(211,224,239,130)": "rgba(87,117,153,90)",
}


def stylesheet(dark):
    text = STYLE
    if dark:
        text = re.sub(r"#[0-9a-f]{6}|rgba\([^)]*\)|\bwhite\b",
                      lambda match: DARK_COLORS.get(match.group(), match.group()), text)
        text += "QPushButton#primary { color: white; background: #1475d8; border-color: #2588ed; }"
        text += "QPushButton#primary:hover { background: #2687e7; }"
        text += "QPushButton#primary:disabled { color: #8192ab; background: #293f5c; border-color: #293f5c; }"
    return text + """
QDialog, QMessageBox { background: palette(window); }
QMenu { background: palette(base); color: palette(text); border: 1px solid palette(mid); padding: 5px; }
QMenu::item { padding: 7px 24px; }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenu::item:disabled { color: palette(mid); }
"""
