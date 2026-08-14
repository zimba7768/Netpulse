"""Per-application network attribution via ETW (Microsoft-Windows-Kernel-Network).

Windows does not expose per-process byte counters through any ordinary API —
Task Manager's own "Network" column comes from ETW, and so does this.  The
provider emits one event per TCP/UDP send and receive carrying the owning PID
and the payload size; we accumulate those per process.

Requirements
------------
* Windows only.
* The process must run **elevated** — starting a real-time kernel trace session
  needs SeSystemProfilePrivilege.
* ``pip install pywintrace``.

Every failure path is soft: if any of the above is missing the collector reports
``available = False`` with a human-readable ``reason`` and the application falls
back to machine-wide totals only.
"""
from __future__ import annotations

import sys
import threading
import time
from collections import defaultdict

from ..autostart import is_admin

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

KERNEL_NETWORK_GUID = "{7DD42A49-5329-4832-8DFD-43D979153A88}"

# Kernel-Network task/opcode ids.
SEND_EVENTS = {10, 26, 42, 58}   # TCP v4/v6 send, UDP v4/v6 send
RECV_EVENTS = {11, 27, 43, 59}   # TCP v4/v6 recv, UDP v4/v6 recv

LOOPBACK_PREFIXES = ("127.", "::1", "0.0.0.0")


def _pick(event: dict, *names: str):
    """Field names vary slightly between Windows builds — try a few spellings."""
    for n in names:
        if n in event:
            return event[n]
    lowered = {k.lower(): v for k, v in event.items()}
    for n in names:
        if n.lower() in lowered:
            return lowered[n.lower()]
    return None


def _as_int(value) -> int | None:
    """ETW property values arrive as strings, occasionally hex-formatted."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except (TypeError, ValueError):
        return None


class EtwNetCollector:
    """Accumulates per-PID byte counts on a background ETW session."""

    def __init__(self) -> None:
        self.available = False
        self.reason = ""
        self._job = None
        self._lock = threading.Lock()
        self._counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # pid -> [down, up]
        self._names: dict[int, str] = {}
        self._names_ts = 0.0
        self._running = False
        # Diagnostics: distinguishes "session never started" from "session
        # running but nothing is being decoded".
        self.events_seen = 0
        self.events_used = 0

    # ------------------------------------------------------------------ start
    def start(self) -> bool:
        if not sys.platform.startswith("win"):
            self.reason = "Per-app tracking requires Windows."
            return False
        if not is_admin():
            self.reason = ("Per-app tracking needs administrator rights — "
                           "start NetPulse with 'Run as administrator'.")
            return False
        try:
            from etw import ETW, ProviderInfo          # pywintrace
            from etw.GUID import GUID
        except Exception:
            self.reason = ("pywintrace is not installed — run "
                           "'pip install pywintrace' to enable per-app tracking.")
            return False

        try:
            provider = ProviderInfo(
                "Microsoft-Windows-Kernel-Network", GUID(KERNEL_NETWORK_GUID)
            )
            self._job = ETW(
                session_name="NetPulseKernelNet",
                providers=[provider],
                event_callback=self._on_event,
            )
            self._job.start()
        except Exception as exc:                        # session already exists, etc.
            self._job = None
            self.reason = f"Could not start the ETW session: {exc}"
            return False

        self._running = True
        self.available = True
        self.reason = ""
        self._refresh_names(force=True)
        return True

    def stop(self) -> None:
        self._running = False
        self.available = False
        if self._job is not None:
            try:
                self._job.stop()
            except Exception:
                pass
            self._job = None

    # --------------------------------------------------------------- callback
    def _on_event(self, event) -> None:
        """Hot path — keep this as cheap as possible."""
        try:
            event_id, data = event[0], event[1]
            if event_id in RECV_EVENTS:
                idx = 0
            elif event_id in SEND_EVENTS:
                idx = 1
            else:
                return
            self.events_seen += 1

            size = _as_int(_pick(data, "size", "Size"))
            if not size:
                return
            pid = _as_int(_pick(data, "PID", "pid", "ProcessId"))
            if pid is None:
                # Fall back to the record header when the payload omits it.
                header = data.get("EventHeader") or {}
                pid = _as_int(header.get("ProcessId"))
            if pid is None:
                return

            daddr = _pick(data, "daddr", "DestinationAddress")
            if isinstance(daddr, str) and daddr.startswith(LOOPBACK_PREFIXES):
                return                                   # ignore localhost traffic

            self.events_used += 1
            with self._lock:
                self._counts[pid][idx] += size
        except Exception:
            return

    # ------------------------------------------------------------------ names
    def _refresh_names(self, force: bool = False) -> None:
        if psutil is None:
            return
        now = time.time()
        if not force and now - self._names_ts < 15:
            return
        self._names_ts = now
        try:
            fresh = {}
            for proc in psutil.process_iter(["pid", "name"]):
                info = proc.info
                if info.get("name"):
                    fresh[info["pid"]] = info["name"]
            self._names.update(fresh)
            if len(self._names) > 4000:                  # keep the cache bounded
                self._names = fresh
        except Exception:
            pass

    def _name_for(self, pid: int) -> str:
        name = self._names.get(pid)
        if name:
            return name
        if psutil is not None:
            try:
                name = psutil.Process(pid).name()
                self._names[pid] = name
                return name
            except Exception:
                pass
        return f"PID {pid}" if pid > 0 else "System"

    # ------------------------------------------------------------------ drain
    def drain(self) -> dict[str, tuple[int, int]]:
        """Return {process name: (down, up)} accumulated since the last call."""
        if not self._running:
            return {}
        with self._lock:
            counts = self._counts
            self._counts = defaultdict(lambda: [0, 0])
        if not counts:
            return {}
        self._refresh_names()
        merged: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for pid, (down, up) in counts.items():
            name = self._name_for(pid)
            merged[name][0] += down
            merged[name][1] += up
        return {k: (v[0], v[1]) for k, v in merged.items()}
