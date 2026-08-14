"""Notification-area icon: live speed at a glance plus a small control menu."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..units import format_rate
from . import theme


def _arrow(path: QPainterPath, cx: float, top: float, bottom: float,
           half_width: float, pointing_down: bool) -> None:
    if pointing_down:
        path.moveTo(cx - half_width, top)
        path.lineTo(cx + half_width, top)
        path.lineTo(cx, bottom)
    else:
        path.moveTo(cx - half_width, bottom)
        path.lineTo(cx + half_width, bottom)
        path.lineTo(cx, top)
    path.closeSubpath()


#: Sizes Windows asks for: 16 in title bars, 24/32 on the taskbar, 48 in
#: alt-tab, 256 in Explorer's extra-large view. Supplying each one explicitly
#: avoids the soft, downscaled look of a single large pixmap.
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

#: The mark, described on a 64-unit grid: (down centre, up centre, half width,
#: top, bottom).  The two arrows are separated by a 2-unit gap so they read as
#: a pair rather than one zigzag — the same rule the charts use between
#: adjacent fills.  Small sizes get a fatter, taller variant because at 16px
#: the slender version dissolves into anti-aliasing.
_GEOMETRY_SMALL = (18.5, 45.5, 12.5, 4.0, 60.0)
_GEOMETRY_NORMAL = (20.0, 44.0, 11.0, 8.0, 56.0)


def _paint_mark(p: QPainter, size: int, down_alpha: float = 1.0,
                up_alpha: float = 1.0) -> None:
    """Draw the NetPulse mark into an already-open painter."""
    unit = size / 64.0
    left, right, half, top, bottom = (
        _GEOMETRY_SMALL if size < 24 else _GEOMETRY_NORMAL)

    for cx, colour, alpha, pointing_down in (
        (left, theme.DOWN, down_alpha, True),
        (right, theme.UP, up_alpha, False),
    ):
        path = QPainterPath()
        _arrow(path, cx * unit, top * unit, bottom * unit, half * unit,
               pointing_down)
        c = QColor(colour)
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        p.fillPath(path, c)


def app_pixmap(size: int = 64) -> QPixmap:
    """The application mark, identical to the tray icon at rest.

    Drawn on transparency rather than on a tile, so it sits equally well on a
    light or dark taskbar and matches the notification area exactly.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    _paint_mark(p, size)
    p.end()
    return pixmap


def app_icon() -> QIcon:
    """Multi-resolution application icon."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(app_pixmap(size))
    return icon


def _intensity(rate: float) -> float:
    if rate <= 0:
        return 0.30
    # 0 .. 1 over roughly 0 - 2 MB/s, eased so small transfers still show
    return 0.45 + 0.55 * min(1.0, (rate / (2 * 1024 * 1024)) ** 0.4)


def speed_icon(down_rate: float, up_rate: float, size: int = 64) -> QIcon:
    """The same mark, with each arrow brightened by its own current rate."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    _paint_mark(p, size, _intensity(down_rate), _intensity(up_rate))
    p.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    def __init__(self, window, engine, settings, parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.engine = engine
        self.settings = settings
        self._last_icon: tuple[int, int] = (-1, -1)

        self.setIcon(app_icon())
        self.setToolTip("NetPulse")

        menu = QMenu()
        self.show_action = QAction("Open NetPulse", self)
        self.show_action.triggered.connect(self.window.show_from_tray)
        menu.addAction(self.show_action)

        self.pause_action = QAction("Pause recording", self)
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(bool(settings.get("paused")))
        self.pause_action.toggled.connect(self._toggle_pause)
        menu.addAction(self.pause_action)

        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.window.quit_application)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _toggle_pause(self, paused: bool) -> None:
        self.engine.set_paused(paused)
        self.pause_action.setText("Resume recording" if paused else "Pause recording")

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.show_from_tray()

    def update_rates(self, down: float, up: float, unit: str = "auto") -> None:
        self.setToolTip(
            f"NetPulse\nDownload {format_rate(down, unit)}\nUpload {format_rate(up, unit)}")
        # Repaint only when the displayed intensity would actually change.
        key = (int(min(down, 4e6) // 65536), int(min(up, 4e6) // 65536))
        if key != self._last_icon:
            self._last_icon = key
            self.setIcon(speed_icon(down, up))
