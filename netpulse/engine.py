"""The background collection engine: samples, stores, rolls up and prunes."""
from __future__ import annotations

import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Signal

from .collectors.files import FileTracker
from .collectors.net_etw import EtwNetCollector
from .collectors.net_system import (DIRECT, VPN, SystemNetCollector,
                                    vpn_active)
from .collectors.wanip import WanIpResolver

LIVE_WINDOW_SECONDS = 120


class Engine(QObject):
    """Owns every collector and the write path into the database.

    Runs on its own thread so the interface never waits on I/O.  Qt signals are
    emitted across the thread boundary, which Qt delivers as queued calls on the
    GUI thread.
    """

    tick = Signal(float, float)          # download bytes/s, upload bytes/s
    file_found = Signal(dict)
    status_changed = Signal(str)

    def __init__(self, db, settings) -> None:
        super().__init__()
        self.db = db
        self.settings = settings
        self.system = SystemNetCollector()
        self.etw = EtwNetCollector()
        self.files = FileTracker(db, settings, on_new=self._on_file,
                                 link_of=self.current_link)
        self.wan = WanIpResolver()
        #: (timestamp, direct down, direct up, vpn down, vpn up) in bytes/sec
        self.live: deque[tuple[float, float, float, float, float]] = deque(
            maxlen=LIVE_WINDOW_SECONDS)
        self.session_down = 0
        self.session_up = 0
        self.started_at = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_rollup = 0.0
        self._last_prune = 0.0

    # ------------------------------------------------------------------ state
    @property
    def per_app_available(self) -> bool:
        return self.etw.available

    @property
    def per_app_reason(self) -> str:
        return self.etw.reason

    def per_app_note(self) -> str:
        """Empty when healthy, otherwise a sentence explaining what is wrong."""
        if not self.settings.get("track_per_app", True):
            return "Per-application tracking is switched off in Settings."
        if not self.etw.available:
            return self.etw.reason or "Per-application tracking is unavailable."
        if self.etw.events_seen == 0 and time.time() - self.started_at > 45:
            return ("The trace session started but Windows has not delivered any "
                    "network events. Another tracing tool may be holding the "
                    "session — restarting NetPulse usually clears it.")
        return ""

    def status_text(self) -> str:
        if self.settings.get("paused"):
            return "Paused — not recording"
        parts = ["Recording"]
        parts.append("per-app on" if self.etw.available else "totals only")
        if self.settings.get("track_files"):
            parts.append("file log on" if self.files.status.startswith("watching")
                         else "file log idle")
        return " · ".join(parts)

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        if self.settings.get("track_per_app", True):
            self.etw.start()
        if self.settings.get("track_files", True):
            self.files.start()
        if self.settings.get("show_wan_ip", True):
            self.wan.start()
        self.db.rollup(since=0)          # heal anything a previous crash left behind
        self.system.reset()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="netpulse-engine", daemon=True)
        self._thread.start()
        self.status_changed.emit(self.status_text())

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self.files.stop()
        self.etw.stop()
        self.wan.stop()
        try:
            self.db.rollup(since=time.time() - 7200)
        except Exception:
            pass

    def set_paused(self, paused: bool) -> None:
        self.settings.set("paused", bool(paused))
        if not paused:
            self.system.reset()          # do not bank the traffic that ran while paused
        self.status_changed.emit(self.status_text())

    def enable_wan_ip(self, enabled: bool) -> None:
        self.settings.set("show_wan_ip", bool(enabled))
        if enabled:
            self.wan.start()
            self.wan.refresh_now()
        else:
            self.wan.stop()

    def enable_per_app(self, enabled: bool) -> None:
        self.settings.set("track_per_app", bool(enabled))
        if enabled and not self.etw.available:
            self.etw.start()
        elif not enabled:
            self.etw.stop()
        self.status_changed.emit(self.status_text())

    # ------------------------------------------------------------------- loop
    def _run(self) -> None:
        interval = max(0.25, self.settings.get("sample_interval_ms", 1000) / 1000)
        while not self._stop.wait(interval):
            try:
                self._sample_once()
            except Exception:
                pass
            now = time.time()
            if now - self._last_rollup > 15:
                self._last_rollup = now
                try:
                    self.db.rollup()
                except Exception:
                    pass
            if now - self._last_prune > 3600:
                self._last_prune = now
                try:
                    self.db.prune(
                        int(self.settings.get("retain_minute_days", 7)),
                        int(self.settings.get("retain_hour_days", 90)),
                        int(self.settings.get("retain_day_days", 0)),
                    )
                except Exception:
                    pass

    def current_link(self) -> str:
        """Which side new activity belongs to right now."""
        return VPN if vpn_active() else DIRECT

    def _sample_once(self) -> None:
        if self.settings.get("paused"):
            self.system.sample()          # keep the baseline current, discard the value
            self.etw.drain()
            self.live.append((time.time(), 0.0, 0.0, 0.0, 0.0))
            self.tick.emit(0.0, 0.0)
            return

        totals = self.system.sample()
        rates = self.system.last_rates
        per_app = self.etw.drain() if self.etw.available else {}

        moved = sum(d + u for d, u in totals.values())
        self.session_down += sum(d for d, _ in totals.values())
        self.session_up += sum(u for _, u in totals.values())
        self.live.append((time.time(),
                          rates[DIRECT][0], rates[DIRECT][1],
                          rates[VPN][0], rates[VPN][1]))
        if moved or per_app:
            self.db.add_samples(totals, per_app)

        rate_down, rate_up = self.system.last_rate
        self.tick.emit(rate_down, rate_up)

    # ------------------------------------------------------------------ files
    def _on_file(self, record: dict) -> None:
        self.file_found.emit(record)

    # ---------------------------------------------------------------- helpers
    def live_series(self, link: str | None = None
                    ) -> tuple[list[float], list[float], list[float]]:
        """Recent rates as (offsets, download, upload).

        ``link`` selects one side; None combines them, which is what the tray
        icon and the sidebar want.
        """
        snapshot = list(self.live)
        if not snapshot:
            return [], [], []
        now = time.time()
        xs = [row[0] - now for row in snapshot]
        if link == DIRECT:
            return xs, [r[1] for r in snapshot], [r[2] for r in snapshot]
        if link == VPN:
            return xs, [r[3] for r in snapshot], [r[4] for r in snapshot]
        return (xs,
                [r[1] + r[3] for r in snapshot],
                [r[2] + r[4] for r in snapshot])
