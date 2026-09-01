"""Watch the public-IP resolver work, live.

    python tools/diagnose-wanip.py [seconds]

Runs the real WanIpResolver and prints every adapter change, every scheduled
lookup and every provider result, so a VPN can be switched on and off while it
watches. Nothing is written and nothing else in NetPulse is started.
"""
from __future__ import annotations

import socket
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netpulse.collectors import wanip  # noqa: E402
from netpulse.collectors.wanip import (ENDPOINTS, WanIpResolver, fetch_text,  # noqa: E402
                                       network_fingerprint, parse_ip)

try:
    import psutil
except ImportError:
    psutil = None

RUN_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 180


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(line: str) -> None:
    print(f"[{stamp()}] {line}", flush=True)


def show_adapters() -> None:
    """Every adapter, its state, and whether the resolver counts it."""
    if psutil is None:
        print("psutil is not installed — cannot list adapters.")
        return
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    print(f"{'Adapter':38} {'Up':>4}  {'IPv4':16} Counted")
    print("-" * 74)
    for name in sorted(addrs):
        st = stats.get(name)
        up = bool(st and st.isup)
        ipv4 = [e.address for e in addrs[name] if e.family == socket.AF_INET]
        shown = ipv4[0] if ipv4 else "—"
        counted = up and any(wanip.is_usable_ipv4(a) for a in ipv4)
        print(f"{name[:38]:38} {'yes' if up else 'no':>4}  {shown:16} "
              f"{'yes' if counted else 'no'}")
    print()


def try_every_provider() -> None:
    """One pass through the list, reporting each answer separately."""
    print(f"{'Provider':16} {'Result':40} Time")
    print("-" * 74)
    for url, name in ENDPOINTS:
        started = time.time()
        try:
            raw = fetch_text(url)
            address = parse_ip(raw)
            result = address or f"unrecognised: {raw.strip()[:30]!r}"
        except Exception as exc:
            result = f"FAILED — {type(exc).__name__}: {exc}"
        print(f"{name:16} {result[:40]:40} {time.time() - started:.1f}s")
    print()


def main() -> int:
    print("=" * 74)
    print("NetPulse — public IP diagnosis")
    print("=" * 74)
    print()

    print("1. Network adapters as the resolver sees them")
    print()
    show_adapters()

    print("2. Every provider, tried once")
    print()
    try_every_provider()

    print("3. Live watch — switch your VPN on and off now")
    print(f"   Running for {RUN_SECONDS}s. Ctrl-C to stop early.")
    print()

    resolver = WanIpResolver()
    resolver.resolved.connect(
        lambda address, source: say(
            f"RESOLVED  {address or '(blank)'}"
            + (f"  via {source}" if source else "")))
    resolver.rechecking.connect(lambda: say("network changed — re-checking"))

    previous = network_fingerprint()
    say(f"starting fingerprint: {len(previous)} adapter(s) up with an address")
    resolver.start()

    deadline = time.time() + RUN_SECONDS
    try:
        while time.time() < deadline:
            time.sleep(1.0)
            current = network_fingerprint()
            if current != previous:
                gone = {n for n, _ in previous} - {n for n, _ in current}
                new = {n for n, _ in current} - {n for n, _ in previous}
                for name in sorted(new):
                    say(f"  adapter UP    {name}")
                for name in sorted(gone):
                    say(f"  adapter DOWN  {name}")
                if not new and not gone:
                    say("  an adapter changed address")
                previous = current
    except KeyboardInterrupt:
        print()
    finally:
        resolver.stop()

    print()
    print("=" * 74)
    print(f"Final address: {resolver.address or '(blank)'}"
          f"{'  via ' + resolver.source if resolver.source else ''}")
    print(f"Failed lookups since the last success: {resolver.failures}")
    if resolver.last_error:
        print(f"Last error: {resolver.last_error}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
