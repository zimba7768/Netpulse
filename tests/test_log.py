"""The rolling diagnostic log.

It exists for faults that only appear after hours of uptime, so its two
obligations are to never lose the interesting part and to never break the
program it is observing.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from netpulse.collectors import wanip           # noqa: E402
from netpulse.log import NULL_LOG, RollingLog   # noqa: E402


class RollingLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="netpulse-log-")
        self.path = Path(self.dir) / "test.log"

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_lines_are_stamped_and_kept(self) -> None:
        log = RollingLog(self.path)
        log.write("first thing")
        log.write("second thing")
        text = log.read()
        self.assertIn("first thing", text)
        self.assertIn("second thing", text)
        self.assertEqual(len(text.strip().splitlines()), 2)

    def test_it_stays_under_its_ceiling(self) -> None:
        log = RollingLog(self.path, max_bytes=4_000)
        for i in range(2_000):
            log.write(f"entry {i} " + "x" * 60)
        self.assertLess(self.path.stat().st_size, 10_000)

    def test_trimming_keeps_the_most_recent_entries(self) -> None:
        # The end of the log is where the fault is; the beginning is history.
        log = RollingLog(self.path, max_bytes=4_000)
        for i in range(2_000):
            log.write(f"entry {i}")
        text = log.read()
        self.assertIn("entry 1999", text)
        self.assertNotIn("entry 0 ", text)
        self.assertIn("trimmed", text)

    def test_it_never_opens_mid_line(self) -> None:
        log = RollingLog(self.path, max_bytes=2_000)
        for i in range(1_000):
            log.write(f"entry {i} " + "y" * 40)
        for line in log.read().splitlines()[1:]:
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} ",
                             "a trimmed line was left half-written")

    def test_an_unwritable_path_is_survivable(self) -> None:
        # Logging must never be the thing that takes the program down.
        blocker = Path(self.dir) / "a-file"
        blocker.write_text("not a directory")
        log = RollingLog(blocker / "inside" / "x.log")
        log.write("still fine")          # must not raise
        self.assertEqual(log.read(), "")

    def test_the_null_log_discards_quietly(self) -> None:
        NULL_LOG.write("goes nowhere")
        self.assertEqual(NULL_LOG.read(), "")


class ResolverLoggingTests(unittest.TestCase):
    """The events that matter when reading a log after the fact."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="netpulse-rlog-")
        self.log = RollingLog(Path(self.dir) / "wanip.log")
        self.resolver = wanip.WanIpResolver(log=self.log)
        self._fetch = wanip.fetch_text
        self._fingerprint = wanip.network_fingerprint

    def tearDown(self) -> None:
        wanip.fetch_text = self._fetch
        wanip.network_fingerprint = self._fingerprint
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_successful_first_lookup_is_recorded(self) -> None:
        wanip.fetch_text = lambda url, timeout=None: "82.26.195.54"
        self.resolver.check()
        self.assertIn("82.26.195.54", self.log.read())

    def test_a_failure_records_every_provider_and_its_cause(self) -> None:
        def refuse(url, timeout=None):
            raise urllib.error.URLError(
                ConnectionRefusedError(10061, "actively refused it"))
        wanip.fetch_text = refuse
        self.resolver.check()
        text = self.log.read()
        self.assertIn("lookup FAILED", text)
        self.assertIn("ConnectionRefusedError", text)
        self.assertIn("10061", text)
        for _url, name in wanip.ENDPOINTS:
            self.assertIn(name, text)

    def test_a_vpn_connecting_is_recorded_by_name(self) -> None:
        self.resolver.fingerprint = (("Ethernet", ("192.168.50.10",)),)
        wanip.network_fingerprint = lambda: (
            ("Ethernet", ("192.168.50.10",)),
            ("SurfsharkWireGuard", ("10.14.0.2",)))
        self.resolver._watch_network()
        text = self.log.read()
        self.assertIn("network changed", text)
        self.assertIn("SurfsharkWireGuard", text)

    def test_a_quiet_success_does_not_fill_the_log(self) -> None:
        # Hours of normal running must not push the interesting part out.
        wanip.fetch_text = lambda url, timeout=None: "82.26.195.54"
        for _ in range(50):
            self.resolver.check()
        self.assertLessEqual(len(self.log.read().strip().splitlines()), 2,
                             "an unchanged address should not be logged again")

    def test_an_unexpected_loop_error_is_recorded(self) -> None:
        self.resolver._record_loop_error(RuntimeError("boom"))
        self.assertIn("UNEXPECTED", self.log.read())
        self.assertIn("boom", self.log.read())


if __name__ == "__main__":
    unittest.main()
