import logging
import sys

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QHBoxLayout,
                               QLineEdit, QMainWindow, QWidget)
from .theme import is_dark


class CaptionButton(QAbstractButton):
    def __init__(self, kind, title, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(36, 32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setToolTip(title)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse() or self.isDown()
        close_hover = hovered and self.kind == "close"
        dark = is_dark(self)
        background = QColor("#d83146" if self.isDown() else "#e34a5c") if close_hover else (QColor(58, 79, 107, 230 if hovered else 160) if dark else QColor(255, 255, 255, 210 if hovered else 110))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 10, 10)
        color = QColor("white" if close_hover else ("#c1d4ec" if dark else "#536b87") if self.isEnabled() else "#8192ab")
        painter.setPen(QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "minimize":
            painter.drawLine(QPointF(13, 17), QPointF(23, 17))
        elif self.kind == "maximize":
            painter.drawRoundedRect(QRectF(13, 11, 10, 10), 1.5, 1.5)
        elif self.kind == "restore":
            painter.drawLine(16, 10, 24, 10)
            painter.drawLine(24, 10, 24, 18)
            painter.drawRoundedRect(QRectF(12, 13, 9, 9), 1, 1)
        elif self.kind == "close":
            painter.drawLine(13, 11, 23, 21)
            painter.drawLine(23, 11, 13, 21)
        if self.hasFocus():
            painter.setPen(QPen(QColor("#087cfa"), 1))
            painter.drawRoundedRect(QRectF(1, 1, 34, 30), 9, 9)


class TitleBar(QWidget):
    """The page header is the draggable title bar; interactive controls stay clickable."""
    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self._drag_offset = None
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(18)
        self.controls = QWidget(self)
        layout = QHBoxLayout(self.controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.minimize_button = CaptionButton("minimize", "最小化")
        self.maximize_button = CaptionButton("maximize", "最大化")
        self.close_button = CaptionButton("close", "关闭")
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximize)
        self.close_button.clicked.connect(window.close)

    def finish(self):
        self.row.addWidget(self.controls, 0, Qt.AlignmentFlag.AlignTop)

    def sync_state(self):
        maximized = self.host.isMaximized()
        self.maximize_button.kind = "restore" if maximized else "maximize"
        title = "还原窗口" if maximized else "最大化"
        self.maximize_button.setAccessibleName(title)
        self.maximize_button.setToolTip(title)
        self.maximize_button.update()

    def toggle_maximize(self):
        self.host.showNormal() if self.host.isMaximized() else self.host.showMaximized()

    def is_drag_area(self, point):
        if not self.rect().contains(point):
            return False
        child = self.childAt(point)
        while child is not None and child is not self:
            if isinstance(child, (QAbstractButton, QComboBox, QLineEdit)):
                return False
            child = child.parentWidget()
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drag_area(event.position().toPoint()):
            handle = self.host.windowHandle()
            if not handle or not handle.startSystemMove():
                if not self.host.isMaximized():
                    self._drag_offset = event.globalPosition().toPoint() - self.host.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.host.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drag_area(event.position().toPoint()):
            self._drag_offset = None
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FramelessMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.native_frame = None
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint |
                            Qt.WindowType.WindowSystemMenuHint)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            hwnd = int(self.winId())
            if self.native_frame is None or self.native_frame.hwnd != hwnd:
                from ace_scheduler.windows.window_frame import WindowFrame
                self.native_frame = WindowFrame(hwnd)
                try:
                    self.native_frame.install()
                except OSError:
                    logging.getLogger("ace_scheduler").exception("Window frame setup")
        if hasattr(self, "title_bar"):
            self.title_bar.sync_state()

    def is_caption_point(self, x, y):
        if not hasattr(self, "title_bar"):
            return False
        return self.title_bar.is_drag_area(self.title_bar.mapFrom(self, QPoint(x, y)))

    def nativeEvent(self, event_type, message):
        if self.native_frame is not None:
            handled, result = self.native_frame.handle(message, self)
            if handled:
                return True, result
        return super().nativeEvent(event_type, message)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.sync_state()
