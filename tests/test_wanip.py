"""Public IP lookup tests.

The risk here isn't the happy path, it's the failure paths: a provider that
returns an error page, a captive portal that answers everything with a LAN
address, or every provider being unreachable. None of those may put junk in the
interface, and none may raise.
"""
from __future__ import annotations

import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netpulse.collectors import wanip


class ParseTests(unittest.TestCase):
    def test_accepts_a_bare_address(self):
        self.assertEqual(wanip.parse_ip("93.184.216.34"), "93.184.216.34")

    def test_tolerates_whitespace_and_newlines(self):
        self.assertEqual(wanip.parse_ip("  93.184.216.34\n"), "93.184.216.34")

    def test_accepts_ipv6(self):
        self.assertEqual(wanip.parse_ip("2606:4700:4700::1111"),
                         "2606:4700:4700::1111")

    def test_documentation_ranges_count_as_non_public(self):
        """RFC 5737 / RFC 3849 ranges are reserved, so they are never a WAN IP.

        Worth pinning down because these are exactly the addresses used in
        examples and screenshots, and it explains why they never appear here.
        """
        for address in ("203.0.113.42", "198.51.100.7", "192.0.2.1", "2001:db8::1"):
            self.assertEqual(wanip.parse_ip(address), "", address)

    def test_rejects_an_error_page(self):
        self.assertEqual(wanip.parse_ip("<html><body>429 Too Many"), "")

    def test_rejects_empty_and_none(self):
        self.assertEqual(wanip.parse_ip(""), "")
        self.assertEqual(wanip.parse_ip("   "), "")
        self.assertEqual(wanip.parse_ip(None), "")

    def test_rejects_private_addresses(self):
        """A captive portal or proxy answering with a LAN address is not a WAN IP."""
        for address in ("192.168.1.1", "10.0.0.5", "172.16.4.9", "127.0.0.1",
                        "0.0.0.0"):
            self.assertEqual(wanip.parse_ip(address), "", address)

    def test_rejects_text_that_merely_contains_an_address(self):
        self.assertEqual(wanip.parse_ip("your ip is 93.184.216.34"), "")


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_fetch = wanip.fetch_text
        self.calls: list[str] = []

    def tearDown(self) -> None:
        wanip.fetch_text = self.real_fetch

    def _patch(self, responses: dict[str, object]) -> None:
        def fake(url: str, timeout: float = 0) -> str:
            self.calls.append(url)
            value = responses.get(url, "")
            if isinstance(value, Exception):
                raise value
            return str(value)
        wanip.fetch_text = fake

    def test_uses_the_first_provider_that_answers(self):
        first = wanip.ENDPOINTS[0][0]
        self._patch({first: "93.184.216.34"})
        resolver = wanip.WanIpResolver()
        address, source = resolver.lookup()
        self.assertEqual(address, "93.184.216.34")
        self.assertEqual(source, wanip.ENDPOINTS[0][1])
        self.assertEqual(len(self.calls), 1, "should stop at the first success")

    def test_falls_through_to_the_next_provider_on_error(self):
        first, second = wanip.ENDPOINTS[0][0], wanip.ENDPOINTS[1][0]
        self._patch({first: OSError("connection refused"), second: "8.8.4.4"})
        address, source = wanip.WanIpResolver().lookup()
        self.assertEqual(address, "8.8.4.4")
        self.assertEqual(source, wanip.ENDPOINTS[1][1])

    def test_falls_through_on_a_junk_response(self):
        first, second = wanip.ENDPOINTS[0][0], wanip.ENDPOINTS[1][0]
        self._patch({first: "<html>error</html>", second: "8.8.4.4"})
        self.assertEqual(wanip.WanIpResolver().lookup()[0], "8.8.4.4")

    def test_returns_empty_when_every_provider_fails(self):
        self._patch({url: TimeoutError("timed out") for url, _ in wanip.ENDPOINTS})
        address, source = wanip.WanIpResolver().lookup()
        self.assertEqual((address, source), ("", ""))
        self.assertEqual(len(self.calls), len(wanip.ENDPOINTS),
                         "every provider should be tried before giving up")

    def test_lookup_never_raises(self):
        self._patch({url: RuntimeError("boom") for url, _ in wanip.ENDPOINTS})
        try:
            wanip.WanIpResolver().lookup()
        except Exception as exc:                       # pragma: no cover
            self.fail(f"lookup() raised {exc!r}; the interface must not crash")


class _FakeStat:
    def __init__(self, isup: bool = True) -> None:
        self.isup = isup


class _FakeAddr:
    def __init__(self, family, address: str) -> None:
        self.family = family
        self.address = address


class _FakePsutil:
    """Stands in for psutil so adapter changes can be simulated."""

    def __init__(self, layout: dict[str, list[str]]) -> None:
        self.layout = layout

    def net_if_addrs(self):
        return {
            name: [_FakeAddr(socket.AF_INET, ip) for ip in ips]
            for name, ips in self.layout.items()
        }

    def net_if_stats(self):
        return {name: _FakeStat(True) for name in self.layout}


HOME = {"Wi-Fi": ["192.168.1.20"], "Loopback": ["127.0.0.1"]}
VPN = {"Wi-Fi": ["192.168.1.20"], "Loopback": ["127.0.0.1"],
       "ProtonVPN TUN": ["10.2.0.2"]}


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_psutil = wanip.psutil

    def tearDown(self) -> None:
        wanip.psutil = self.real_psutil

    def test_is_stable_when_nothing_changes(self):
        wanip.psutil = _FakePsutil(HOME)
        self.assertEqual(wanip.network_fingerprint(), wanip.network_fingerprint())

    def test_changes_when_a_vpn_adapter_appears(self):
        wanip.psutil = _FakePsutil(HOME)
        before = wanip.network_fingerprint()
        wanip.psutil = _FakePsutil(VPN)
        self.assertNotEqual(before, wanip.network_fingerprint(),
                            "a VPN connecting must register as a change")

    def test_changes_when_an_address_is_reassigned(self):
        wanip.psutil = _FakePsutil(HOME)
        before = wanip.network_fingerprint()
        wanip.psutil = _FakePsutil({"Wi-Fi": ["192.168.1.55"],
                                    "Loopback": ["127.0.0.1"]})
        self.assertNotEqual(before, wanip.network_fingerprint())

    def test_ignores_loopback_addresses(self):
        wanip.psutil = _FakePsutil({"Wi-Fi": ["192.168.1.20"]})
        without = wanip.network_fingerprint()
        wanip.psutil = _FakePsutil({"Wi-Fi": ["192.168.1.20", "127.0.0.1"]})
        self.assertEqual(without[0][2], wanip.network_fingerprint()[0][2])

    def test_survives_psutil_being_unavailable(self):
        wanip.psutil = None
        self.assertEqual(wanip.network_fingerprint(), ())


class ScheduleTests(unittest.TestCase):
    def test_due_entries_are_kept_in_order(self):
        resolver = wanip.WanIpResolver()
        resolver.schedule(30.0, 5.0, 15.0)
        self.assertEqual(resolver._due, sorted(resolver._due))

    def test_take_due_consumes_only_what_has_arrived(self):
        resolver = wanip.WanIpResolver()
        resolver.schedule(-1.0, -0.5, 60.0)      # two overdue, one future
        now = time.time()
        self.assertTrue(resolver._take_due(now))
        self.assertEqual(len(resolver._due), 1, "the future entry must survive")
        self.assertFalse(resolver._take_due(now), "nothing else is due yet")


class VpnSwitchTests(unittest.TestCase):
    """The reported bug: turning a VPN on did not update the address."""

    def setUp(self) -> None:
        self.real_psutil = wanip.psutil
        self.real_fetch = wanip.fetch_text
        self.real_poll = wanip.POLL_SECONDS
        self.real_delays = wanip.RECHECK_DELAYS
        wanip.POLL_SECONDS = 0.02
        wanip.RECHECK_DELAYS = (0.0,)
        self.answer = "93.184.216.34"
        wanip.fetch_text = lambda url, timeout=0: self.answer

    def tearDown(self) -> None:
        wanip.psutil = self.real_psutil
        wanip.fetch_text = self.real_fetch
        wanip.POLL_SECONDS = self.real_poll
        wanip.RECHECK_DELAYS = self.real_delays

    @staticmethod
    def _wait_for(predicate, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_address_follows_a_vpn_being_switched_on(self):
        wanip.psutil = _FakePsutil(HOME)
        resolver = wanip.WanIpResolver(interval=3600)   # periodic check disabled
        resolver.start()
        try:
            self.assertTrue(self._wait_for(lambda: resolver.address == "93.184.216.34"),
                            "the first lookup should have resolved")

            # VPN comes up: a new adapter appears and the exit address changes.
            self.answer = "45.83.220.11"
            wanip.psutil = _FakePsutil(VPN)

            self.assertTrue(
                self._wait_for(lambda: resolver.address == "45.83.220.11"),
                "the address must follow the VPN without waiting for the "
                "periodic check or an application restart")
        finally:
            resolver.stop()

    def test_address_follows_the_vpn_being_switched_off_again(self):
        wanip.psutil = _FakePsutil(VPN)
        self.answer = "45.83.220.11"
        resolver = wanip.WanIpResolver(interval=3600)
        resolver.start()
        try:
            self.assertTrue(self._wait_for(lambda: resolver.address == "45.83.220.11"))
            self.answer = "93.184.216.34"
            wanip.psutil = _FakePsutil(HOME)
            self.assertTrue(self._wait_for(lambda: resolver.address == "93.184.216.34"))
        finally:
            resolver.stop()

    def test_a_quiet_network_does_not_keep_looking_up(self):
        """No adapter change means no extra requests — this must stay cheap."""
        wanip.psutil = _FakePsutil(HOME)
        calls = []
        wanip.fetch_text = lambda url, timeout=0: (calls.append(url), self.answer)[1]
        resolver = wanip.WanIpResolver(interval=3600)
        resolver.start()
        try:
            self.assertTrue(self._wait_for(lambda: bool(calls)))
            time.sleep(0.4)                     # many poll ticks, no change
            self.assertEqual(len(calls), 1,
                             f"expected a single lookup, got {len(calls)}")
        finally:
            resolver.stop()


class EndpointConfigTests(unittest.TestCase):
    def test_every_endpoint_is_https_and_named(self):
        self.assertTrue(wanip.ENDPOINTS)
        for url, name in wanip.ENDPOINTS:
            self.assertTrue(url.startswith("https://"), url)
            self.assertTrue(name)

    def test_response_read_is_bounded(self):
        """A provider serving a huge body must not be read into memory."""
        self.assertLessEqual(wanip.MAX_BYTES, 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
