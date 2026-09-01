"""Measure the direct/VPN split against the raw adapter counters.

    python tools/diagnose-split.py [seconds]

Reads exactly what NetPulse reads, and shows its working: the per-adapter byte
deltas, the split derived from them, and what proportion of the physical
adapter's traffic could not be accounted for by the tunnel.

Some direct traffic while a VPN is up is correct — encryption overhead is real
traffic on the wire, and LAN traffic never enters the tunnel. This exists to
tell "a few percent of overhead" apart from "the split is not working".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netpulse.collectors.net_system import (DIRECT, IGNORED, VPN,  # noqa: E402
                                            SystemNetCollector,
                                            classify_adapter, vpn_active)
from netpulse.units import format_bytes  # noqa: E402

try:
    import psutil
except ImportError:
    psutil = None

ACTIVE_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 45
BASELINE_SECONDS = 15

#: A verdict needs enough tunnelled traffic that per-packet overhead is a small
#: fraction of it. Below this the percentages are dominated by idle chatter and
#: say nothing useful — which is exactly how an earlier version of this tool
#: managed to report 82% "unexplained" on a machine doing nothing.
MIN_TUNNEL_BYTES = 2_000_000

#: Direct traffic above the measured background, as a share of tunnelled
#: traffic. Encryption overhead alone lands well under the first figure.
OVERHEAD_OK = 0.12
OVERHEAD_SUSPECT = 0.30


def show_classification() -> None:
    print("How each adapter is being treated")
    print()
    print(f"{'Adapter':40} {'Up':>4}  Treated as")
    print("-" * 68)
    stats = psutil.net_if_stats()
    for name in sorted(psutil.net_io_counters(pernic=True)):
        kind = classify_adapter(name)
        st = stats.get(name)
        up = "yes" if st and st.isup else "no"
        label = {DIRECT: "direct (physical)", VPN: "VPN tunnel",
                 IGNORED: "ignored"}[kind]
        print(f"{name[:40]:40} {up:>4}  {label}")
    print()


def measure(collector: SystemNetCollector, seconds: int,
            label: str) -> dict[str, list[int]]:
    """Total both links over a period, printing progress as it goes."""
    totals = {DIRECT: [0, 0], VPN: [0, 0]}
    collector.sample()                    # discard the delta since the last phase
    started = time.time()
    while time.time() - started < seconds:
        time.sleep(1.0)
        sample = collector.sample()
        for link in (DIRECT, VPN):
            totals[link][0] += sample[link][0]
            totals[link][1] += sample[link][1]
        left = seconds - int(time.time() - started)
        print(f"\r  {label}: {left:3d}s left   "
              f"direct {format_bytes(totals[DIRECT][0]):>10}   "
              f"tunnel {format_bytes(totals[VPN][0]):>10}   ",
              end="", flush=True)
    print()
    return totals


def verdict(baseline_rate: float, active: dict[str, list[int]],
            seconds: int) -> None:
    tunnel = active[VPN][0] + active[VPN][1]
    direct = active[DIRECT][0] + active[DIRECT][1]
    background = baseline_rate * seconds
    unexplained = max(0.0, direct - background)

    print(f"  Background traffic, from the idle phase: "
          f"{format_bytes(int(baseline_rate))}/s")
    print(f"  Expected from background over {seconds}s: "
          f"{format_bytes(int(background))}")
    print(f"  Direct recorded while downloading:       {format_bytes(direct)}")
    print(f"  Direct above background:                 "
          f"{format_bytes(int(unexplained))}")
    print(f"  Tunnelled:                               {format_bytes(tunnel)}")
    print()

    if tunnel < MIN_TUNNEL_BYTES:
        print("VERDICT: not enough tunnelled traffic to judge — only "
              f"{format_bytes(tunnel)} went")
        print("through the VPN. Run it again and start a large download while")
        print("it measures; a few hundred KB of idle chatter cannot tell")
        print("overhead apart from a leak.")
        return

    share = unexplained / tunnel
    print(f"Direct traffic beyond the background, as a share of tunnelled: "
          f"{share:.1%}")
    print()
    if share <= OVERHEAD_OK:
        print("VERDICT: working correctly. What the Dashboard shows while the")
        print("VPN is up is encryption overhead and local network traffic —")
        print("both real, neither leaking out of the tunnel.")
    elif share <= OVERHEAD_SUSPECT:
        print("VERDICT: a little more than overhead. Most likely something is")
        print("deliberately bypassing the tunnel — check Surfshark's Bypasser")
        print("list, and whether your DNS server is on the LAN.")
    else:
        print("VERDICT: too much to be overhead. A real share of your traffic")
        print("is not entering the tunnel. Send me this output.")


def main() -> int:
    if psutil is None:
        print("psutil is not installed.")
        return 1

    print("=" * 68)
    print("NetPulse — direct / VPN split")
    print("=" * 68)
    print()
    show_classification()

    if not vpn_active():
        print("No tunnel adapter is up. Connect your VPN and run this again.")
        return 1

    collector = SystemNetCollector()

    # Background first. A machine on a home network receives a constant
    # trickle of broadcast traffic — ARP, mDNS, SSDP, a local DNS server —
    # which never enters the tunnel and so is correctly counted as direct.
    # Measuring it separately is the difference between explaining the
    # Dashboard's figure and merely being alarmed by it.
    print(f"Phase 1 of 2 — background, {BASELINE_SECONDS}s.")
    print("Leave the machine alone: no browsing, no downloads.")
    print()
    idle = measure(collector, BASELINE_SECONDS, "idle  ")
    baseline_rate = (idle[DIRECT][0] + idle[DIRECT][1]) / BASELINE_SECONDS
    print()

    print(f"Phase 2 of 2 — under load, {ACTIVE_SECONDS}s.")
    print("Start a large download NOW — a Linux ISO, a game update, anything")
    print("that will move a few hundred MB. Press Enter when it is running.")
    try:
        input()
    except EOFError:
        pass
    active = measure(collector, ACTIVE_SECONDS, "active")
    print()

    print("=" * 68)
    verdict(baseline_rate, active, ACTIVE_SECONDS)
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
