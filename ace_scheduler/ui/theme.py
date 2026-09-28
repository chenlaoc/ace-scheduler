"""A restrained glass-inspired desktop theme; all effects are painted locally."""
from PySide6.QtGui import QColor, QPalette


def apply_palette(app):
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {"Window": "#edf2fa", "WindowText": "#18263d", "Base": "#ffffff",
              "AlternateBase": "#f6f8fc", "Text": "#18263d", "Button": "#f5f8fd",
              "ButtonText": "#18263d", "Highlight": "#087cfa", "HighlightedText": "#ffffff",
              "ToolTipBase": "#ffffff", "ToolTipText": "#18263d"}
    for name, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, name), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#919cad"))
    app.setPalette(palette)


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
