import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from .theme import is_dark


class HistoryChart(QWidget):
    def __init__(self):
        super().__init__()
        self.samples = []
        self.marker = None
        self.setMinimumHeight(185)

    def set_data(self, samples, marker=None):
        self.samples = list(samples)
        self.marker = marker
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 9))
        now = self.samples[-1].timestamp if self.samples else time.monotonic()
        width = self.width() / 3
        for index, (field, title, color) in enumerate((("cpu_percent", "CPU %", "#268cfa"),
                                                      ("read_mbps", "Read MB/s", "#28ac91"),
                                                      ("write_mbps", "Write MB/s", "#a083e7"))):
            rect = QRectF(index * width + 3, 37, width - 25, self.height() - 64)
            painter.setPen(QColor("#b6c9e2" if is_dark(self) else "#6b7d94"))
            painter.drawText(QPointF(rect.left(), 17), title)
            valid = [getattr(s, field) for s in self.samples if getattr(s, field) is not None]
            maximum = max(1.0, max(valid, default=1.0) * 1.15)
            for part in (0, .5, 1):
                y = rect.top() + rect.height() * part
                painter.setPen(QPen(QColor("#354961" if is_dark(self) else "#e6edf5"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor("#a1b6d0" if is_dark(self) else "#99a7b9"))
            painter.drawText(QPointF(rect.left(), rect.bottom() + 21), "−60 s")
            painter.drawText(QPointF(rect.right() - 28, rect.bottom() + 21), "现在")
            painter.drawText(QRectF(rect.right() - 64, 0, 64, 24), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{maximum:.1f}")
            segments, segment = [], []
            for sample in self.samples:
                value = getattr(sample, field)
                if value is None:
                    if segment:
                        segments.append(segment)
                    segment = []
                    continue
                segment.append(QPointF(rect.left() + (sample.timestamp - now + 60) / 60 * rect.width(),
                                        rect.bottom() - value / maximum * rect.height()))
            if segment:
                segments.append(segment)
            for segment in segments:
                path = QPainterPath(segment[0])
                for point in segment[1:]:
                    path.lineTo(point)
                fill = QPainterPath(path)
                fill.lineTo(segment[-1].x(), rect.bottom())
                fill.lineTo(segment[0].x(), rect.bottom())
                fill.closeSubpath()
                gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                top_color = QColor(color)
                top_color.setAlpha(42)
                gradient.setColorAt(0, top_color)
                gradient.setColorAt(1, QColor(255, 255, 255, 0))
                painter.fillPath(fill, gradient)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(color), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPath(path)
                painter.setBrush(QColor(color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(segment[-1], 2.6, 2.6)
            if self.marker is not None and now - 60 <= self.marker <= now:
                x = rect.left() + (self.marker - now + 60) / 60 * rect.width()
                painter.setPen(QPen(QColor("#efaa83"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if not valid:
                painter.setPen(QColor("#a0aec0"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "等待有效采样")
