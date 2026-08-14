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
EXCLUDE_HINTS = (
    "loopback", "pseudo-interface", "vethernet", "vmware", "virtualbox",
    "isatap", "teredo", "bluetooth", "npcap", "hyper-v", "wsl", "docker",
)


def is_real_adapter(name: str) -> bool:
    low = name.lower()
    if low in ("lo", "lo0"):
        return False
    return not any(h in low for h in EXCLUDE_HINTS)


class SystemNetCollector:
    """Delta sampler over per-adapter byte counters."""

    def __init__(self, interfaces: list[str] | None = None) -> None:
        self.interfaces = interfaces or []      # empty = auto-select
        self._prev: dict[str, tuple[int, int]] = {}
        self._last_ts: float = 0.0
        self.last_rate: tuple[float, float] = (0.0, 0.0)   # bytes/sec down, up
        self.reset()

    # ------------------------------------------------------------------
    def available_interfaces(self) -> list[str]:
        if psutil is None:
            return []
        try:
            return sorted(psutil.net_io_counters(pernic=True).keys())
        except Exception:
            return []

    def _selected(self, counters: dict) -> list[str]:
        if self.interfaces:
            return [n for n in counters if n in self.interfaces]
        return [n for n in counters if is_real_adapter(n)]

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
    def sample(self) -> tuple[int, int]:
        """Bytes (down, up) transferred since the previous call."""
        if psutil is None:
            return 0, 0
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            return 0, 0

        now = time.time()
        elapsed = max(0.001, now - self._last_ts)
        down = up = 0
        for name in self._selected(counters):
            c = counters[name]
            cur = (c.bytes_recv, c.bytes_sent)
            prev = self._prev.get(name)
            self._prev[name] = cur
            if prev is None:
                continue                      # first sight of this adapter
            d, u = cur[0] - prev[0], cur[1] - prev[1]
            # Counters reset when an adapter is disabled or the driver reloads.
            down += d if d >= 0 else 0
            up += u if u >= 0 else 0

        # Drop adapters that disappeared so they cannot resurrect stale numbers.
        for gone in set(self._prev) - set(counters):
            self._prev.pop(gone, None)

        self._last_ts = now
        self.last_rate = (down / elapsed, up / elapsed)
        return down, up
