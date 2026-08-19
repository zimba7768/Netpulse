"""Autostart tests.

The Windows side of this module cannot be exercised here, but the scheduled-task
definition it hands to schtasks can be: if the XML is malformed or missing a
field, the task silently fails to run at sign-in, which is exactly the failure
that is hardest to notice.
"""
from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netpulse import autostart

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class TaskDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        # schtasks is handed UTF-16 bytes; parse exactly what it would receive.
        self.xml = autostart._task_xml().encode("utf-16")
        self.root = ET.fromstring(self.xml)

    def test_xml_is_well_formed_and_namespaced(self):
        self.assertTrue(self.root.tag.endswith("Task"))
        self.assertEqual(self.root.get("version"), "1.2")

    def test_starts_at_logon(self):
        trigger = self.root.find(".//t:LogonTrigger", NS)
        self.assertIsNotNone(trigger, "a logon trigger is required")
        self.assertEqual(trigger.find("t:Enabled", NS).text, "true")

    def test_requests_highest_privileges(self):
        level = self.root.find(".//t:Principal/t:RunLevel", NS)
        self.assertIsNotNone(level)
        self.assertEqual(level.text, "HighestAvailable",
                         "without this the task starts unelevated and "
                         "per-application tracking stays off")

    def test_runs_interactively(self):
        logon = self.root.find(".//t:Principal/t:LogonType", NS)
        self.assertEqual(logon.text, "InteractiveToken",
                         "a non-interactive task cannot show a window or tray icon")

    def test_action_points_at_the_application(self):
        exec_node = self.root.find(".//t:Actions/t:Exec", NS)
        self.assertIsNotNone(exec_node)
        command = exec_node.find("t:Command", NS).text or ""
        arguments = exec_node.find("t:Arguments", NS).text or ""
        workdir = exec_node.find("t:WorkingDirectory", NS).text or ""
        self.assertTrue(command, "command must not be empty")
        self.assertIn("--tray", arguments, "it should start hidden in the tray")
        if not getattr(sys, "frozen", False):
            self.assertIn("main.py", arguments)
        self.assertTrue(workdir)

    def test_survives_laptop_conditions(self):
        settings = self.root.find("t:Settings", NS)
        self.assertEqual(settings.find("t:DisallowStartIfOnBatteries", NS).text,
                         "false", "must still start on battery")
        self.assertEqual(settings.find("t:StopIfGoingOnBatteries", NS).text, "false")
        self.assertEqual(settings.find("t:ExecutionTimeLimit", NS).text, "PT0S",
                         "a time limit would kill long-running monitoring")
        self.assertEqual(settings.find("t:MultipleInstancesPolicy", NS).text,
                         "IgnoreNew", "never start a second copy")

    def test_paths_with_special_characters_are_escaped(self):
        original = autostart.app_root
        try:
            autostart.app_root = lambda: __import__("pathlib").Path(
                "C:/Tools/Net & Pulse <beta>")
            xml = autostart._task_xml()
            self.assertIn("&amp;", xml)
            self.assertNotIn("<beta>", xml)
            ET.fromstring(xml.encode("utf-16"))     # still parses
        finally:
            autostart.app_root = original


class FrozenBuildTests(unittest.TestCase):
    """A PyInstaller build must resolve paths to the .exe, not to _MEIPASS."""

    def tearDown(self) -> None:
        if hasattr(sys, "frozen"):
            del sys.frozen

    def test_app_root_follows_the_executable_when_frozen(self):
        loose = autostart.app_root()
        sys.frozen = True                       # what PyInstaller sets
        frozen = autostart.app_root()
        self.assertEqual(frozen, Path(sys.executable).resolve().parent)
        self.assertNotEqual(frozen, loose,
                            "frozen builds must not use the source tree path")

    def test_launch_parts_drops_the_script_argument_when_frozen(self):
        sys.frozen = True
        command, arguments, workdir = autostart.launch_parts()
        self.assertEqual(command, sys.executable,
                         "the exe launches itself, not an interpreter")
        self.assertNotIn("main.py", arguments)
        self.assertIn("--tray", arguments)
        self.assertEqual(workdir, str(Path(sys.executable).resolve().parent))


class LaunchCommandTests(unittest.TestCase):
    def test_command_is_quoted(self):
        command = autostart.launch_command()
        self.assertTrue(command.startswith('"'), command)
        self.assertIn("--tray", command)

    def test_mode_helpers_are_consistent(self):
        mode = autostart.current_mode()
        self.assertIn(mode, (autostart.MODE_NONE, autostart.MODE_TASK,
                             autostart.MODE_RUN))
        self.assertEqual(autostart.is_enabled(), mode != autostart.MODE_NONE)
        self.assertTrue(autostart.describe())

    @unittest.skipIf(sys.platform.startswith("win"), "would modify the real system")
    def test_refuses_politely_off_windows(self):
        ok, message = autostart.set_enabled(True)
        self.assertFalse(ok)
        self.assertIn("Windows", message)


class StaleTaskTests(unittest.TestCase):
    """A registered task stores an absolute path, so moving the app strands it.

    This bit the author of the reported issue: the repository was re-cloned to
    a new folder, leaving the task pointing at the old copy, which is the one
    Windows would faithfully keep starting.
    """

    def setUp(self) -> None:
        self.real_action = autostart.task_action

    def tearDown(self) -> None:
        autostart.task_action = self.real_action

    def test_matches_when_the_task_points_at_this_copy(self):
        command, arguments, _ = autostart.launch_parts()
        autostart.task_action = lambda: (command, arguments)
        self.assertTrue(autostart.task_matches_this_copy())

    def test_case_differences_are_not_treated_as_a_mismatch(self):
        command, arguments, _ = autostart.launch_parts()
        autostart.task_action = lambda: (command.upper(), arguments.upper())
        self.assertTrue(autostart.task_matches_this_copy(),
                        "Windows paths are case-insensitive")

    def test_detects_a_task_left_behind_by_a_moved_installation(self):
        autostart.task_action = lambda: (
            r"C:\Users\zimba\Downloads\netpulse-github\netpulse\pythonw.exe",
            r'"C:\Users\zimba\Downloads\netpulse-github\netpulse\main.py" --tray')
        self.assertFalse(autostart.task_matches_this_copy())

    def test_no_task_is_not_a_mismatch(self):
        autostart.task_action = lambda: ("", "")
        self.assertTrue(autostart.task_matches_this_copy())


class ElevationTests(unittest.TestCase):
    def test_the_elevated_command_survives_a_path_with_spaces(self):
        """Every quoting layer between Python, cmd and PowerShell is bypassed."""
        import base64
        from pathlib import Path as P

        captured = {}

        real_run = autostart._run
        autostart._run = lambda args: (captured.update(args=args), (0, ""))[1]
        try:
            autostart._elevated_schtasks(P(r"C:\Program Files\net pulse\task.xml"))
        finally:
            autostart._run = real_run

        args = captured["args"]
        self.assertEqual(args[:3], ["powershell", "-NoProfile", "-EncodedCommand"])
        script = base64.b64decode(args[3]).decode("utf-16-le")
        self.assertIn(r"C:\Program Files\net pulse\task.xml", script)
        self.assertIn("-Verb RunAs", script, "must raise the UAC prompt")
        self.assertIn("-Wait", script, "must wait, or the result is unknowable")
        self.assertIn("exit $p.ExitCode", script,
                      "a declined prompt has to be distinguishable from success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
