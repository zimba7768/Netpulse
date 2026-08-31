"""Render every page offscreen with seeded demo data, for visual review.

    QT_QPA_PLATFORM=offscreen python tools/screenshot.py [outdir]

Not part of the application — this exists so the interface can be inspected
without a desktop session.
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMP = Path(tempfile.gettempdir())
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else TEMP / "netpulse-shots"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NETPULSE_DATA_DIR", str(TEMP / "netpulse-demo"))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from netpulse.config import Settings, data_dir, db_path  # noqa: E402
from netpulse.db import Database  # noqa: E402
from netpulse.engine import Engine  # noqa: E402
from netpulse.ui import theme  # noqa: E402
from netpulse.ui.assets import ensure_assets  # noqa: E402
from netpulse.ui.main_window import MainWindow  # noqa: E402

APPS = [
    ("chrome.exe", 0.30), ("steam.exe", 0.22), ("Spotify.exe", 0.10),
    ("msedge.exe", 0.09), ("Discord.exe", 0.07), ("OneDrive.exe", 0.06),
    ("Code.exe", 0.05), ("svchost.exe", 0.04), ("qbittorrent.exe", 0.04),
    ("Teams.exe", 0.03),
]

#: Roughly a third of the demo traffic is shown as having gone through a tunnel.
VPN_SHARE = 0.35

FILES = [
    ("Windows11_23H2.iso", 5_368_709_120, "software-download.microsoft.com", "Chrome"),
    ("Cyberpunk2077-Patch.pkg", 2_147_483_648, "steamcontent.com", None),
    ("annual-report-2026.pdf", 4_812_004, "investors.example.com", "Edge"),
    ("holiday-photos.zip", 812_004_233, "drive.google.com", "Chrome"),
    ("blender-4.5-windows.msi", 312_004_233, "mirror.blender.org", "Firefox"),
    ("dataset-q2.csv", 48_120_042, "data.example.gov", "Chrome"),
    ("podcast-ep-214.mp3", 92_120_042, "cdn.podcast.fm", None),
    ("invoice-8841.pdf", 212_004, "billing.example.com", "Edge"),
    ("ubuntu-26.04-desktop.iso", 4_612_004_233, "releases.ubuntu.com", "Firefox"),
    ("driver-nvidia-580.exe", 712_004_233, "us.download.nvidia.com", "Chrome"),
    ("project-backup.7z", 1_912_004_233, None, None),
    ("meeting-recording.mp4", 412_004_233, "teams.microsoft.com", "Teams.exe"),
]


def seed(db: Database) -> None:
    random.seed(7)
    now = datetime.now()
    start = now - timedelta(days=400)

    day = start
    while day <= now:
        weekend = day.weekday() >= 5
        base = random.uniform(1.4, 3.2) * (1.9 if weekend else 1.0)
        for hour in range(24):
            if day.date() == now.date() and hour > now.hour:
                break
            # a plausible daily rhythm: quiet overnight, evening peak
            shape = 0.12 + 0.88 * max(0.0, math.sin((hour - 6) / 18 * math.pi)) ** 1.6
            if hour in (20, 21, 22):
                shape *= 1.6
            volume = base * shape * random.uniform(0.55, 1.5) * 1024 ** 3 / 24
            if random.random() < 0.05:
                volume *= 4                      # the occasional big download
            down = int(volume)
            up = int(volume * random.uniform(0.05, 0.14))
            ts = datetime(day.year, day.month, day.day, hour,
                          random.randint(0, 59)).timestamp()
            if ts > time.time():
                continue
            for link, weight in (("direct", 1.0), ("vpn", VPN_SHARE)):
                ldown, lup = int(down * weight), int(up * weight)
                per_app = {}
                for name, share in APPS:
                    jitter = random.uniform(0.4, 1.7)
                    per_app[name] = (int(ldown * share * jitter * 0.9),
                                     int(lup * share * jitter * 0.9))
                db.add_traffic(ldown, lup, per_app, ts=ts, link=link)
        day += timedelta(days=1)

    db.rollup(since=0)

    folder = str(Path.home() / "Downloads")
    for i, (name, size, source, app) in enumerate(FILES):
        db.add_file(f"{folder}/{name}", name, folder, size, "down", source, app,
                    ts=time.time() - i * random.uniform(3600, 90000),
                    link="vpn" if i % 3 == 0 else "direct")


def main() -> int:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(str(db_path()) + suffix)
        except OSError:
            pass

    app = QApplication(sys.argv)
    app.setStyleSheet(theme.build_stylesheet(ensure_assets(data_dir())))

    settings = Settings()
    settings.update({"watch_folders": [str(Path.home() / "Downloads")],
                     "units": "auto"})
    db = Database(db_path())
    seed(db)

    now_stamp = time.time()
    engine = Engine(db, settings)
    # The demo data includes per-application figures, so present the collector
    # as healthy — otherwise the Applications page shows its "unavailable"
    # banner, which is misleading in a screenshot.
    engine.etw.available = True
    engine.etw.events_seen = 1
    # A documentation-range address (RFC 5737), never a real one — these
    # images end up in the README.
    engine.wan.address = "203.0.113.42"
    engine.wan.source = "ipify.org"
    engine.wan.checked_at = now_stamp

    # Synthetic live trace so the speed graph has something to draw.
    random.seed(3)
    now = time.time()
    level = 900_000.0
    for i in range(120):
        level = max(40_000.0, level + random.uniform(-260_000, 300_000))
        burst = 2_400_000 if 70 < i < 96 else 0
        engine.live.append((now - (120 - i),
                            level + burst, level * random.uniform(0.06, 0.2),
                            level * VPN_SHARE, level * VPN_SHARE * 0.12))

    window = MainWindow(db, engine, settings)
    window.resize(1280, 840)
    window.show()
    QCoreApplication.processEvents()

    pages = [(0, "dashboard"), (1, "history"), (2, "applications"),
             (3, "files"), (5, "settings")]
    for index, name in pages:
        window.nav_group.button(index).setChecked(True)
        window.stack.setCurrentIndex(index)
        window.refresh_current()
        if name == "dashboard":
            window.dashboard.refresh_live()
        for _ in range(6):
            QCoreApplication.processEvents()
        window.grab().save(str(OUT / f"{name}.png"))
        print("wrote", OUT / f"{name}.png")

    # every history period, since each has its own axis behaviour
    for period in ("hour", "day", "week", "month", "year"):
        window.nav_group.button(1).setChecked(True)
        window.stack.setCurrentIndex(1)
        window.history.set_period(period)
        for _ in range(6):
            QCoreApplication.processEvents()
        window.grab().save(str(OUT / f"history-{period}.png"))
        print("wrote", OUT / f"history-{period}.png")

    # the VPN tab, one image per section
    for key, _label in [("overview", ""), ("history", ""),
                        ("apps", ""), ("files", "")]:
        window.nav_group.button(4).setChecked(True)
        window.stack.setCurrentIndex(4)
        window.vpn.show_section(key)
        if key == "overview":
            window.vpn.refresh_live()
        for _ in range(6):
            QCoreApplication.processEvents()
        window.grab().save(str(OUT / f"vpn-{key}.png"))
        print("wrote", OUT / f"vpn-{key}.png")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
