from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget
from deepecohab_recorder.ui.theme import color, manager


class WaveformPreview(QWidget):
    """Compact three-period TTL timing preview."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.period_ms = 33.333
        self.pulse_ms = 4.0
        self.valid = True
        self.message = ""
        self.setFixedHeight(105)
        self.setMinimumWidth(460)
        manager().changed.connect(self.apply_theme)

    def apply_theme(self, *args):
        self.update()

    def set_parameters(
        self, period_ms: float, pulse_ms: float, valid: bool, message: str = ""
    ) -> None:
        self.period_ms = max(0.001, float(period_ms))
        self.pulse_ms = max(0.0, float(pulse_ms))
        self.valid = bool(valid)
        self.message = message
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(7, 7, -7, -7)
        painter.fillRect(rect, color("surface"))
        painter.setPen(QPen(color("accent"), 1.5))
        painter.drawRect(rect)

        if not self.valid:
            painter.setPen(color("bad"))
            painter.drawText(
                rect.adjusted(12, 10, -12, -10), Qt.TextWordWrap, self.message
            )
            return

        interval_ms = max(0.0, self.period_ms - self.pulse_ms)
        left = rect.left() + 18
        right = rect.right() - 18
        text_y = rect.top() + 19
        high = rect.top() + 44
        low = rect.bottom() - 15
        width = max(1.0, float(right - left))
        period_px = width / 3.0
        pulse_fraction = min(1.0, self.pulse_ms / self.period_ms)

        painter.setPen(QPen(color("ink-2"), 1))
        painter.drawText(
            left,
            text_y,
            (
                f"3 periods | period {self.period_ms:.3f} ms | "
                f"pulse {self.pulse_ms:.3f} ms | interval {interval_ms:.3f} ms"
            ),
        )

        # Mark the start and end of the middle period with dotted guides.
        guide_pen = QPen(color("line-strong"), 1, Qt.DotLine)
        for x in (left + period_px, left + 2.0 * period_px):
            painter.setPen(guide_pen)
            painter.drawLine(int(x), rect.top() + 27, int(x), rect.bottom() - 5)

        wave_pen = QPen(color("good"), 2)
        painter.setPen(wave_pen)
        x = float(left)
        for _ in range(3):
            high_end = x + period_px * pulse_fraction
            end = x + period_px
            painter.drawLine(int(x), low, int(x), high)
            painter.drawLine(int(x), high, int(high_end), high)
            painter.drawLine(int(high_end), high, int(high_end), low)
            painter.drawLine(int(high_end), low, int(end), low)
            x = end
