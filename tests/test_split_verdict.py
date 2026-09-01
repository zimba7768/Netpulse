"""The direct/VPN diagnostic's verdict logic.

An earlier version reported "too much to be overhead" against 68 KB of traffic
from an idle machine — a false alarm produced by comparing percentages at a
volume where percentages mean nothing. These tests pin the thresholds and,
more importantly, the refusal to judge without enough data.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_tool():
    path = os.path.join(ROOT, "tools", "diagnose-split.py")
    spec = importlib.util.spec_from_file_location("diagnose_split", path)
    module = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["diagnose-split.py"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


tool = load_tool()
DIRECT, VPN = tool.DIRECT, tool.VPN

#: A home network's constant broadcast trickle, as measured on real hardware.
LAN_NOISE = 3500.0


def verdict_for(direct: int, tunnel: int, seconds: int = 45,
                baseline: float = LAN_NOISE) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        tool.verdict(baseline, {DIRECT: [direct, 0], VPN: [tunnel, 0]}, seconds)
    return buffer.getvalue()


class VerdictTests(unittest.TestCase):
    def test_an_idle_machine_is_not_judged(self) -> None:
        # The reported false alarm: 16 seconds of a machine doing nothing.
        out = verdict_for(direct=67_000, tunnel=27_300, seconds=16)
        self.assertIn("not enough tunnelled traffic", out)
        self.assertNotIn("too much to be overhead", out)

    def test_encryption_overhead_alone_passes(self) -> None:
        # 200 MB through the tunnel, 4% overhead, plus the LAN trickle.
        direct = int(200_000_000 * 0.04 + LAN_NOISE * 45)
        self.assertIn("working correctly",
                      verdict_for(direct, 200_000_000))

    def test_background_traffic_is_subtracted_before_judging(self) -> None:
        # Direct traffic that is entirely background must not count against it,
        # however long the run: LAN noise is real, and correctly on the
        # Dashboard, but it is not a leak.
        direct = int(LAN_NOISE * 45)
        self.assertIn("working correctly", verdict_for(direct, 200_000_000))

    def test_a_genuine_leak_is_called_out(self) -> None:
        self.assertIn("too much to be overhead",
                      verdict_for(100_000_000, 200_000_000))

    def test_a_bypass_list_reads_as_suspicious_not_broken(self) -> None:
        out = verdict_for(40_000_000, 200_000_000)
        self.assertIn("Bypasser", out)
        self.assertNotIn("too much to be overhead", out)

    def test_the_threshold_sits_between_overhead_and_a_leak(self) -> None:
        # Guards against a future edit quietly moving the line.
        self.assertLess(tool.OVERHEAD_OK, tool.OVERHEAD_SUSPECT)
        self.assertGreater(tool.OVERHEAD_OK, 0.05,
                           "must tolerate ordinary encryption overhead")
        self.assertLess(tool.OVERHEAD_SUSPECT, 0.5,
                        "half the traffic outside the tunnel is not a warning")

    def test_the_volume_floor_is_meaningful(self) -> None:
        self.assertGreaterEqual(tool.MIN_TUNNEL_BYTES, 1_000_000)


if __name__ == "__main__":
    unittest.main()
