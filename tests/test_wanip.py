"""Public IP lookup tests.

The risk here isn't the happy path, it's the failure paths: a provider that
returns an error page, a captive portal that answers everything with a LAN
address, or every provider being unreachable. None of those may put junk in the
interface, and none may raise.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt

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

    def __init__(self, layout: dict[str, list[str]],
                 down: dict[str, list[str]] | None = None) -> None:
        self.layout = layout
        self.down = down or {}

    def net_if_addrs(self):
        merged = {**self.layout, **self.down}
        return {
            name: [_FakeAddr(socket.AF_INET, ip) for ip in ips]
            for name, ips in merged.items()
        }

    def net_if_stats(self):
        return ({name: _FakeStat(True) for name in self.layout}
                | {name: _FakeStat(False) for name in self.down})


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
        self.assertEqual(without, wanip.network_fingerprint())

    def test_ignores_adapters_that_are_down(self):
        """A real machine has several, and they are not part of the picture."""
        wanip.psutil = _FakePsutil({"Ethernet": ["192.168.50.10"]})
        just_ethernet = wanip.network_fingerprint()
        wanip.psutil = _FakePsutil(
            {"Ethernet": ["192.168.50.10"]},
            down={"Wi-Fi 2": ["169.254.128.239"], "Wi-Fi 3": ["169.254.202.70"]})
        self.assertEqual(just_ethernet, wanip.network_fingerprint())

    def test_ignores_apipa_addresses(self):
        """169.254.x.x means DHCP failed; Windows reissues them on a whim.

        A machine with several disconnected virtual adapters would otherwise
        look like it changed network every time one renumbered itself.
        """
        wanip.psutil = _FakePsutil({"Ethernet": ["192.168.50.10"]})
        clean = wanip.network_fingerprint()
        wanip.psutil = _FakePsutil(
            {"Ethernet": ["192.168.50.10"], "Bluetooth": ["169.254.180.227"]})
        self.assertEqual(clean, wanip.network_fingerprint())

    def test_a_real_windows_layout_is_stable_while_the_vpn_holds(self):
        """Modelled on a reported machine: Surfshark up, six adapters down."""
        layout = {"Ethernet": ["192.168.50.10"], "SurfsharkWireGuard": ["10.14.0.2"]}
        down_first = {"Bluetooth Network Connection": ["169.254.180.227"],
                      "OpenVPN Data Channel Offload for Surfshark": ["169.254.202.209"],
                      "Wi-Fi": ["169.254.112.158"], "Wi-Fi 2": ["169.254.128.239"]}
        down_later = {"Bluetooth Network Connection": ["169.254.9.11"],
                      "OpenVPN Data Channel Offload for Surfshark": ["169.254.77.4"],
                      "Wi-Fi": ["169.254.31.200"], "Wi-Fi 2": ["169.254.55.9"]}
        wanip.psutil = _FakePsutil(layout, down=down_first)
        before = wanip.network_fingerprint()
        wanip.psutil = _FakePsutil(layout, down=down_later)
        self.assertEqual(before, wanip.network_fingerprint(),
                         "disconnected adapters renumbering must not read as "
                         "a network change — it would restart the lookup "
                         "ladder over and over")

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


class FailureRecoveryTests(unittest.TestCase):
    """Reported: with a VPN on, the address went blank and stayed blank.

    A failed lookup used to drop straight back to the 15-minute cycle, so one
    bad moment — a tunnel still connecting, a second offline — blanked the
    display for a quarter of an hour.
    """

    def setUp(self) -> None:
        self.real_fetch = wanip.fetch_text
        self.real_backoff = wanip.RETRY_BACKOFF
        self.answer: object = "93.184.216.34"
        wanip.fetch_text = self._fetch

    def tearDown(self) -> None:
        wanip.fetch_text = self.real_fetch
        wanip.RETRY_BACKOFF = self.real_backoff

    def _fetch(self, url: str, timeout: float = 0) -> str:
        if isinstance(self.answer, Exception):
            raise self.answer
        return str(self.answer)

    def test_a_failure_retries_soon_not_in_fifteen_minutes(self):
        resolver = wanip.WanIpResolver(interval=900)
        resolver.check()
        self.assertEqual(resolver.next_delay(), 900, "healthy: the slow cycle")

        self.answer = OSError("no route to host")
        resolver.check()
        self.assertEqual(resolver.next_delay(), wanip.RETRY_BACKOFF[0])
        self.assertLess(resolver.next_delay(), 60,
                        "a failure must be retried within the minute")

    def test_backoff_grows_then_settles(self):
        resolver = wanip.WanIpResolver(interval=900)
        self.answer = OSError("down")
        delays = []
        for _ in range(len(wanip.RETRY_BACKOFF) + 2):
            resolver.check()
            delays.append(resolver.next_delay())
        self.assertEqual(delays[:len(wanip.RETRY_BACKOFF)], list(wanip.RETRY_BACKOFF))
        self.assertEqual(delays[-1], wanip.RETRY_BACKOFF[-1], "caps, never grows without limit")
        self.assertEqual(sorted(delays), delays, "backoff must not go backwards")

    def test_recovers_to_the_slow_cycle_after_a_success(self):
        resolver = wanip.WanIpResolver(interval=900)
        self.answer = OSError("down")
        resolver.check()
        resolver.check()
        self.assertNotEqual(resolver.next_delay(), 900)
        self.answer = "93.184.216.34"
        resolver.check()
        self.assertEqual(resolver.failures, 0)
        self.assertEqual(resolver.next_delay(), 900)

    def test_a_transient_failure_keeps_the_last_address_on_screen(self):
        """Nothing suggests the address changed, so do not blank it."""
        resolver = wanip.WanIpResolver()
        resolver.check()
        self.assertEqual(resolver.address, "93.184.216.34")
        self.answer = TimeoutError("slow")
        resolver.check()
        self.assertEqual(resolver.address, "93.184.216.34",
                         "a blip must not wipe a known-good address")

    def test_a_failure_after_a_network_change_does_blank_it(self):
        """Here the old address really is suspect, so showing it would lie.

        One failure is not enough: a tunnel coming up drops a request or two,
        and blanking on the first would flicker for no reason.
        """
        resolver = wanip.WanIpResolver()
        resolver.check()
        resolver.suspect = True                # as the loop sets on adapter change
        self.answer = OSError("tunnel not up yet")

        resolver.check()
        self.assertEqual(resolver.address, "93.184.216.34",
                         "one failure mid-reconnect should not blank it")
        resolver.check()
        self.assertEqual(resolver.address, "",
                         "after a network change an unconfirmed address is wrong")

    def test_the_reason_for_failure_is_recorded(self):
        """So the interface can explain itself instead of just saying no."""
        resolver = wanip.WanIpResolver()
        self.answer = OSError("blocked")
        resolver.check()
        self.assertIn("OSError", resolver.last_error)
        self.answer = "93.184.216.34"
        resolver.check()
        self.assertEqual(resolver.last_error, "", "cleared once it works again")

    def test_the_interface_is_told_even_when_the_first_lookup_fails(self):
        """Otherwise the chip sits on 'checking…' for ever."""
        emitted = []
        resolver = wanip.WanIpResolver()
        resolver.resolved.connect(lambda a, s: emitted.append((a, s)),
                                  Qt.DirectConnection)
        self.answer = OSError("offline")
        resolver.check()
        self.assertEqual(emitted, [("", "")])


class EndpointConfigTests(unittest.TestCase):
    def test_every_endpoint_is_https_and_named(self):
        self.assertTrue(wanip.ENDPOINTS)
        for url, name in wanip.ENDPOINTS:
            self.assertTrue(url.startswith("https://"), url)
            self.assertTrue(name)

    def test_response_read_is_bounded(self):
        """A provider serving a huge body must not be read into memory."""
        self.assertLessEqual(wanip.MAX_BYTES, 1024)


class ProviderPreferenceTests(unittest.TestCase):
    """On a network that blocks some providers by name, don't keep asking them.

    Reported machine: DNS refuses ipify.org, checkip.amazonaws.com and
    icanhazip.com, while ifconfig.me and ipinfo.io resolve normally.
    """

    def setUp(self) -> None:
        self.real_fetch = wanip.fetch_text
        self.calls: list[str] = []
        blocked = {url for url, _ in wanip.ENDPOINTS[:3]}

        def fake(url: str, timeout: float = 0) -> str:
            self.calls.append(url)
            if url in blocked:
                raise OSError("getaddrinfo failed")
            return "47.194.12.239"

        wanip.fetch_text = fake

    def tearDown(self) -> None:
        wanip.fetch_text = self.real_fetch

    def test_the_first_lookup_walks_the_list(self):
        resolver = wanip.WanIpResolver()
        self.assertEqual(resolver.lookup()[0], "47.194.12.239")
        self.assertEqual(len(self.calls), 4, "three blocked, the fourth answers")

    def test_later_lookups_go_straight_to_what_worked(self):
        resolver = wanip.WanIpResolver()
        resolver.lookup()
        self.calls.clear()
        self.assertEqual(resolver.lookup()[0], "47.194.12.239")
        self.assertEqual(len(self.calls), 1,
                         "the known-good provider should be asked first")

    def test_it_falls_back_if_the_preferred_one_stops_working(self):
        resolver = wanip.WanIpResolver()
        resolver.lookup()
        resolver.preferred = wanip.ENDPOINTS[0][0]      # pretend a blocked one
        self.calls.clear()
        self.assertEqual(resolver.lookup()[0], "47.194.12.239",
                         "a stale preference must not break the lookup")
        self.assertGreater(len(self.calls), 1)


class TraceEndpointTests(unittest.TestCase):
    """The DNS-free fallback answers in a different shape to the others."""

    BODY = ("fl=12f34\n"
            "h=1.1.1.1\n"
            "ip=93.184.216.34\n"
            "ts=1756600000.123\n"
            "visit_scheme=https\n"
            "uag=NetPulse/1.0\n"
            "colo=LHR\n")

    def test_the_named_field_is_read(self) -> None:
        self.assertEqual(wanip.parse_ip(self.BODY), "93.184.216.34")

    def test_a_private_answer_is_still_rejected(self) -> None:
        self.assertEqual(wanip.parse_ip("fl=x\nip=192.168.1.8\n"), "")

    def test_the_field_is_matched_at_the_start_of_a_line(self) -> None:
        # "sip=" or "ip=" inside another value must not be mistaken for it.
        self.assertEqual(wanip.parse_ip("uag=ip=1.2.3.4\nip=93.184.216.34\n"),
                         "93.184.216.34")

    def test_a_body_with_no_ip_field_gives_nothing(self) -> None:
        self.assertEqual(wanip.parse_ip("fl=x\nts=1\ncolo=LHR\n"), "")

    def test_the_fallback_needs_no_name_resolution(self) -> None:
        # The whole point of this provider: if it had a hostname it would fail
        # in exactly the situation it exists for.
        url = dict((name, url) for url, name in wanip.ENDPOINTS)["1.1.1.1 (no DNS)"]
        host = url.split("//", 1)[1].split("/", 1)[0]
        ipaddress.ip_address(host)          # raises if it is not a literal

    def test_it_is_tried_last(self) -> None:
        # It is a fallback, not a default: the ordinary providers come first.
        self.assertEqual(wanip.ENDPOINTS[-1][1], "1.1.1.1 (no DNS)")


class LoopSurvivalTests(unittest.TestCase):
    """The background loop must outlive anything that goes wrong inside it.

    This is the defect these tests exist for: an unguarded loop died on its
    first unexpected exception, and nothing anywhere said so. Lookups stopped,
    adapter watching stopped, and the chip went on reading "retrying…" for the
    rest of the session because the state it was reading never changed again.
    """

    def setUp(self) -> None:
        # The loop paces itself against the adapter poll; shorten it so the
        # suite does not spend seconds waiting to watch it go round.
        self._poll = wanip.POLL_SECONDS
        wanip.POLL_SECONDS = 0.02

    def tearDown(self) -> None:
        wanip.POLL_SECONDS = self._poll

    def resolver(self) -> wanip.WanIpResolver:
        r = wanip.WanIpResolver(interval=0.05)
        # These tests are about the loop staying alive, not about how long it
        # waits between attempts, so the backoff ladder is collapsed.
        r.next_delay = lambda: 0.02
        self.addCleanup(r.stop)
        return r

    def test_a_raising_check_does_not_kill_the_loop(self) -> None:
        r = self.resolver()
        calls = []

        def exploding_check():
            calls.append(1)
            raise RuntimeError("boom")

        r.check = exploding_check
        r.start()
        deadline = time.time() + 3.0
        while len(calls) < 3 and time.time() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(len(calls), 3,
                                "the loop stopped after the first exception")
        self.assertTrue(r.running, "the thread died")

    def test_it_recovers_once_the_error_stops(self) -> None:
        r = self.resolver()
        state = {"fail": True}

        def flaky_check():
            if state["fail"]:
                raise RuntimeError("boom")
            r.address, r.source = "93.184.216.34", "test"
            r.failures = 0
            r.checked_at = time.time()
            return True

        r.check = flaky_check
        r.start()
        time.sleep(0.4)
        self.assertEqual(r.address, "")
        state["fail"] = False
        deadline = time.time() + 3.0
        while not r.address and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(r.address, "93.184.216.34",
                         "the loop never resumed after recovering")

    def test_a_raising_fingerprint_does_not_kill_the_loop(self) -> None:
        # The adapter watch runs on every pass, so it is the likelier of the
        # two to be handed something unexpected by the platform.
        r = self.resolver()
        real = wanip.network_fingerprint
        wanip.network_fingerprint = lambda: (_ for _ in ()).throw(OSError("nope"))
        self.addCleanup(setattr, wanip, "network_fingerprint", real)
        r.check = lambda: False
        r.start()
        time.sleep(0.5)
        self.assertTrue(r.running, "the thread died watching adapters")

    def test_an_error_is_recorded_rather_than_swallowed(self) -> None:
        r = self.resolver()
        r.check = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        r.start()
        deadline = time.time() + 3.0
        while not r.loop_errors and time.time() < deadline:
            time.sleep(0.05)
        self.assertGreater(r.loop_errors, 0)
        self.assertIn("boom", r.last_error)
        # It also has to count as a failed attempt, or the chip would go on
        # saying "checking…" while nothing was happening.
        self.assertGreater(r.failures, 0)
        self.assertGreater(r.checked_at, 0)

    def test_a_dead_loop_is_visible(self) -> None:
        r = self.resolver()
        self.assertFalse(r.running)
        r.check = lambda: False
        r.start()
        self.assertTrue(r.running)
        r.stop()
        self.assertFalse(r.running)


if __name__ == "__main__":
    unittest.main(verbosity=2)
