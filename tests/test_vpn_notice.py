"""The dashboard's explanation of itself during a VPN session.

The main dashboard deliberately excludes tunnelled traffic, so while a VPN is
connected it shows a small figure where the user expects a large one. A page
quietly missing most of what you expect reads as broken — this notice is the
difference between "the app is wrong" and "the traffic is on the other tab".
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from netpulse.config import Settings                    # noqa: E402
from netpulse.db import DIRECT, VPN, Database           # noqa: E402
from netpulse.engine import Engine                      # noqa: E402
from netpulse.ui import pages                           # noqa: E402


class VpnNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="netpulse-notice-")
        self.db = Database(os.path.join(self.dir, f"n_{time.time_ns()}.db"))
        self.settings = Settings()
        self.engine = Engine(self.db, self.settings)
        self._real_vpn_active = pages.vpn_active

    def tearDown(self) -> None:
        pages.vpn_active = self._real_vpn_active
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def page(self, link: str = DIRECT) -> pages.DashboardPage:
        return pages.DashboardPage(self.db, self.engine, self.settings, link=link)

    def test_it_appears_only_while_a_tunnel_is_up(self) -> None:
        pages.vpn_active = lambda: False
        page = self.page()
        page.refresh()
        self.assertFalse(page.vpn_notice.isVisible())

        pages.vpn_active = lambda: True
        page.refresh()
        self.assertTrue(page.vpn_notice.isVisibleTo(page))

    def test_it_says_where_the_missing_traffic_went(self) -> None:
        pages.vpn_active = lambda: True
        page = self.page()
        page.refresh()
        text = page.vpn_notice_text.text()
        self.assertIn("VPN tab", text)
        self.assertIn("overhead", text)

    def test_the_vpn_tab_never_shows_it(self) -> None:
        # That page is not missing anything, so the explanation would be noise.
        pages.vpn_active = lambda: True
        page = self.page(link=VPN)
        page.refresh()
        self.assertFalse(page.vpn_notice.isVisible())

    def test_a_failing_adapter_check_does_not_break_the_page(self) -> None:
        # Refresh runs on a timer; an exception here would fire repeatedly.
        pages.vpn_active = lambda: (_ for _ in ()).throw(OSError("no psutil"))
        page = self.page()
        page.refresh()                     # must not raise
        self.assertFalse(page.vpn_notice.isVisible())


class ManualRecheckTests(unittest.TestCase):
    """The button that answers "is the displayed address stale?" in place.

    Before it existed, the only way to make the app look again was to restart
    it — which destroys the long-running state that causes the fault, so the
    test and the bug could never coexist.
    """

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="netpulse-recheck-")
        self.db = Database(os.path.join(self.dir, f"n_{time.time_ns()}.db"))
        self.settings = Settings()
        self.engine = Engine(self.db, self.settings)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_button_queues_a_lookup_immediately(self) -> None:
        page = pages.SettingsPage(self.db, self.engine, self.settings)
        self.engine.wan._due.clear()
        page._check_wan_now()
        self.assertTrue(self.engine.wan._pending(),
                        "no lookup was queued")

    def test_it_is_recorded_so_the_log_shows_who_asked(self) -> None:
        written = []
        self.engine.wan.log = type("L", (), {"write": lambda _s, m: written.append(m)})()
        page = pages.SettingsPage(self.db, self.engine, self.settings)
        page._check_wan_now()
        self.assertTrue(any("manual" in m for m in written))


if __name__ == "__main__":
    unittest.main()
