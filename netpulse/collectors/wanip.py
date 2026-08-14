"""Public (WAN) IP address lookup.

Your router knows its own WAN address, but there is no reliable, vendor-neutral
way to ask it — so, like every other tool that shows this, NetPulse asks an
outside service what address the request appeared to come from.

That means a small outbound HTTPS request, which is worth being upfront about
in an application built to watch outbound traffic: it sends nothing but the
request itself, and it can be switched off in Settings. Several providers are
tried in turn so one being down or blocked isn't fatal.

Rather than poll often, the resolver watches the *local* network adapters —
which is free, needs no network, and changes the instant a VPN connects or
drops. A change there schedules an immediate re-check, so the displayed address
follows a VPN within seconds. The slow periodic check remains as a backstop for
the case where the address changes without any local change, such as an ISP
lease renewal or a VPN hopping between its own servers.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, Signal

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

#: Plain-text endpoints — each returns the bare address and nothing else.
ENDPOINTS: list[tuple[str, str]] = [
    ("https://api.ipify.org", "ipify.org"),
    ("https://icanhazip.com", "icanhazip.com"),
    ("https://ifconfig.me/ip", "ifconfig.me"),
    ("https://ipinfo.io/ip", "ipinfo.io"),
]

TIMEOUT = 6.0
MAX_BYTES = 128          # a valid answer is under 50 bytes; more means an error page
USER_AGENT = "NetPulse/1.0 (+https://github.com/zimba7768/Netpulse)"

#: How often the adapter fingerprint is compared. Local only, so it is cheap.
POLL_SECONDS = 2.0

#: After a local network change, look again on this ladder (seconds). A VPN
#: adapter appears before its routes are ready, so the first answer can still
#: be the old address; the later attempts catch it once traffic actually moves.
RECHECK_DELAYS = (2.0, 6.0, 15.0, 30.0)


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


def network_fingerprint() -> tuple:
    """A cheap snapshot of local network configuration.

    Adapter names, whether each is up, and its IPv4 addresses. A VPN connecting
    or dropping always changes at least one of those. IPv6 is deliberately left
    out: privacy extensions rotate temporary addresses on a timer, which would
    look like a network change every few hours and trigger pointless lookups.
    """
    if psutil is None:
        return ()
    try:
        stats = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
    except Exception:
        return ()

    items: list[tuple[str, bool, tuple[str, ...]]] = []
    for name in sorted(addresses):
        status = stats.get(name)
        if status is None:
            continue
        ipv4 = tuple(sorted(
            entry.address for entry in addresses[name]
            if entry.family == socket.AF_INET and entry.address
            and not entry.address.startswith("127.")
        ))
        items.append((name, bool(status.isup), ipv4))
    return tuple(items)


class WanIpResolver(QObject):
    """Looks up the public IP on a background thread and reports changes."""

    #: address ('' when unavailable), provider name
    resolved = Signal(str, str)
    #: a lookup is under way after a local network change
    rechecking = Signal()

    def __init__(self, interval: float = 900.0, parent=None) -> None:
        super().__init__(parent)
        self.interval = interval
        self.address = ""
        self.source = ""
        self.checked_at = 0.0
        self.fingerprint: tuple = ()
        self._due: list[float] = []
        # refresh_now() is called from the interface thread while the worker is
        # reading the same list.
        self._due_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ life
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._due = [0.0]                      # look up straight away
        self._thread = threading.Thread(target=self._run, name="wan-ip", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def refresh_now(self) -> None:
        """Ask again immediately rather than waiting for the next check."""
        self.schedule(0.0)
        self._wake.set()

    # -------------------------------------------------------------- schedule
    def schedule(self, *delays: float, replace: bool = False) -> None:
        """Queue lookups this many seconds from now."""
        now = time.time()
        with self._due_lock:
            if replace:
                self._due.clear()
            self._due.extend(now + delay for delay in delays)
            self._due.sort()

    def _take_due(self, now: float) -> bool:
        """Consume every entry that has come due; True if there was one."""
        with self._due_lock:
            if not self._due or self._due[0] > now:
                return False
            while self._due and self._due[0] <= now:
                self._due.pop(0)
            return True

    def _pending(self) -> bool:
        with self._due_lock:
            return bool(self._due)

    def _clear_due(self) -> None:
        with self._due_lock:
            self._due.clear()

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

    def check(self) -> bool:
        """Perform one lookup. Returns True when the address changed."""
        address, source = self.lookup()
        first_answer = not self.checked_at
        changed = address != self.address or source != self.source
        self.address, self.source = address, source
        self.checked_at = time.time()
        if changed or first_answer:
            self.resolved.emit(address, source)
        return changed

    # ------------------------------------------------------------------ loop
    def _run(self) -> None:
        self.fingerprint = network_fingerprint()
        while not self._stop.is_set():
            now = time.time()
            if self._take_due(now):
                if self.check():
                    # Settled on a new address — drop any remaining retries.
                    self._clear_due()
                if not self._pending():
                    self.schedule(self.interval)

            self._wake.wait(POLL_SECONDS)
            if self._stop.is_set():
                break
            self._wake.clear()

            current = network_fingerprint()
            if current != self.fingerprint:
                self.fingerprint = current
                self.schedule(*RECHECK_DELAYS, replace=True)
                self.rechecking.emit()
