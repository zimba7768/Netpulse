"""Comparing the two interpreters NetPulse can run under.

The app is launched with pythonw.exe so it has no console window; every
diagnostic so far has been run with python.exe. Per-application VPN rules,
split-tunnel lists and firewall rules all match on the executable, so those are
two different network identities — which is invisible unless both are tried.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_tool():
    path = os.path.join(ROOT, "tools", "diagnose-wanip.py")
    spec = importlib.util.spec_from_file_location("diagnose_wanip", path)
    module = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["diagnose-wanip.py"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


tool = load_tool()

ISP = "47.195.99.250"          # the address the app reported
TUNNEL = "82.26.195.54"        # the address the script reported


class CompareVerdictTests(unittest.TestCase):
    def verdict(self, mine: str, theirs: str) -> str:
        return tool.compare_verdict(mine, theirs, "pythonw.exe")

    def test_two_addresses_means_two_routes(self) -> None:
        # The reported situation, exactly.
        out = self.verdict(ISP, TUNNEL)
        self.assertIn("DIFFERENT public addresses", out)
        self.assertIn("pythonw.exe", out)
        self.assertIn("Bypasser", out)

    def test_matching_addresses_clear_the_interpreter(self) -> None:
        out = self.verdict(TUNNEL, TUNNEL)
        self.assertIn("same address", out)
        self.assertIn("somewhere else", out)
        self.assertNotIn("Bypasser", out)

    def test_a_blocked_twin_is_reported_as_blocking(self) -> None:
        out = self.verdict(TUNNEL, "")
        self.assertIn("pythonw.exe", out)
        self.assertIn("blocking", out)

    def test_nothing_at_all_is_not_a_diagnosis(self) -> None:
        out = self.verdict("", "")
        self.assertIn("nothing to compare", out)
        self.assertNotIn("DIFFERENT", out)

    def test_the_reverse_case_is_not_silently_misread(self) -> None:
        # Only the windowed one working is not the reported fault; saying
        # nothing here would let a confusing result pass as an answer.
        out = self.verdict("", TUNNEL)
        self.assertIn("reverse", out)


class WindowedTwinTests(unittest.TestCase):
    def test_an_override_is_honoured(self) -> None:
        os.environ["NETPULSE_ALT_PYTHON"] = "/some/other/python"
        self.addCleanup(os.environ.pop, "NETPULSE_ALT_PYTHON", None)
        # Compare paths as paths: Path normalises separators per platform, so
        # comparing against a string with forward slashes can only ever pass
        # on Linux — which is how this got through in the first place.
        self.assertEqual(tool.windowed_twin(), Path("/some/other/python"))

    def test_a_missing_twin_is_none_rather_than_a_guess(self) -> None:
        os.environ.pop("NETPULSE_ALT_PYTHON", None)
        # pythonw.exe does not exist beside a Linux interpreter.
        if not sys.platform.startswith("win"):
            self.assertIsNone(tool.windowed_twin())


if __name__ == "__main__":
    unittest.main()
