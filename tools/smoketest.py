"""End-to-end check: run the real engine against real traffic for a few seconds.

    python tools/smoketest.py

Starts the collection engine, generates network activity, and verifies that the
sampled bytes reached the database and survive rollup into every bucket the
interface queries.  Also exercises the file tracker by writing a file into a
watched folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="netpulse-smoke-"))
WATCH = WORK / "Downloads"
WATCH.mkdir()
os.environ["NETPULSE_DATA_DIR"] = str(WORK / "data")

from PySide6.QtCore import Qt  # noqa: E402

from netpulse.config import Settings, db_path  # noqa: E402
from netpulse.db import Database  # noqa: E402
from netpulse.engine import Engine  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def generate_traffic() -> None:
    """Pull a few packages so the adapter counters actually move."""
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--no-deps", "--quiet",
         "--dest", str(WORK / "pkgs"), "requests", "urllib3"],
        capture_output=True, timeout=120,
    )


def main() -> int:
    print(f"workspace: {WORK}")
    settings = Settings()
    settings.update({
        "watch_folders": [str(WATCH)],
        "track_per_app": True,
        "track_files": True,
        "read_browser_history": False,
        "min_file_bytes": 1024,
        "sample_interval_ms": 500,
    })
    db = Database(db_path())
    engine = Engine(db, settings)

    # Direct connection: there is no Qt event loop here, so a queued delivery
    # would never arrive.  The GUI receives the same signal through the loop.
    seen_files: list[dict] = []
    engine.file_found.connect(seen_files.append, Qt.DirectConnection)

    engine.start()
    print(f"  engine status: {engine.status_text()}")
    time.sleep(1.0)

    generate_traffic()

    # a file arriving in a watched folder
    payload = os.urandom(400_000)
    (WATCH / "smoketest-payload.bin").write_bytes(payload)
    time.sleep(6.0)

    engine.stop()
    db.rollup(since=0)

    ticks = len(engine.live)
    check("engine sampled repeatedly", ticks >= 5, f"{ticks} samples")
    check("session counters moved", engine.session_down > 0,
          f"down {engine.session_down:,} B / up {engine.session_up:,} B")

    day_down, day_up = db.totals_for_period("day")
    check("today's totals were written", day_down > 0, f"{day_down:,} B down")
    check("upload was recorded too", day_up > 0, f"{day_up:,} B up")

    for period in ("hour", "day", "week", "month", "year"):
        data = db.series(period)
        total = sum(d["down"] for d in data)
        check(f"{period:<5} series carries the traffic", total >= day_down * 0.99,
              f"{total:,} B across {len(data)} buckets")

    hour_total = sum(d["down"] for d in db.series("hour"))
    day_total = sum(d["down"] for d in db.series("day"))
    check("hourly and daily views agree", abs(hour_total - day_total) == 0,
          f"{hour_total:,} vs {day_total:,}")

    apps = db.apps_for_period("day")
    if engine.per_app_available:
        check("per-application rows exist", bool(apps), f"{len(apps)} apps")
    else:
        print(f"  [SKIP] per-application tracking — {engine.per_app_reason}")

    logged = db.recent_files()
    check("watched folder file was logged",
          any(f["name"] == "smoketest-payload.bin" for f in logged),
          f"{len(logged)} file(s)")
    check("file size recorded correctly",
          any(f["size"] == len(payload) for f in logged))
    check("new-file signal fired", bool(seen_files), f"{len(seen_files)} signal(s)")

    stats = db.stats()
    check("database stayed small", stats["db_bytes"] < 2_000_000,
          f"{stats['db_bytes']:,} B for {stats['traffic_rows']:,} rows")

    db.close()
    shutil.rmtree(WORK, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
