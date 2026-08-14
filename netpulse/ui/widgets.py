"""Reusable presentation widgets: cards, stat tiles, and the two charts.

Both charts are painted directly with QPainter rather than pulled from a
plotting library, so the marks follow the house rules exactly: thin bars with
4px rounded data-ends anchored to the baseline, a 2px surface gap between
adjacent fills, 2px series lines, recessive grid and axes, a legend whenever two
series are on screen, and a hover layer on every plotted mark.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QStyledItemDelegate, QToolTip, QVBoxLayout,
                               QWidget)

from ..units import format_bytes, format_rate, split_bytes
from . import theme


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------
class Card(QFrame):
    def __init__(self, title: str = "", hint: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        self.header = QHBoxLayout()
        self.header.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.header.addWidget(self.title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("CardHint")
            self.header.addWidget(hint_label)
        self.header.addStretch(1)
        if title or hint:
            outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)

    def add_header_widget(self, widget: QWidget) -> None:
        self.header.addWidget(widget)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)


class Legend(QWidget):
    """Identity is never carried by colour alone — this ships with every chart."""

    def __init__(self, entries: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        self._values: dict[str, QLabel] = {}
        for label, color in entries:
            item = QWidget()
            line = QHBoxLayout(item)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(7)
            swatch = QLabel()
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(f"background:{color}; border-radius:3px;")
            name = QLabel(label)
            name.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; font-size:12px;")
            value = QLabel("")
            value.setStyleSheet(
                f"color:{theme.TEXT}; font-size:12px; font-weight:600;")
            line.addWidget(swatch)
            line.addWidget(name)
            line.addWidget(value)
            self._values[label] = value
            row.addWidget(item)
        row.addStretch(1)

    def set_value(self, label: str, text: str) -> None:
        if label in self._values:
            self._values[label].setText(text)


class WanIpChip(QFrame):
    """The public IP address, sat beside a page title. Click to copy."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Chip")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 7, 13, 7)
        row.setSpacing(10)

        caption = QLabel("WAN IP")
        caption.setObjectName("ChipLabel")
        self.value = QLabel("checking…")
        self.value.setObjectName("ChipValue")
        row.addWidget(caption)
        row.addWidget(self.value)

        self._address = ""
        self._restore = QTimer(self)
        self._restore.setSingleShot(True)
        self._restore.timeout.connect(self._show_address)
        self.setToolTip("Your public IP address")

    def set_address(self, address: str, source: str = "") -> None:
        self._address = address
        self._show_address()
        if address:
            self.setToolTip(
                f"Your public IP address, according to {source or 'an external service'}.\n"
                "Click to copy.")
        else:
            self.setToolTip(
                "Could not reach any of the address-lookup services.\n"
                "This is normal if you are offline.")

    def set_disabled_note(self) -> None:
        self._address = ""
        self.value.setText("off")
        self.value.setStyleSheet(f"color:{theme.MUTED};")
        self.setToolTip("Public IP lookup is switched off in Settings.")

    def set_checking(self) -> None:
        self._address = ""
        self._restore.stop()
        self.value.setText("checking…")
        self.value.setStyleSheet(f"color:{theme.MUTED};")
        self.setToolTip("Looking up your public IP address…")

    def update_from(self, resolver, enabled: bool) -> None:
        """Reflect the resolver's current state, including 'not asked yet'."""
        if not enabled:
            self.set_disabled_note()
        elif resolver.address:
            self.set_address(resolver.address, resolver.source)
        elif resolver.checked_at:
            self.set_address("", "")          # asked, and nothing answered
        else:
            self.set_checking()

    def _show_address(self) -> None:
        self.value.setText(self._address or "unavailable")
        self.value.setStyleSheet(
            "" if self._address else f"color:{theme.MUTED};")

    def mousePressEvent(self, event) -> None:
        if self._address:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._address)
            self.value.setText("copied")
            self.value.setStyleSheet(f"color:{theme.GOOD};")
            self._restore.start(1200)
        super().mousePressEvent(event)


class StatTile(QFrame):
    """A headline number with its download/upload split underneath."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 15, 18, 15)
        box.setSpacing(4)

        self.label = QLabel(label.upper())
        self.label.setObjectName("TileLabel")

        value_row = QHBoxLayout()
        value_row.setSpacing(5)
        value_row.setContentsMargins(0, 0, 0, 0)
        self.value = QLabel("0")
        self.value.setObjectName("TileValue")
        self.unit = QLabel("B")
        self.unit.setObjectName("TileUnit")
        value_row.addWidget(self.value)
        value_row.addWidget(self.unit, 0, Qt.AlignBottom)
        value_row.addStretch(1)

        self.split = QLabel("")
        self.split.setTextFormat(Qt.RichText)
        self.split.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; font-size:12px;")

        box.addWidget(self.label)
        box.addLayout(value_row)
        box.addWidget(self.split)

    def set_values(self, down: int, up: int, unit: str = "auto") -> None:
        value, suffix = split_bytes(down + up, unit)
        self.value.setText(value)
        self.unit.setText(suffix)
        self.split.setText(
            f'<span style="color:{theme.DOWN}">&#9660;</span> {format_bytes(down, unit)}'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:{theme.UP}">&#9650;</span> {format_bytes(up, unit)}'
        )


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def _nice_ceiling(value: float) -> float:
    """Round a maximum up to a friendly axis bound."""
    if value <= 0:
        return 1024.0
    step = 1.0
    while step * 10 <= value:
        step *= 10
    for mult in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if step * mult >= value:
            return step * mult
    return step * 10


def _top_rounded(rect: QRectF, radius: float) -> QPainterPath:
    """A bar whose data-end is rounded but whose base sits flat on the axis."""
    radius = max(0.0, min(radius, rect.width() / 2, rect.height()))
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + radius)
    path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
    path.lineTo(rect.right() - radius, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path


class SpeedGraph(QWidget):
    """Live download/upload rate over the last two minutes."""

    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 62, 12, 14, 22

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.down: list[float] = []
        self.up: list[float] = []
        self.window_seconds = 120
        self.unit = "auto"

    def set_data(self, down: list[float], up: list[float]) -> None:
        self.down, self.up = down, up
        self.update()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        p.fillRect(rect, QColor(theme.SURFACE))

        plot = QRectF(
            self.PAD_LEFT, self.PAD_TOP,
            max(10, rect.width() - self.PAD_LEFT - self.PAD_RIGHT),
            max(10, rect.height() - self.PAD_TOP - self.PAD_BOTTOM),
        )
        peak = max([0.0] + self.down + self.up)
        top = _nice_ceiling(peak * 1.15)

        # ---- grid and value axis (recessive) --------------------------------
        p.setFont(QFont(self.font().family(), 8))
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4
            p.setPen(QPen(QColor(theme.GRID if i else theme.AXIS), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QPen(QColor(theme.MUTED)))
            p.drawText(
                QRectF(0, y - 9, self.PAD_LEFT - 9, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                format_rate(top * i / 4, self.unit) if i else "0",
            )

        # ---- time axis ------------------------------------------------------
        p.setPen(QPen(QColor(theme.MUTED)))
        p.drawText(QRectF(plot.left(), plot.bottom() + 3, 90, 16),
                   Qt.AlignLeft, f"-{self.window_seconds}s")
        p.drawText(QRectF(plot.right() - 60, plot.bottom() + 3, 60, 16),
                   Qt.AlignRight, "now")

        if not self.down and not self.up:
            p.setPen(QPen(QColor(theme.MUTED)))
            p.drawText(plot, Qt.AlignCenter, "Waiting for the first sample…")
            p.end()
            return

        for values, color in ((self.down, theme.DOWN), (self.up, theme.UP)):
            self._draw_series(p, plot, values, color, top)
        p.end()

    def _draw_series(self, p: QPainter, plot: QRectF, values: list[float],
                     color: str, top: float) -> None:
        if len(values) < 2:
            return
        n = self.window_seconds
        points: list[QPointF] = []
        offset = max(0, n - len(values))
        for i, v in enumerate(values[-n:]):
            x = plot.left() + plot.width() * (offset + i) / max(1, n - 1)
            y = plot.bottom() - plot.height() * min(1.0, v / top)
            points.append(QPointF(x, y))

        area = QPainterPath()
        area.moveTo(points[0].x(), plot.bottom())
        for pt in points:
            area.lineTo(pt)
        area.lineTo(points[-1].x(), plot.bottom())
        area.closeSubpath()

        brush_color = QColor(color)
        brush_color.setAlphaF(0.22)
        p.fillPath(area, QBrush(brush_color))

        line = QPainterPath()
        line.moveTo(points[0])
        for pt in points[1:]:
            line.lineTo(pt)
        p.strokePath(line, QPen(QColor(color), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))


class BarChart(QWidget):
    """Grouped download/upload bars for a bucketed period."""

    bar_hovered = Signal(int)

    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 68, 14, 18, 30

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.data: list[dict] = []
        self.unit = "auto"
        self._hover = -1
        self._geometry: list[QRectF] = []

    def set_data(self, data: list[dict], unit: str = "auto") -> None:
        self.data = data or []
        self.unit = unit
        self._hover = -1
        self.update()

    # ------------------------------------------------------------------ hover
    def mouseMoveEvent(self, event) -> None:
        idx = -1
        for i, rect in enumerate(self._geometry):
            if rect.contains(event.position()):
                idx = i
                break
        if idx != self._hover:
            self._hover = idx
            self.update()
        if idx >= 0:
            item = self.data[idx]
            QToolTip.showText(event.globalPosition().toPoint(), (
                f"<b>{item['sublabel']}</b><br>"
                f"<span style='color:{theme.DOWN}'>&#9660;</span> Download "
                f"{format_bytes(item['down'], self.unit)}<br>"
                f"<span style='color:{theme.UP}'>&#9650;</span> Upload "
                f"{format_bytes(item['up'], self.unit)}<br>"
                f"Total {format_bytes(item['down'] + item['up'], self.unit)}"
            ), self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        QToolTip.hideText()
        self.update()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        p.fillRect(rect, QColor(theme.SURFACE))

        plot = QRectF(
            self.PAD_LEFT, self.PAD_TOP,
            max(10, rect.width() - self.PAD_LEFT - self.PAD_RIGHT),
            max(10, rect.height() - self.PAD_TOP - self.PAD_BOTTOM),
        )
        self._geometry = []

        if not self.data:
            p.setPen(QPen(QColor(theme.MUTED)))
            p.drawText(plot, Qt.AlignCenter, "No data for this period yet.")
            p.end()
            return

        # Bars are drawn side by side, so the axis is scaled to the tallest
        # single bar rather than to the group total.
        peak = max(max(d["down"], d["up"]) for d in self.data)
        top = _nice_ceiling(max(peak, 1) * 1.12)

        p.setFont(QFont(self.font().family(), 8))
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4
            p.setPen(QPen(QColor(theme.GRID if i else theme.AXIS), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QPen(QColor(theme.MUTED)))
            p.drawText(QRectF(0, y - 9, self.PAD_LEFT - 10, 18),
                       Qt.AlignRight | Qt.AlignVCenter,
                       format_bytes(top * i / 4, self.unit) if i else "0")

        count = len(self.data)
        slot = plot.width() / count
        gap = 2.0                                   # surface gap between fills
        # Wider slots (few buckets) get chunkier bars, but never so wide that
        # the pair stops reading as two marks.
        max_w = 18.0 if count > 8 else 30.0
        bar_w = max(2.0, min(max_w, (slot - 10) / 2 - gap / 2))
        peak_idx = max(range(count), key=lambda i: self.data[i]["down"] + self.data[i]["up"])

        label_every = 1
        metrics = p.fontMetrics()
        widest = max(metrics.horizontalAdvance(d["label"]) for d in self.data)
        while slot * label_every < widest + 12:
            label_every += 1

        for i, item in enumerate(self.data):
            cx = plot.left() + slot * (i + 0.5)
            self._geometry.append(QRectF(plot.left() + slot * i, plot.top(),
                                         slot, plot.height()))
            if i == self._hover:
                p.fillRect(QRectF(plot.left() + slot * i, plot.top(), slot, plot.height()),
                           QColor(255, 255, 255, 10))

            for value, color, side in ((item["down"], theme.DOWN, -1),
                                       (item["up"], theme.UP, 1)):
                if value <= 0:
                    continue
                h = plot.height() * min(1.0, value / top)
                h = max(h, 2.0)
                x = cx + side * (gap / 2) - (bar_w if side < 0 else 0)
                bar = QRectF(x, plot.bottom() - h, bar_w, h)
                p.fillPath(_top_rounded(bar, 4), QBrush(QColor(color)))

            if i == peak_idx or i == self._hover:
                total = item["down"] + item["up"]
                if total > 0:
                    p.setPen(QPen(QColor(theme.TEXT if i == self._hover else theme.TEXT_SECONDARY)))
                    p.setFont(QFont(self.font().family(), 8, QFont.DemiBold))
                    tall = max(item["down"], item["up"])
                    y = plot.bottom() - plot.height() * min(1.0, tall / top) - 16
                    p.drawText(QRectF(cx - slot, max(plot.top() - 14, y), slot * 2, 14),
                               Qt.AlignCenter, format_bytes(total, self.unit))
                    p.setFont(QFont(self.font().family(), 8))

            if i % label_every == 0 or i == self._hover:
                p.setPen(QPen(QColor(theme.TEXT if i == self._hover else theme.MUTED)))
                p.drawText(QRectF(cx - slot * 1.5, plot.bottom() + 6, slot * 3, 16),
                           Qt.AlignCenter, item["label"])
        p.end()


class ShareBarDelegate(QStyledItemDelegate):
    """Paints a 0..1 share value (UserRole) as a thin proportional bar."""

    def __init__(self, color: str = theme.DOWN, parent=None) -> None:
        super().__init__(parent)
        self.color = color

    def paint(self, painter: QPainter, option, index) -> None:
        share = index.data(Qt.UserRole)
        if share is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(10, 0, -10, 0)
        h = 6.0
        y = rect.center().y() - h / 2
        track = QRectF(rect.left(), y, rect.width(), h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.GRID))
        painter.drawRoundedRect(track, 3, 3)
        width = max(0.0, min(1.0, float(share))) * rect.width()
        if width > 0:
            painter.setBrush(QColor(self.color))
            painter.drawRoundedRect(QRectF(rect.left(), y, max(width, 4.0), h), 3, 3)
        painter.restore()
