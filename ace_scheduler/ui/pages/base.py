from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget


class Page(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setObjectName("pageScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName("pageBody")
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 2, 8, 8)
        self.body.setSpacing(18)
        self.setWidget(self.content)
