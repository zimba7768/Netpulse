"""Machine-wide upload/download sampling from network adapter counters.

This is the authoritative source for totals: it reads the same byte counters
Windows itself reports, works without administrator rights, and never misses
traffic.  Per-application attribution is layered on top by ``net_etw``.
"""
from __future__ import annotations

import time

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard requirement at runtime
    psutil = None

# Adapters that would double-count or report purely local traffic.
#: Adapters that would double-count or report purely local traffic.
EXCLUDE_HINTS = (
    "loopback", "pseudo-interface", "vethernet", "vmware", "virtualbox",
    "isatap", "teredo", "bluetooth", "npcap", "hyper-v", "wsl", "docker",
)

#: Adapters that carry a VPN tunnel. Traffic here is also carried, encrypted,
#: by whichever physical adapter is underneath — which is why they have to be
#: told apart rather than added together.
TUNNEL_HINTS = (
    "wireguard", "wintun", "openvpn", "tap-windows", "tap adapter",
    "nordlynx", "surfshark", "protonvpn", "mullvad", "expressvpn",
    "cyberghost", "private internet access", "pia ", "tunnelbear",
    "windscribe", "zerotier", "tailscale", "wg-", "tun", "vpn",
)

DIRECT = "direct"
VPN = "vpn"
IGNORED = "ignored"


def classify_adapter(name: str) -> str:
    """Sort an adapter into direct, tunnel, or not-to-be-counted."""
    low = (name or "").lower()
    if low in ("lo", "lo0") or any(h in low for h in EXCLUDE_HINTS):
        return IGNORED
    if any(h in low for h in TUNNEL_HINTS):
        return VPN
    return DIRECT


def is_real_adapter(name: str) -> bool:
    """Kept for callers that only care whether an adapter counts at all."""
    return classify_adapter(name) != IGNORED


def vpn_active() -> bool:
    """True when a tunnel adapter is currently up."""
    if psutil is None:
        return False
    try:
        stats = psutil.net_if_stats()
    except Exception:
        return False
    return any(status.isup and classify_adapter(name) == VPN
               for name, status in stats.items())


class SystemNetCollector:
    """Delta sampler over per-adapter byte counters, split by link.

    A VPN adapter and the physical adapter beneath it both count the same
    conversation — once as plaintext entering the tunnel, once as ciphertext on
    the wire. Adding them together doubles every figure while a VPN is
    connected, so they are measured separately and *direct* is what remains
    once the tunnel's share is taken off.
    """

    def __init__(self, interfaces: list[str] | None = None) -> None:
        self.interfaces = interfaces or []      # empty = auto-select
        self._prev: dict[str, tuple[int, int]] = {}
        self._last_ts: float = 0.0
        #: bytes/sec, per link: {"direct": (down, up), "vpn": (down, up)}
        self.last_rates: dict[str, tuple[float, float]] = {
            DIRECT: (0.0, 0.0), VPN: (0.0, 0.0)}
        self.reset()

    # ------------------------------------------------------------------
    def available_interfaces(self) -> list[str]:
        if psutil is None:
            return []
        try:
            return sorted(psutil.net_io_counters(pernic=True).keys())
        except Exception:
            return []

    def classified_interfaces(self) -> dict[str, str]:
        """Every adapter and how it is being treated — shown in Settings."""
        return {name: classify_adapter(name) for name in self.available_interfaces()}

    def _selected(self, counters: dict) -> list[str]:
        if self.interfaces:
            return [n for n in counters if n in self.interfaces]
        return [n for n in counters if classify_adapter(n) != IGNORED]

    def reset(self) -> None:
        """Re-baseline so the next sample does not report a huge first delta."""
        self._prev = {}
        self._last_ts = time.time()
        if psutil is None:
            return
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            return
        for name in self._selected(counters):
            c = counters[name]
            self._prev[name] = (c.bytes_recv, c.bytes_sent)

    # ------------------------------------------------------------------
    def sample(self) -> dict[str, tuple[int, int]]:
        """Bytes transferred since the previous call, per link.

        Returns {"direct": (down, up), "vpn": (down, up)}.
        """
        empty = {DIRECT: (0, 0), VPN: (0, 0)}
        if psutil is None:
            return empty
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            return empty

        now = time.time()
        elapsed = max(0.001, now - self._last_ts)
        physical_down = physical_up = 0
        vpn_down = vpn_up = 0

        for name in self._selected(counters):
            c = counters[name]
            cur = (c.bytes_recv, c.bytes_sent)
            prev = self._prev.get(name)
            self._prev[name] = cur
            if prev is None:
                continue                      # first sight of this adapter
            down, up = cur[0] - prev[0], cur[1] - prev[1]
            # Counters reset when an adapter is disabled or the driver reloads.
            down = down if down >= 0 else 0
            up = up if up >= 0 else 0
            if classify_adapter(name) == VPN:
                vpn_down += down
                vpn_up += up
            else:
                physical_down += down
                physical_up += up

        # Drop adapters that disappeared so they cannot resurrect stale numbers.
        for gone in set(self._prev) - set(counters):
            self._prev.pop(gone, None)

        # The tunnel's bytes are already inside the physical total, wrapped in
        # encryption. Subtracting leaves what genuinely bypassed the tunnel —
        # plus a few percent of protocol overhead, which is real traffic on the
        # wire and has to live somewhere.
        direct_down = max(0, physical_down - vpn_down)
        direct_up = max(0, physical_up - vpn_up)

        self._last_ts = now
        self.last_rates = {
            DIRECT: (direct_down / elapsed, direct_up / elapsed),
            VPN: (vpn_down / elapsed, vpn_up / elapsed),
        }
        return {DIRECT: (direct_down, direct_up), VPN: (vpn_down, vpn_up)}

    @property
    def last_rate(self) -> tuple[float, float]:
        """Combined rate, for the tray icon and the sidebar."""
        direct = self.last_rates[DIRECT]
        vpn = self.last_rates[VPN]
        return (direct[0] + vpn[0], direct[1] + vpn[1])
