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
    # Plain, ad-free and rarely caught by the DNS filtering some VPN clients
    # switch on by default, which is exactly when the others start failing.
    ("https://checkip.amazonaws.com", "amazonaws.com"),
    ("https://icanhazip.com", "icanhazip.com"),
    ("https://ifconfig.me/ip", "ifconfig.me"),
    ("https://ipinfo.io/ip", "ipinfo.io"),
    # Last resort, and the important one: an IP literal, so it needs no DNS at
    # all. Every provider above dies the moment name resolution is filtered —
    # by ad-blocking DNS, a router, or a VPN client pushing its own resolver —
    # which is exactly the situation where the address has just changed and is
    # most worth knowing. Cloudflare's certificate covers the address itself,
    # so TLS still validates without a hostname.
    ("https://1.1.1.1/cdn-cgi/trace", "1.1.1.1 (no DNS)"),
]

#: Generous, because the first attempts happen while a VPN tunnel is still
#: settling and everything is slow.
TIMEOUT = 8.0
#: A bare address is under 50 bytes; the trace endpoint answers with a few
#: short lines. Anything larger is an error page, and is rejected on parsing.
MAX_BYTES = 512
USER_AGENT = "NetPulse/1.0 (+https://github.com/zimba7768/Netpulse)"

#: How often the adapter fingerprint is compared. Local only, so it is cheap.
POLL_SECONDS = 2.0

#: After a local network change, look again on this ladder (seconds). A VPN
#: adapter appears before its routes are ready, so the first answer can still
#: be the old address; the later attempts catch it once traffic actually moves.
RECHECK_DELAYS = (2.0, 6.0, 15.0, 30.0)

#: When a lookup fails, back off through these instead of dropping straight to
#: the slow periodic check — otherwise one failed attempt (a VPN mid-connect,
#: a moment offline) leaves the address blank for a quarter of an hour.
RETRY_BACKOFF = (5.0, 15.0, 45.0, 120.0, 300.0)


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
    # Cloudflare's trace endpoint answers with "key=value" lines rather than a
    # bare address. That is a defined format, not prose, so reading one named
    # field out of it is safe in a way that hunting for digits in free text
    # would not be.
    if "ip=" in text:
        for line in text.splitlines():
            if line.startswith("ip="):
                candidate = line[3:].strip()
                break
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    if address.is_private or address.is_loopback or address.is_unspecified:
        return ""          # a proxy answered with a LAN address — not our WAN IP
    return str(address)


def is_usable_ipv4(address: str) -> bool:
    """Ignore addresses that say 'no working connection here'.

    169.254.x.x is what Windows assigns when DHCP fails, and it hands those out
    to every disconnected adapter — of which a laptop with several virtual
    Wi-Fi and VPN interfaces has many. They are reassigned on their own
    schedule, so counting them makes the fingerprint churn for no reason.
    """
    return bool(address) and not address.startswith(("127.", "169.254."))


def network_fingerprint() -> tuple:
    """A cheap snapshot of local network configuration.

    Only adapters that are **up**, with their usable IPv4 addresses. A VPN
    connecting or dropping always changes that; a disconnected adapter idly
    renumbering itself does not.

    IPv6 is deliberately left out: privacy extensions rotate temporary
    addresses on a timer, which would look like a network change every few
    hours and trigger pointless lookups.
    """
    if psutil is None:
        return ()
    try:
        stats = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
    except Exception:
        return ()

    items: list[tuple[str, tuple[str, ...]]] = []
    for name in sorted(addresses):
        status = stats.get(name)
        if status is None or not status.isup:
            continue                       # down adapters are noise, not signal
        ipv4 = tuple(sorted(
            entry.address for entry in addresses[name]
            if entry.family == socket.AF_INET and is_usable_ipv4(entry.address)
        ))
        if ipv4:
            items.append((name, ipv4))
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
        self.failures = 0
        #: True once the local network has changed and no lookup has confirmed
        #: an address since — i.e. whatever is displayed can no longer be
        #: trusted, as opposed to merely being unconfirmed.
        self.suspect = False
        self.last_error = ""
        #: Unexpected errors caught by the loop itself, as opposed to a
        #: provider simply not answering. Non-zero means something is wrong
        #: that is worth reporting rather than quietly retrying.
        self.loop_errors = 0
        #: The provider that answered most recently, tried first next time.
        self.preferred = ""
        self._announced = False
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

    def schedule_recheck(self) -> None:
        """Queue the post-network-change ladder.

        A VPN connecting is not one event: adapters appear, get a placeholder
        address, then a real one, and the client may switch between its own
        interfaces. Replacing the queue outright each time would keep pushing
        the imminent lookup further away, so a burst of changes could stop any
        lookup from ever running. Anything already about to fire is kept.
        """
        now = time.time()
        with self._due_lock:
            imminent = [when for when in self._due if when <= now + 3.0]
            self._due = sorted(imminent + [now + d for d in RECHECK_DELAYS])

    def _pending(self) -> bool:
        with self._due_lock:
            return bool(self._due)

    def _clear_due(self) -> None:
        with self._due_lock:
            self._due.clear()

    # ----------------------------------------------------------------- fetch
    def lookup(self) -> tuple[str, str]:
        """Try each provider until one gives a valid address.

        Whichever answered last is tried first. On a network that filters some
        of these by name — ad-blocking DNS, a router filter, a VPN client's
        "clean browsing" option — the blocked ones fail every time, and
        starting from a known-good provider turns a slow walk through the list
        into a single request.
        """
        reasons: list[str] = []
        order = list(ENDPOINTS)
        if self.preferred:
            order.sort(key=lambda item: item[0] != self.preferred)
        for url, name in order:
            if self._stop.is_set():
                break
            try:
                address = parse_ip(fetch_text(url))
            except Exception as exc:           # offline, blocked, timed out
                reasons.append(f"{name}: {type(exc).__name__}")
                continue
            if address:
                self.last_error = ""
                self.preferred = url
                return address, name
            reasons.append(f"{name}: unrecognised reply")
        # Kept so the interface can explain itself rather than just saying no.
        self.last_error = " · ".join(reasons)
        return "", ""

    def check(self) -> bool:
        """Perform one lookup. Returns True when the address changed."""
        address, source = self.lookup()
        self.checked_at = time.time()

        if address:
            changed = address != self.address or source != self.source
            self.address, self.source = address, source
            self.failures = 0
            self.suspect = False
            if changed or not self._announced:
                self._announced = True
                self.resolved.emit(address, source)
            return changed

        self.failures += 1
        if self.suspect and self.address and self.failures >= 2:
            # The network changed and we still cannot confirm an address, so
            # what is on screen is probably wrong. Better blank than lying.
            self.address, self.source = "", ""
            self.resolved.emit("", "")
        elif not self._announced:
            self._announced = True
            self.resolved.emit("", "")
        # Otherwise keep displaying the last known address: a single failed
        # request is no reason to believe it has changed.
        return False

    def next_delay(self) -> float:
        """How long to wait before the next lookup."""
        if self.failures:
            return RETRY_BACKOFF[min(self.failures - 1, len(RETRY_BACKOFF) - 1)]
        return self.interval

    # ------------------------------------------------------------------ loop
    def _run_due(self) -> None:
        """Run any lookup that has come due, and queue the next one."""
        if not self._take_due(time.time()):
            return
        if self.check():
            self._clear_due()          # settled on a new address; drop retries
        if not self._pending():
            self.schedule(self.next_delay())

    def _watch_network(self) -> None:
        """Notice a local change and queue the re-check ladder."""
        current = network_fingerprint()
        if current != self.fingerprint:
            self.fingerprint = current
            self.suspect = True
            self.schedule_recheck()
            self.rechecking.emit()

    def _run(self) -> None:
        try:
            self.fingerprint = network_fingerprint()
        except Exception:
            self.fingerprint = ()

        while not self._stop.is_set():
            # Nothing in a single pass is worth the thread for. An unguarded
            # loop dies on the first unexpected error and takes the whole
            # feature with it silently: no more lookups, no more adapter
            # watching, and the interface goes on showing "retrying…" for the
            # rest of the session because nothing ever tells it otherwise.
            # Failing loudly into the tooltip and carrying on is strictly
            # better than a resolver that is quietly no longer there.
            try:
                self._run_due()
            except Exception as exc:
                self._record_loop_error(exc)

            self._wake.wait(POLL_SECONDS)
            if self._stop.is_set():
                break
            self._wake.clear()

            try:
                self._watch_network()
            except Exception as exc:
                self._record_loop_error(exc)

    def _record_loop_error(self, exc: BaseException) -> None:
        """Treat an unexpected error as a failed attempt and keep going."""
        self.failures += 1
        self.loop_errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        if not self.checked_at:
            # Nothing has ever succeeded, so the interface is still waiting on
            # a first answer. Give it one, or it waits forever.
            self.checked_at = time.time()
        if not self._pending():
            self.schedule(self.next_delay())

    @property
    def running(self) -> bool:
        """True while the background thread is alive."""
        return self._thread is not None and self._thread.is_alive()
