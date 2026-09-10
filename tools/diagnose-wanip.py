"""Watch the public-IP resolver work, live.

    python tools/diagnose-wanip.py [seconds]

Runs the real WanIpResolver and prints every adapter change, every scheduled
lookup and every provider result, so a VPN can be switched on and off while it
watches. Nothing is written and nothing else in NetPulse is started.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netpulse.collectors import wanip  # noqa: E402
from netpulse.collectors.wanip import (ENDPOINTS, WanIpResolver,  # noqa: E402
                                       describe_error, fetch_text,
                                       network_fingerprint, parse_ip)

try:
    import psutil
except ImportError:
    psutil = None

ARGS = sys.argv[1:]
#: Write to a file instead of the console. pythonw.exe has no console at all,
#: so this is the only way to see what it did.
OUT_FILE = ""
if "--out" in ARGS:
    index = ARGS.index("--out")
    OUT_FILE = ARGS[index + 1]
    del ARGS[index:index + 2]
#: Run the provider list under this interpreter *and* its windowed twin.
COMPARE = "--compare" in ARGS
if COMPARE:
    ARGS.remove("--compare")
#: 0 means "providers only" — no live watch.
RUN_SECONDS = int(ARGS[0]) if ARGS else 180


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


def elevated() -> bool | None:
    """Whether this process holds administrator rights, or None off Windows."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def show_context() -> None:
    """The two things that differ between this script and the running app."""
    state = elevated()
    if state is None:
        print("Running elevated: not applicable on this platform")
    else:
        print(f"Running elevated: {'YES — as administrator' if state else 'no'}")
    print()


def show_proxy() -> None:
    """Whatever Windows currently reports as the system proxy.

    A stale copy of this, cached inside urllib for the life of the process, is
    one way a long-running app fails while a fresh script succeeds — so it is
    worth seeing rather than assuming.
    """
    import urllib.request
    proxies = urllib.request.getproxies()
    if proxies:
        print("System proxy configuration:")
        for scheme, value in sorted(proxies.items()):
            print(f"  {scheme:8} {value}")
    else:
        print("System proxy configuration: none")
    print()


def try_every_provider() -> str:
    """One pass through the list, reporting each answer separately.

    Returns the first address obtained, so two interpreters can be compared.
    """
    found = ""
    print(f"{'Provider':16} {'Result':52} Time")
    print("-" * 74)
    for url, name in ENDPOINTS:
        started = time.time()
        try:
            raw = fetch_text(url)
            address = parse_ip(raw)
            found = found or address
            result = address or f"unrecognised: {raw.strip()[:30]!r}"
        except Exception as exc:
            result = f"FAILED — {describe_error(exc)}"
        print(f"{name:16} {result[:52]:52} {time.time() - started:.1f}s")
    print()
    return found


def windowed_twin() -> Path | None:
    """pythonw.exe beside this python.exe, if it exists.

    The app is launched with the windowed interpreter so it has no console
    window. That makes it a *different executable* to the one every diagnostic
    has used — and per-application VPN rules, split tunnelling and firewalls
    all match on the executable. Two interpreters, two network paths, and no
    way to see it without running both.
    """
    override = os.environ.get("NETPULSE_ALT_PYTHON")
    if override:
        return Path(override)
    twin = Path(sys.executable).with_name("pythonw.exe")
    return twin if twin.exists() else None


def compare_verdict(mine: str, theirs: str, twin_name: str) -> str:
    """What two interpreters seeing two different public addresses means."""
    if mine and theirs and mine != theirs:
        return (
            "VERDICT: the two interpreters have DIFFERENT public addresses.\n"
            "They are taking different routes to the internet, so a\n"
            "per-application rule — a VPN bypass or split-tunnel list, or a\n"
            "firewall rule — is matching one executable and not the other.\n"
            f"The app runs {twin_name}, so that is the name to look for in\n"
            "Surfshark's Bypasser list.")
    if mine and theirs:
        return ("VERDICT: both interpreters see the same address, so the app's\n"
                "traffic takes the same route as this script's. The difference\n"
                "is somewhere else.")
    if mine and not theirs:
        return (f"VERDICT: {twin_name} could not reach any provider while this\n"
                "one could. Same conclusion — something is treating the two\n"
                "executables differently — and it is blocking rather than\n"
                "merely rerouting.")
    if theirs and not mine:
        return (f"VERDICT: only {twin_name} could reach a provider, which is\n"
                "the reverse of the reported fault. Worth re-running.")
    return ("VERDICT: neither could reach a provider — nothing to compare.\n"
            "Check that the VPN is connected and try again.")


def compare_interpreters(mine: str) -> int:
    """Run the same provider sweep under the windowed interpreter."""
    twin = windowed_twin()
    print("=" * 74)
    if twin is None:
        print("No windowed interpreter (pythonw.exe) found beside this one, so")
        print("there is nothing to compare against.")
        print("=" * 74)
        return 0

    print(f"Now the same lookup under {twin.name}, which is what the app runs.")
    print()
    scratch = Path(tempfile.gettempdir()) / "netpulse-pythonw-check.txt"
    try:
        subprocess.run([str(twin), os.path.abspath(__file__), "0",
                        "--out", str(scratch)], timeout=180, check=False)
        report = scratch.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"Could not run it: {type(exc).__name__}: {exc}")
        print("=" * 74)
        return 1

    theirs = ""
    for line in report.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                ipaddress.ip_address(parts[1])
                theirs = parts[1]
                break
            except ValueError:
                continue

    for line in report.splitlines():
        if line.strip() and not line.startswith("="):
            print(line)

    print("=" * 74)
    print(f"  {Path(sys.executable).name:16} saw {mine or '(nothing)'}")
    print(f"  {twin.name:16} saw {theirs or '(nothing)'}")
    print()
    print(compare_verdict(mine, theirs, twin.name))
    print("=" * 74)
    return 0


def main() -> int:
    if OUT_FILE:
        # pythonw has no console; everything has to go to the file.
        sys.stdout = open(OUT_FILE, "w", encoding="utf-8", buffering=1)

    print("=" * 74)
    print("NetPulse — public IP diagnosis")
    print("=" * 74)
    print()

    print("1. Network adapters as the resolver sees them")
    print()
    show_adapters()

    print("2. Proxy settings and every provider, tried once")
    print()
    show_context()
    show_proxy()
    mine = try_every_provider()

    if COMPARE:
        return compare_interpreters(mine)
    if RUN_SECONDS <= 0:
        return 0

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
