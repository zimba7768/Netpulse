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


if __name__ == "__main__":
    unittest.main()
