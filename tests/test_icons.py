"""Icon tests.

The taskbar icon is easy to get subtly wrong — an .ico with bad offsets, or a
mark that collapses to a smudge at 16px — and neither failure is obvious until
it is on someone's taskbar. These checks parse the generated container byte for
byte and look at the actual pixels.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication

_app = QGuiApplication.instance() or QGuiApplication([])

from netpulse.ui.assets import ICO_SIZES, write_ico          # noqa: E402
from netpulse.ui.tray import ICON_SIZES, app_icon, app_pixmap  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class PixmapTests(unittest.TestCase):
    def test_every_size_renders_something_visible(self):
        for size in ICON_SIZES:
            pixmap = app_pixmap(size)
            self.assertEqual((pixmap.width(), pixmap.height()), (size, size))
            image = pixmap.toImage()
            opaque = sum(
                1
                for y in range(size)
                for x in range(size)
                if image.pixelColor(x, y).alpha() > 128
            )
            coverage = opaque / (size * size)
            self.assertGreater(coverage, 0.10,
                               f"{size}px icon is nearly blank ({coverage:.0%})")
            self.assertLess(coverage, 0.85,
                            f"{size}px icon is an unreadable blob ({coverage:.0%})")

    def test_both_series_colours_are_present(self):
        """Identity comes from the blue/orange pair — neither may vanish."""
        for size in (16, 32, 64):
            image = app_pixmap(size).toImage()
            blues = oranges = 0
            for y in range(size):
                for x in range(size):
                    colour = image.pixelColor(x, y)
                    if colour.alpha() < 128:
                        continue
                    if colour.blue() > colour.red():
                        blues += 1
                    elif colour.red() > colour.blue():
                        oranges += 1
            self.assertGreater(blues, 0, f"download arrow missing at {size}px")
            self.assertGreater(oranges, 0, f"upload arrow missing at {size}px")

    def test_background_is_transparent(self):
        """It has to sit on a light or dark taskbar equally well."""
        image = app_pixmap(64).toImage()
        for corner in ((0, 0), (63, 0), (0, 63), (63, 63)):
            self.assertEqual(image.pixelColor(*corner).alpha(), 0,
                             "corners must be transparent, not tiled")

    def test_icon_advertises_multiple_resolutions(self):
        sizes = {(s.width(), s.height()) for s in app_icon().availableSizes()}
        self.assertIn((16, 16), sizes)
        self.assertIn((32, 32), sizes)
        self.assertIn((256, 256), sizes)


class IcoContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = (Path(tempfile.gettempdir())
                     / f"netpulse-test-{os.getpid()}.ico")
        self.assertIsNotNone(write_ico(self.path), "write_ico reported failure")
        self.blob = self.path.read_bytes()

    def tearDown(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def test_header_is_a_valid_icon_directory(self):
        reserved, kind, count = struct.unpack("<HHH", self.blob[:6])
        self.assertEqual(reserved, 0)
        self.assertEqual(kind, 1, "type 1 = icon (2 would be a cursor)")
        self.assertEqual(count, len(ICO_SIZES))

    def test_entries_point_at_real_png_payloads(self):
        count = struct.unpack("<H", self.blob[4:6])[0]
        seen = []
        for i in range(count):
            start = 6 + 16 * i
            (width, height, palette, reserved, planes, bpp, length,
             offset) = struct.unpack("<BBBBHHII", self.blob[start:start + 16])
            self.assertEqual((palette, reserved), (0, 0))
            self.assertEqual((planes, bpp), (1, 32))
            self.assertEqual(width, height, "icons must be square")

            self.assertLessEqual(offset + length, len(self.blob),
                                 "entry points past the end of the file")
            payload = self.blob[offset:offset + length]
            self.assertTrue(payload.startswith(PNG_MAGIC), "payload is not a PNG")

            # The PNG's own IHDR must agree with the directory entry.
            png_width, png_height = struct.unpack(">II", payload[16:24])
            self.assertEqual(png_width, png_height)
            self.assertEqual(png_width, 256 if width == 0 else width,
                             "directory entry disagrees with the PNG header")
            seen.append(png_width)

        self.assertEqual(sorted(seen), sorted(ICO_SIZES))
        self.assertIn(256, seen, "Explorer's extra-large view needs 256px")

    def test_payloads_do_not_overlap(self):
        count = struct.unpack("<H", self.blob[4:6])[0]
        spans = []
        for i in range(count):
            start = 6 + 16 * i
            length, offset = struct.unpack("<II", self.blob[start + 8:start + 16])
            spans.append((offset, offset + length))
        spans.sort()
        self.assertGreaterEqual(spans[0][0], 6 + 16 * count,
                                "first payload overlaps the directory")
        for (_, end), (nxt, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, nxt, "payloads overlap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
