"""Public (WAN) IP address lookup.

Your router knows its own WAN address, but there is no reliable, vendor-neutral
way to ask it — so, like every other tool that shows this, NetPulse asks an
outside service what address the request appeared to come from.

That means a small outbound HTTPS request, which is worth being upfront about
in an application built to watch outbound traffic: it happens once at start-up
and then every 15 minutes, it sends nothing but the request itself, and it can
be switched off in Settings. Several providers are tried in turn so one being
down or blocked isn't fatal.
"""
from __future__ import annotations

import ipaddress
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, Signal

#: Plain-text endpoints — each returns the bare address and nothing else.
ENDPOINTS: list[tuple[str, str]] = [
    ("https://api.ipify.org", "ipify.org"),
    ("https://icanhazip.com", "icanhazip.com"),
    ("https://ifconfig.me/ip", "ifconfig.me"),
    ("https://ipinfo.io/ip", "ipinfo.io"),
]

TIMEOUT = 6.0
MAX_BYTES = 128          # a valid answer is under 50 bytes; anything more is an error page
USER_AGENT = "NetPulse/1.0 (+https://github.com/zimba7768/Netpulse)"


def fetch_text(url: str, timeout: float = TIMEOUT) -> str:
    """Fetch a small plain-text body. Separate function so tests can replace it."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_BYTES).decode("utf-8", "ignore")


def parse_ip(text: str) -> str:
    """Return a normalised address, or '' if the response wasn't one.

    Providers occasionally answer with an error page or a rate-limit notice;
    validating rather than trusting keeps that out of the interface.
    """
    if not text:
        return ""
    candidate = text.strip().split()[0].strip() if text.strip() else ""
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    if address.is_private or address.is_loopback or address.is_unspecified:
        return ""          # a proxy answered with a LAN address — not our WAN IP
    return str(address)


class WanIpResolver(QObject):
    """Looks up the public IP on a background thread and reports changes."""

    #: address ('' when unavailable), provider name
    resolved = Signal(str, str)

    def __init__(self, interval: float = 900.0, parent=None) -> None:
        super().__init__(parent)
        self.interval = interval
        self.address = ""
        self.source = ""
        self.checked_at = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ life
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wan-ip", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def refresh_now(self) -> None:
        """Ask again immediately rather than waiting for the next interval."""
        self._wake.set()

    # ----------------------------------------------------------------- fetch
    def lookup(self) -> tuple[str, str]:
        """Try each provider until one gives a valid address."""
        for url, name in ENDPOINTS:
            if self._stop.is_set():
                break
            try:
                address = parse_ip(fetch_text(url))
            except Exception:
                continue                       # offline, blocked, timed out
            if address:
                return address, name
        return "", ""

    def _run(self) -> None:
        while not self._stop.is_set():
            address, source = self.lookup()
            changed = address != self.address
            self.address, self.source = address, source
            self.checked_at = time.time()
            if changed or address:
                self.resolved.emit(address, source)
            self._wake.wait(self.interval)
            self._wake.clear()
