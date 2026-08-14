"""Public IP lookup tests.

The risk here isn't the happy path, it's the failure paths: a provider that
returns an error page, a captive portal that answers everything with a LAN
address, or every provider being unreachable. None of those may put junk in the
interface, and none may raise.
"""
from __future__ import annotations

import os
import sys
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
