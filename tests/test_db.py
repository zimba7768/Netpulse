"""Storage tests: bucket alignment, rollup correctness, retention, queries."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netpulse.db import (DAY, HOUR, MINUTE, SYSTEM, Database, floor_day,
                         floor_hour, floor_minute)
from netpulse.units import format_bytes, format_rate


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        # tempfile, not a hard-coded "/tmp": on Windows that resolves to
        # C:\tmp, which does not exist, and SQLite cannot create the file.
        self.dir = tempfile.mkdtemp(prefix="netpulse-test-")
        self.path = os.path.join(self.dir, f"netpulse_{time.time_ns()}.db")
        self.db = Database(self.path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------- buckets
    def test_bucket_alignment(self):
        ts = datetime(2026, 3, 14, 15, 9, 26).timestamp()
        self.assertEqual(datetime.fromtimestamp(floor_minute(ts)).second, 0)
        hour = datetime.fromtimestamp(floor_hour(ts))
        self.assertEqual((hour.minute, hour.second), (0, 0))
        self.assertEqual(hour.hour, 15)
        day = datetime.fromtimestamp(floor_day(ts))
        self.assertEqual((day.hour, day.minute, day.second), (0, 0, 0))
        self.assertEqual(day.day, 14)

    # ------------------------------------------------------------- rollups
    def test_rollup_sums_minutes_into_hours_and_days(self):
        base = floor_hour(time.time())
        for i in range(10):
            self.db.add_traffic(100, 50, {"chrome.exe": (80, 40)}, ts=base + i * 60)
        self.db.rollup(since=0)

        down, up = self.db.totals(base, base + 3600, SYSTEM, HOUR)
        self.assertEqual((down, up), (1000, 500))

        day = floor_day(base)
        down, up = self.db.totals(day, day + 86400, SYSTEM, DAY)
        self.assertEqual((down, up), (1000, 500))

        down, up = self.db.totals(day, day + 86400, "chrome.exe", DAY)
        self.assertEqual((down, up), (800, 400))

    def test_rollup_covers_a_gap_left_by_sleep_or_a_stopped_process(self):
        """The incremental rollup must not lose samples older than its window.

        If the machine sleeps for hours, the minute rows written just before it
        went under are older than the fixed look-back by the time the next
        rollup runs. Anything not folded into the hour and day buckets by then
        would disappear from every total built on them.
        """
        long_ago = time.time() - 9 * 3600
        self.db.add_traffic(5_000_000, 500_000, ts=long_ago)
        # Pretend a rollup last completed just after that sample was written.
        self.db.set_meta("last_rollup", str(int(long_ago) + 30))

        self.db.rollup()                       # the ordinary periodic call

        day = floor_day(long_ago)
        self.assertEqual(self.db.totals(day, day + 86400, SYSTEM, DAY),
                         (5_000_000, 500_000),
                         "traffic from before the look-back window was lost")

    def test_rollup_records_its_own_high_water_mark(self):
        self.assertIsNone(self.db.get_meta("last_rollup"))
        self.db.rollup()
        stamp = self.db.get_meta("last_rollup")
        self.assertIsNotNone(stamp)
        self.assertAlmostEqual(float(stamp), time.time(), delta=30)

    def test_rollup_is_idempotent(self):
        base = floor_hour(time.time())
        self.db.add_traffic(500, 250, ts=base + 30)
        for _ in range(5):
            self.db.rollup(since=0)
        down, up = self.db.totals(base, base + 3600, SYSTEM, HOUR)
        self.assertEqual((down, up), (500, 250), "repeated rollups must not accumulate")

    def test_samples_within_a_minute_accumulate(self):
        ts = floor_minute(time.time())
        for _ in range(4):
            self.db.add_traffic(25, 5, ts=ts + 1)
        self.db.rollup(since=0)
        down, up = self.db.totals(floor_hour(ts), floor_hour(ts) + 3600, SYSTEM, HOUR)
        self.assertEqual((down, up), (100, 20))

    # ----------------------------------------------------------- retention
    def test_prune_keeps_rolled_up_history(self):
        old = time.time() - 30 * 86400
        self.db.add_traffic(1000, 100, ts=old)
        self.db.rollup(since=0)
        self.db.prune(minute_days=7, hour_days=90, day_days=0)

        self.assertEqual(
            self.db.totals(floor_day(old), floor_day(old) + 86400, SYSTEM, MINUTE),
            (0, 0), "minute detail older than the window should be gone")
        self.assertEqual(
            self.db.totals(floor_day(old), floor_day(old) + 86400, SYSTEM, DAY),
            (1000, 100), "daily totals must survive pruning")

    # -------------------------------------------------------------- series
    def test_hourly_series_has_one_slot_per_hour(self):
        now = time.time()
        self.db.add_traffic(700, 300, ts=now - 3600)
        self.db.rollup(since=0)
        data = self.db.series("hour")
        self.assertEqual(len(data), 24)
        self.assertEqual(sum(d["down"] for d in data), 700)
        self.assertEqual(sum(d["up"] for d in data), 300)
        self.assertTrue(all("label" in d and "sublabel" in d for d in data))

    def test_daily_series_covers_the_requested_window(self):
        for days_ago in range(5):
            self.db.add_traffic(100 * (days_ago + 1), 10,
                                ts=time.time() - days_ago * 86400)
        self.db.rollup(since=0)
        data = self.db.series("day", count=7)
        self.assertEqual(len(data), 7)
        self.assertEqual(sum(d["down"] for d in data), 100 + 200 + 300 + 400 + 500)

    def test_monthly_series_buckets_by_calendar_month(self):
        now = datetime.now()
        this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        last_month = (this_month - timedelta(days=1)).replace(day=1, hour=12)
        self.db.add_traffic(1000, 0, ts=this_month.timestamp())
        self.db.add_traffic(2000, 0, ts=last_month.timestamp())
        self.db.rollup(since=0)
        data = self.db.series("month", count=3)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[-1]["down"], 1000, "current month is the last bucket")
        self.assertEqual(data[-2]["down"], 2000, "previous month precedes it")

    def test_weekly_and_yearly_series_totals_match(self):
        self.db.add_traffic(4096, 1024, ts=time.time())
        self.db.rollup(since=0)
        for period in ("week", "year"):
            data = self.db.series(period)
            self.assertEqual(sum(d["down"] for d in data), 4096, period)
            self.assertEqual(sum(d["up"] for d in data), 1024, period)

    def test_period_totals_agree_with_series(self):
        self.db.add_traffic(3000, 1500, ts=time.time())
        self.db.rollup(since=0)
        for period in ("day", "week", "month", "year"):
            down, up = self.db.totals_for_period(period)
            self.assertEqual((down, up), (3000, 1500), period)

    # ---------------------------------------------------------------- apps
    def test_top_apps_ranks_by_total_traffic(self):
        now = time.time()
        self.db.add_traffic(0, 0, {"steam.exe": (5000, 100)}, ts=now)
        self.db.add_traffic(0, 0, {"chrome.exe": (900, 50)}, ts=now)
        self.db.add_traffic(0, 0, {"chrome.exe": (100, 50)}, ts=now)
        self.db.rollup(since=0)
        rows = self.db.apps_for_period("day")
        self.assertEqual([r["app"] for r in rows], ["steam.exe", "chrome.exe"])
        self.assertEqual(rows[1]["down"], 1000)

    def test_system_total_is_not_counted_as_an_application(self):
        self.db.add_traffic(999, 1, {"edge.exe": (10, 1)}, ts=time.time())
        self.db.rollup(since=0)
        self.assertNotIn("", [r["app"] for r in self.db.apps_for_period("day")])

    # --------------------------------------------------------------- files
    def test_file_log_deduplicates(self):
        self.assertTrue(self.db.add_file("C:/D/a.zip", "a.zip", "C:/D", 500))
        self.assertFalse(self.db.add_file("C:/D/a.zip", "a.zip", "C:/D", 500))
        self.assertEqual(len(self.db.recent_files()), 1)

    def test_file_source_enrichment(self):
        self.db.add_file("C:/D/b.iso", "b.iso", "C:/D", 900)
        self.db.enrich_file_source("C:/D/b.iso", "releases.example.com", "Chrome")
        row = self.db.recent_files()[0]
        self.assertEqual(row["source"], "releases.example.com")
        self.assertEqual(row["app"], "Chrome")
        self.assertEqual(row["ext"], "iso")

    def test_file_search_and_stats(self):
        self.db.add_file("C:/D/report.pdf", "report.pdf", "C:/D", 100)
        self.db.add_file("C:/D/movie.mkv", "movie.mkv", "C:/D", 900)
        self.assertEqual(len(self.db.recent_files(search="report")), 1)
        self.assertEqual(self.db.file_stats()["bytes"], 1000)


class UnitTests(unittest.TestCase):
    def test_format_bytes_scales(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(format_bytes(2 * 1024 ** 3), "2.00 GB")

    def test_forced_units(self):
        self.assertEqual(format_bytes(1024 ** 2, "KB"), "1,024.00 KB")
        self.assertEqual(format_bytes(1024 ** 2, "MB"), "1.00 MB")

    def test_rate_suffix(self):
        self.assertTrue(format_rate(2048).endswith("/s"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
