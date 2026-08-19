"""WAN IP chip display states.

The chip is the only place this feature surfaces, so what it says has to match
what is actually happening. Saying "unavailable" while a retry is already
scheduled reads as broken when it is merely busy — which is exactly what was
reported.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from netpulse.ui.widgets import WanIpChip           # noqa: E402


class FakeResolver:
    def __init__(self, address="", source="", checked_at=0.0, failures=0,
                 last_error=""):
        self.address = address
        self.source = source
        self.checked_at = checked_at
        self.failures = failures
        self.last_error = last_error


class ChipStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chip = WanIpChip()

    def text(self) -> str:
        return self.chip.value.text()

    def test_before_the_first_lookup_it_says_checking(self):
        self.chip.update_from(FakeResolver(), True)
        self.assertEqual(self.text(), "checking…")

    def test_a_resolved_address_is_shown(self):
        self.chip.update_from(
            FakeResolver("47.194.12.239", "ifconfig.me", time.time()), True)
        self.assertEqual(self.text(), "47.194.12.239")
        self.assertIn("ifconfig.me", self.chip.toolTip())

    def test_early_failures_read_as_retrying_not_unavailable(self):
        """The reported complaint: 'unavailable' during a normal retry window."""
        for failures in (1, 2):
            self.chip.update_from(
                FakeResolver(checked_at=time.time(), failures=failures,
                             last_error="ipify.org: URLError"), True)
            self.assertEqual(self.text(), "retrying…",
                             f"after {failures} failure(s) another attempt is "
                             "already scheduled")
            self.assertIn("trying again", self.chip.toolTip())

    def test_persistent_failure_does_say_unavailable(self):
        self.chip.update_from(
            FakeResolver(checked_at=time.time(), failures=5,
                         last_error="ipify.org: URLError"), True)
        self.assertEqual(self.text(), "unavailable")
        self.assertIn("URLError", self.chip.toolTip(),
                      "the reason must be readable, not just the verdict")

    def test_recovering_replaces_retrying_with_the_address(self):
        self.chip.update_from(
            FakeResolver(checked_at=time.time(), failures=2), True)
        self.assertEqual(self.text(), "retrying…")
        self.chip.update_from(
            FakeResolver("47.194.12.239", "ifconfig.me", time.time()), True)
        self.assertEqual(self.text(), "47.194.12.239")

    def test_switched_off_says_so(self):
        self.chip.update_from(FakeResolver("1.2.3.4", "x", time.time()), False)
        self.assertEqual(self.text(), "off")
        self.assertIn("Settings", self.chip.toolTip())

    def test_the_patience_threshold_is_shorter_than_the_backoff_ladder(self):
        """Otherwise it would sit on 'retrying…' long after it had given up."""
        from netpulse.collectors import wanip
        self.assertLess(WanIpChip.PATIENCE, len(wanip.RETRY_BACKOFF))


if __name__ == "__main__":
    unittest.main(verbosity=2)
