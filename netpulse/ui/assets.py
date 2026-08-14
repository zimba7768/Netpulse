"""Runtime-generated artwork.

Qt stylesheets can only reference images by file path, so the few pixel assets
the theme needs (a check mark, spin/combo arrows) are painted once at start-up
into the application data folder.  Nav icons are painted straight to QIcon.
"""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme
from .tray import app_pixmap

#: Sizes stored inside the .ico file handed to Windows shortcuts.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


# ---------------------------------------------------------------- stylesheet
def _check_pixmap(size: int = 16) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#ffffff"), max(2.0, size / 8))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    u = size / 16.0
    p.drawPolyline([QPointF(3.5 * u, 8.5 * u), QPointF(6.8 * u, 11.8 * u),
                    QPointF(12.5 * u, 4.8 * u)])
    p.end()
    return pixmap


def _arrow_pixmap(up: bool, size: int = 10, color: str = theme.TEXT_SECONDARY) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    pad = size * 0.22
    if up:
        path.moveTo(pad, size - pad)
        path.lineTo(size - pad, size - pad)
        path.lineTo(size / 2, pad)
    else:
        path.moveTo(pad, pad)
        path.lineTo(size - pad, pad)
        path.lineTo(size / 2, size - pad)
    path.closeSubpath()
    p.fillPath(path, QColor(color))
    p.end()
    return pixmap


def write_ico(target: Path) -> Path | None:
    """Write a real multi-resolution .ico so shortcuts show the app's own mark.

    Qt cannot save the ICO container, but the modern format is simply a small
    directory followed by PNG payloads, which Qt *can* produce.
    """
    payloads: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        # The QByteArray must outlive the QBuffer that writes into it — a
        # temporary here leaves the buffer pointing at freed memory.
        store = QByteArray()
        buffer = QBuffer(store)
        buffer.open(QBuffer.WriteOnly)
        ok = app_pixmap(size).save(buffer, "PNG")
        buffer.close()
        if not ok:
            return None
        payloads.append((size, bytes(store.data())))

    header = struct.pack("<HHH", 0, 1, len(payloads))     # reserved, type=icon, count
    offset = 6 + 16 * len(payloads)
    directory = b""
    body = b""
    for size, png in payloads:
        dimension = 0 if size >= 256 else size            # 0 means 256 in ICO
        directory += struct.pack(
            "<BBBBHHII",
            dimension, dimension,                         # width, height
            0, 0,                                         # palette size, reserved
            1, 32,                                        # colour planes, bits per pixel
            len(png), offset,
        )
        offset += len(png)
        body += png

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header + directory + body)
        return target
    except OSError:
        return None


def ensure_assets(folder: Path) -> dict[str, str]:
    """Write the stylesheet images and return {name: url-safe path}."""
    folder = Path(folder) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    items = {
        "check": _check_pixmap(),
        "arrow_up": _arrow_pixmap(True),
        "arrow_down": _arrow_pixmap(False),
    }
    paths = {}
    for name, pixmap in items.items():
        target = folder / f"{name}.png"
        pixmap.save(str(target), "PNG")
        paths[name] = target.as_posix()
    return paths


# ---------------------------------------------------------------- nav icons
def nav_icon(kind: str, color: str, size: int = 18) -> QIcon:
    pixmap = QPixmap(size * 2, size * 2)      # 2x for crisp scaling
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    s = size * 2
    c = QColor(color)
    p.setPen(Qt.NoPen)
    p.setBrush(c)

    if kind == "dashboard":
        gap = s * 0.10
        cell = (s - gap) / 2
        for row in range(2):
            for col in range(2):
                h = cell * (0.72 if (row, col) in ((0, 1), (1, 0)) else 1.0)
                p.drawRoundedRect(
                    QRectF(col * (cell + gap), row * (cell + gap) + (cell - h),
                           cell, h), s * 0.07, s * 0.07)
    elif kind == "history":
        widths = s * 0.20
        for i, factor in enumerate((0.42, 0.72, 1.0)):
            x = i * (s - widths) / 2
            p.drawRoundedRect(QRectF(x, s * (1 - factor), widths, s * factor),
                              s * 0.06, s * 0.06)
    elif kind == "applications":
        rows = 3
        h = s * 0.20
        gap = (s - rows * h) / (rows - 1)
        for i in range(rows):
            y = i * (h + gap)
            p.drawEllipse(QRectF(0, y, h, h))
            p.drawRoundedRect(QRectF(h * 1.5, y + h * 0.18, s - h * 1.5, h * 0.64),
                              h * 0.3, h * 0.3)
    elif kind == "files":
        fold = s * 0.34
        path = QPainterPath()
        path.moveTo(s * 0.14, 0)
        path.lineTo(s * 0.86 - fold, 0)
        path.lineTo(s * 0.86, fold)
        path.lineTo(s * 0.86, s)
        path.lineTo(s * 0.14, s)
        path.closeSubpath()
        p.fillPath(path, c)
        corner = QPainterPath()
        corner.moveTo(s * 0.86 - fold, 0)
        corner.lineTo(s * 0.86, fold)
        corner.lineTo(s * 0.86 - fold, fold)
        corner.closeSubpath()
        p.fillPath(corner, QColor(theme.SIDEBAR))
    elif kind == "settings":
        cx = cy = s / 2
        body = s * 0.33          # radius of the gear body
        tooth_w, tooth_h = s * 0.15, s * 0.19
        p.save()
        p.translate(cx, cy)
        for i in range(8):
            p.save()
            p.rotate(i * 45)
            p.drawRoundedRect(
                QRectF(-tooth_w / 2, -body - tooth_h * 0.55, tooth_w, tooth_h),
                s * 0.035, s * 0.035)
            p.restore()
        p.drawEllipse(QPointF(0, 0), body, body)
        p.setBrush(QColor(theme.SIDEBAR))
        p.drawEllipse(QPointF(0, 0), body * 0.42, body * 0.42)
        p.restore()
    p.end()
    return QIcon(pixmap)
