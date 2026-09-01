"""The direct/VPN split: classification, subtraction, and per-link storage.

The point of the split is that a tunnel adapter and the physical adapter
underneath it both count the same conversation. These tests pin the rule that
keeps the two from being added together.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netpulse.collectors import net_system
from netpulse.collectors.net_system import (DIRECT, IGNORED, VPN,
                                            SystemNetCollector,
                                            classify_adapter)
from netpulse.db import Database


class Counter:
    """Stand-in for psutil's per-adapter counter tuple."""

    def __init__(self, recv: int, sent: int) -> None:
        self.bytes_recv = recv
        self.bytes_sent = sent


class FakePsutil:
    def __init__(self, counters: dict[str, tuple[int, int]]) -> None:
        self.counters = counters

    def net_io_counters(self, pernic: bool = False):
        return {n: Counter(*v) for n, v in self.counters.items()}


class ClassifyTests(unittest.TestCase):
    def test_real_adapters(self) -> None:
        for name in ("Ethernet", "Wi-Fi", "Ethernet 2", "eth0"):
            self.assertEqual(classify_adapter(name), DIRECT, name)

    def test_tunnels(self) -> None:
        # The names taken from a real machine running Surfshark and OpenVPN.
        for name in ("SurfsharkWireGuard", "OpenVPN Data Channel Offload",
                     "NordLynx", "ProtonVPN TUN", "wg-quick0", "TAP-Windows Adapter V9"):
            self.assertEqual(classify_adapter(name), VPN, name)

    def test_ignored(self) -> None:
        for name in ("Loopback Pseudo-Interface 1", "vEthernet (WSL)",
                     "VMware Network Adapter VMnet1", "lo"):
            self.assertEqual(classify_adapter(name), IGNORED, name)


class SplitTests(unittest.TestCase):
    """Direct is what is left of the physical adapter once the tunnel is taken off."""

    def setUp(self) -> None:
        self.fake = FakePsutil({"Ethernet": (0, 0), "SurfsharkWireGuard": (0, 0)})
        self._real = net_system.psutil
        net_system.psutil = self.fake
        self.collector = SystemNetCollector()

    def tearDown(self) -> None:
        net_system.psutil = self._real

    def advance(self, ethernet: tuple[int, int], tunnel: tuple[int, int]) -> dict:
        self.fake.counters["Ethernet"] = ethernet
        self.fake.counters["SurfsharkWireGuard"] = tunnel
        return self.collector.sample()

    def test_tunnel_is_not_added_to_the_physical_adapter(self) -> None:
        # 1 MB through the tunnel appears on both adapters: it must be counted
        # once, as VPN, with nothing left over as direct.
        result = self.advance((1_000_000, 100_000), (1_000_000, 100_000))
        self.assertEqual(result[VPN], (1_000_000, 100_000))
        self.assertEqual(result[DIRECT], (0, 0))

    def test_direct_traffic_alongside_a_tunnel(self) -> None:
        # 1.5 MB on the wire of which 1 MB was tunnelled.
        result = self.advance((1_500_000, 150_000), (1_000_000, 100_000))
        self.assertEqual(result[VPN], (1_000_000, 100_000))
        self.assertEqual(result[DIRECT], (500_000, 50_000))

    def test_no_tunnel_means_everything_is_direct(self) -> None:
        result = self.advance((800_000, 40_000), (0, 0))
        self.assertEqual(result[DIRECT], (800_000, 40_000))
        self.assertEqual(result[VPN], (0, 0))

    def test_direct_never_goes_negative(self) -> None:
        # Tunnel overhead can briefly exceed the physical delta across a sample
        # boundary; the answer is zero, never a negative figure.
        result = self.advance((900_000, 90_000), (1_000_000, 100_000))
        self.assertEqual(result[DIRECT], (0, 0))

    def test_counter_reset_is_not_read_as_a_huge_delta(self) -> None:
        self.advance((5_000_000, 500_000), (0, 0))
        result = self.advance((1_000, 100), (0, 0))     # driver reloaded
        self.assertEqual(result[DIRECT], (0, 0))

    def test_combined_rate_is_the_sum_of_both_links(self) -> None:
        self.advance((1_500_000, 150_000), (1_000_000, 100_000))
        down, up = self.collector.last_rate
        rates = self.collector.last_rates
        self.assertAlmostEqual(down, rates[DIRECT][0] + rates[VPN][0])
        self.assertAlmostEqual(up, rates[DIRECT][1] + rates[VPN][1])


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="netpulse-links-")
        self.db = Database(os.path.join(self.dir, f"n_{time.time_ns()}.db"))

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_links_are_stored_and_queried_apart(self) -> None:
        now = time.time()
        self.db.add_samples({DIRECT: (300, 30), VPN: (700, 70)},
                            {DIRECT: {"a.exe": (300, 30)},
                             VPN: {"b.exe": (700, 70)}}, ts=now)
        self.db.rollup(since=0)
        self.assertEqual(self.db.totals_for_period("day", link=DIRECT)[:2], (300, 30))
        self.assertEqual(self.db.totals_for_period("day", link=VPN)[:2], (700, 70))
        # No link given means both, which is what a machine-wide figure is.
        self.assertEqual(self.db.totals_for_period("day")[:2], (1000, 100))

    def test_applications_are_listed_per_link(self) -> None:
        now = time.time()
        self.db.add_samples({DIRECT: (300, 30), VPN: (700, 70)},
                            {DIRECT: {"a.exe": (300, 30)},
                             VPN: {"b.exe": (700, 70)}}, ts=now)
        self.db.rollup(since=0)
        direct = {row["app"] for row in self.db.apps_for_period("day", link=DIRECT)}
        vpn = {row["app"] for row in self.db.apps_for_period("day", link=VPN)}
        self.assertIn("a.exe", direct)
        self.assertNotIn("b.exe", direct)
        self.assertIn("b.exe", vpn)
        self.assertNotIn("a.exe", vpn)

    def test_files_carry_their_link(self) -> None:
        self.db.add_file("/d/one.iso", "one.iso", "/d", 10, link=DIRECT)
        self.db.add_file("/d/two.iso", "two.iso", "/d", 20, link=VPN)
        names = {r["name"] for r in self.db.recent_files(link=VPN)}
        self.assertEqual(names, {"two.iso"})
        self.assertEqual({r["name"] for r in self.db.recent_files()},
                         {"one.iso", "two.iso"})

    def test_rollup_keeps_the_links_separate(self) -> None:
        now = time.time()
        for _ in range(3):
            self.db.add_samples({DIRECT: (100, 10), VPN: (200, 20)}, ts=now)
        self.db.rollup(since=0)
        self.db.rollup(since=0)          # idempotent: recomputed, not accumulated
        self.assertEqual(self.db.totals_for_period("day", link=DIRECT)[:2], (300, 30))
        self.assertEqual(self.db.totals_for_period("day", link=VPN)[:2], (600, 60))


class RealisticVpnSessionTests(unittest.TestCase):
    """What the Dashboard should read during an ordinary VPN session.

    The expectation worth pinning is not "direct is zero" — it cannot be.
    WireGuard wraps every packet, so the physical adapter always carries more
    bytes than the tunnel does, and that overhead is real traffic on the wire.
    What must hold is that the residual stays *small*: overhead, not a second
    copy of the session.
    """

    #: WireGuard adds roughly 60 bytes to a 1420-byte payload — about 4%.
    OVERHEAD = 1.045

    def setUp(self) -> None:
        self.fake = FakePsutil({"Ethernet": (0, 0), "SurfsharkWireGuard": (0, 0)})
        self._real = net_system.psutil
        net_system.psutil = self.fake
        self.collector = SystemNetCollector()

    def tearDown(self) -> None:
        net_system.psutil = self._real

    def run_session(self, seconds: int = 60, per_second: int = 2_000_000,
                    lan_per_second: int = 0) -> dict[str, list[int]]:
        """Push traffic through the tunnel and total what each side records."""
        eth = [0, 0]
        tun = [0, 0]
        totals = {DIRECT: [0, 0], VPN: [0, 0]}
        for _ in range(seconds):
            tun[0] += per_second
            tun[1] += per_second // 20
            eth[0] += int(per_second * self.OVERHEAD) + lan_per_second
            eth[1] += int(per_second // 20 * self.OVERHEAD)
            self.fake.counters["Ethernet"] = tuple(eth)
            self.fake.counters["SurfsharkWireGuard"] = tuple(tun)
            sample = self.collector.sample()
            for link in (DIRECT, VPN):
                totals[link][0] += sample[link][0]
                totals[link][1] += sample[link][1]
        return totals

    def test_the_vpn_tab_gets_the_whole_session(self) -> None:
        totals = self.run_session()
        self.assertEqual(totals[VPN][0], 60 * 2_000_000)

    def test_the_dashboard_shows_only_overhead(self) -> None:
        totals = self.run_session()
        share = totals[DIRECT][0] / totals[VPN][0]
        self.assertLess(share, 0.10,
                        "the Dashboard is showing more than encryption overhead")
        self.assertGreater(share, 0.0,
                           "overhead is real traffic and should not vanish")

    def test_nothing_is_counted_twice(self) -> None:
        # The bug this whole split exists to fix: the two sides together must
        # equal the wire, not double it.
        totals = self.run_session()
        wire = 60 * int(2_000_000 * self.OVERHEAD)   # per second, as sampled
        self.assertEqual(totals[DIRECT][0] + totals[VPN][0], wire)

    def test_traffic_that_bypasses_the_tunnel_lands_on_the_dashboard(self) -> None:
        # A NAS copy or a local DNS server never enters the tunnel, and should
        # show up as direct rather than being hidden.
        plain = self.run_session(lan_per_second=0)[DIRECT][0]
        withlan = self.run_session(lan_per_second=500_000)[DIRECT][0]
        self.assertGreater(withlan - plain, 60 * 400_000)

    def test_an_idle_tunnel_records_nothing_on_either_side(self) -> None:
        totals = self.run_session(seconds=10, per_second=0)
        self.assertEqual(totals[VPN], [0, 0])
        self.assertEqual(totals[DIRECT], [0, 0])


if __name__ == "__main__":
    unittest.main()
