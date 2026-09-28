from PySide6.QtCore import Property, QEasingCurve, QPointF, QRectF, QSize, Qt, QPropertyAnimation
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)


def label(text, role="body", wrap=False):
    value = QLabel(text)
    value.setProperty("role", role)
    value.setWordWrap(wrap)
    return value


class Backdrop(QWidget):
    """Static, cached ambient color; no blur pipeline or continuous animation."""
    def __init__(self):
        super().__init__()
        self.cache = None

    def resizeEvent(self, event):
        self.cache = None
        super().resizeEvent(event)

    def paintEvent(self, event):
        if self.cache is None:
            ratio = self.devicePixelRatioF()
            self.cache = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
            self.cache.setDevicePixelRatio(ratio)
            self.cache.fill(QColor("#edf2f9"))
            paint = QPainter(self.cache)
            for x, y, radius, color in ((.76, .06, .68, "#c9e1fa"), (.98, .74, .56, "#d3efe7"),
                                         (.05, .95, .55, "#e3dff7"), (.25, .01, .35, "#f6ede9")):
                center = QPointF(self.width() * x, self.height() * y)
                gradient = QRadialGradient(center, self.width() * radius)
                gradient.setColorAt(0, QColor(color))
                transparent = QColor(color)
                transparent.setAlpha(0)
                gradient.setColorAt(1, transparent)
                paint.fillRect(self.rect(), gradient)
            paint.end()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.cache)


class GlassCard(QFrame):
    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("glassCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 20)
        self.body.setSpacing(14)
        if title:
            self.body.addWidget(label(title, "sectionTitle"))
        if subtitle:
            self.body.addWidget(label(subtitle, "muted", True))


class MetricCard(GlassCard):
    def __init__(self, title, unit, accent="#087cfa"):
        super().__init__()
        self.body.setSpacing(7)
        heading = QHBoxLayout()
        heading.addWidget(label(title, "muted"))
        heading.addStretch()
        dot = label("●", "muted")
        dot.setStyleSheet(f"color: {accent}; font-size: 9px;")
        heading.addWidget(dot)
        self.body.addLayout(heading)
        line = QHBoxLayout()
        line.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.value_label = label("—", "metric")
        self.unit_label = label(unit, "muted")
        line.addWidget(self.value_label)
        line.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(line)
        self.note = label("等待进程数据", "muted")
        self.body.addWidget(self.note)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value, note=""):
        self.value_label.setText(value)
        self.note.setText(note)


class Switch(QAbstractButton):
    """Keyboard-accessible switch. Animates only in response to a toggle."""
    def __init__(self, accessible_name):
        super().__init__()
        self.setCheckable(True)
        self.setAccessibleName(accessible_name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(48, 29)
        self._position = 0.0
        self.animation = QPropertyAnimation(self, b"position", self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    @Property(float)
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value
        self.update()

    def _animate(self, checked):
        self.animation.stop()
        if not self.isVisible():
            self.position = float(checked)
            return
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(float(checked))
        self.animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = QColor("#30bb88" if self.isChecked() else "#d6e0eb")
        if not self.isEnabled():
            color.setAlpha(100)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(1, 2, 46, 25), 12.5, 12.5)
        painter.setBrush(QColor(87, 111, 144, 28))
        painter.drawEllipse(QRectF(3 + self.position * 21, 5, 21, 21))
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(3 + self.position * 21, 4, 21, 21))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#087cfa"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(.5, .5, 47, 28), 14, 14)


def setting_row(title, description, control):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 5, 0, 5)
    text = QVBoxLayout()
    text.setSpacing(5)
    text.addWidget(label(title, "detailValue"))
    text.addWidget(label(description, "muted", True))
    layout.addLayout(text, 1)
    layout.addSpacing(24)
    layout.addWidget(control)
    return widget


def icon(kind, color="#66809e", size=22):
    result = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(scale)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 24, size / 24)
        painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "overview":
            for x, y in ((4, 4), (14, 4), (4, 14), (14, 14)):
                painter.drawRoundedRect(QRectF(x, y, 6, 6), 2, 2)
        elif kind == "policy":
            for y, knob in ((6, 9), (12, 16), (18, 7)):
                painter.drawLine(4, y, 20, y)
                painter.setBrush(QColor("#f6faff"))
                painter.drawEllipse(QPointF(knob, y), 2.4, 2.4)
        elif kind == "experiment":
            painter.drawLine(5, 19, 5, 12)
            painter.drawLine(12, 19, 12, 5)
            painter.drawLine(19, 19, 19, 9)
            painter.drawLine(3, 22, 21, 22)
        elif kind == "settings":
            painter.drawRoundedRect(QRectF(4, 4, 16, 16), 5, 5)
            painter.drawEllipse(QPointF(12, 12), 3.5, 3.5)
            for x, y, dx, dy in ((12, 1, 0, 3), (12, 20, 0, 3), (1, 12, 3, 0), (20, 12, 3, 0)):
                painter.drawLine(x, y, x + dx, y + dy)
        elif kind == "chip":
            painter.drawRoundedRect(QRectF(6, 6, 12, 12), 3, 3)
            painter.drawRect(QRectF(9, 9, 6, 6))
            for point in (9, 15):
                for x, y, dx, dy in ((point, 2, 0, 4), (point, 18, 0, 4), (2, point, 4, 0), (18, point, 4, 0)):
                    painter.drawLine(x, y, x + dx, y + dy)
        painter.end()
        result.addPixmap(pixmap)
    return result
