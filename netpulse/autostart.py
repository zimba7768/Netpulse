"""Start-with-Windows support.

There are two ways to start an app at sign-in, and the difference matters here:

* A **scheduled task** with "run with highest privileges" starts NetPulse
  elevated and silently, so per-application tracking is available from the
  moment you sign in. Creating the task needs administrator rights once.
* The **Run registry key** needs no rights at all, but Windows always launches
  Run entries unelevated — and it cannot show a UAC prompt at sign-in — so
  NetPulse would start with machine-wide totals only.

So: try the scheduled task, fall back to the Run key, and tell the user which
one they ended up with.
"""
from __future__ import annotations

import base64
import ctypes
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

APP_KEY = "NetPulse"
TASK_NAME = "NetPulse"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

IS_WINDOWS = sys.platform.startswith("win")
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0      # CREATE_NO_WINDOW

# What is currently providing autostart.
MODE_NONE = "none"
MODE_TASK = "task"
MODE_RUN = "run"


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def app_root() -> Path:
    """The folder the application lives in.

    In a PyInstaller build ``__file__`` points inside the temporary extraction
    directory, which is deleted when the process exits — useless as a working
    directory for a startup entry. The executable's own folder is the stable
    answer there.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def interpreter() -> Path:
    """pythonw.exe when available, so no console window flashes at sign-in."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "python.exe":
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            return quiet
    return exe


def launch_parts() -> tuple[str, str, str]:
    """(command, arguments, working directory) for whichever mechanism is used."""
    root = app_root()
    if getattr(sys, "frozen", False):              # PyInstaller build
        return str(sys.executable), "--tray", str(root)
    return str(interpreter()), f'"{root / "main.py"}" --tray', str(root)


def launch_command() -> str:
    command, arguments, _ = launch_parts()
    return f'"{command}" {arguments}'.strip()


def _run(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              creationflags=_NO_WINDOW, timeout=30)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 1, str(exc)


# ---------------------------------------------------------------- scheduled task
def _account_name() -> str:
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USERNAME") or ""
    return f"{domain}\\{user}" if domain else user


def _task_xml() -> str:
    command, arguments, workdir = launch_parts()
    account = _account_name()

    def esc(text: str) -> str:
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>NetPulse</Author>
    <Description>Starts NetPulse network usage monitoring at sign-in.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{esc(account)}</UserId>
      <Delay>PT20S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{esc(account)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{esc(command)}</Command>
      <Arguments>{esc(arguments)}</Arguments>
      <WorkingDirectory>{esc(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def task_exists() -> bool:
    if not IS_WINDOWS:
        return False
    code, _ = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    return code == 0


def _elevated_schtasks(xml_path: Path) -> tuple[int, str]:
    """Register the task through a single UAC prompt.

    Creating a task that runs with highest privileges is itself a privileged
    operation, so without this the only route was to restart the whole
    application as administrator first — asking the user to think about
    elevation twice for something they had already asked for once.

    The script is passed base64-encoded rather than as a quoted string: it
    contains both kinds of quote and a Windows path, and -EncodedCommand
    removes every layer of quoting between Python, the Windows command line
    and PowerShell that could otherwise mangle it.
    """
    script = (
        "$xml = '" + str(xml_path).replace("'", "''") + "'; "
        "$argument = '/Create /TN " + TASK_NAME + " /XML \"' + $xml + '\" /F'; "
        "$p = Start-Process schtasks -ArgumentList $argument "
        "-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return _run(["powershell", "-NoProfile", "-EncodedCommand", encoded])


def _create_task() -> tuple[bool, str]:
    path = Path(tempfile.gettempdir()) / "netpulse-task.xml"
    try:
        # schtasks wants UTF-16 for /XML input.
        path.write_text(_task_xml(), encoding="utf-16")
    except OSError as exc:
        return False, f"Could not write the task definition: {exc}"

    if is_admin():
        code, output = _run(["schtasks", "/Create", "/TN", TASK_NAME,
                             "/XML", str(path), "/F"])
    else:
        code, output = _elevated_schtasks(path)
        if code != 0 and not output.strip():
            output = ("The administrator prompt was declined or dismissed.")

    try:
        path.unlink()
    except OSError:
        pass
    return code == 0, output.strip()


def task_action() -> tuple[str, str]:
    """What the registered task actually launches: (command, arguments).

    Worth checking, because the task stores an absolute path. Move or re-clone
    the application and the task keeps faithfully starting the old copy.
    """
    if not IS_WINDOWS:
        return "", ""
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/XML", "ONE"],
            capture_output=True, creationflags=_NO_WINDOW, timeout=30)
    except Exception:
        return "", ""
    if proc.returncode != 0:
        return "", ""
    raw = proc.stdout or b""
    for encoding in ("utf-16", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return "", ""
    command = re.search(r"<Command>(.*?)</Command>", text, re.S)
    arguments = re.search(r"<Arguments>(.*?)</Arguments>", text, re.S)
    return (command.group(1).strip() if command else "",
            arguments.group(1).strip() if arguments else "")


def task_matches_this_copy() -> bool:
    """False when the task points somewhere other than the running copy."""
    command, arguments = task_action()
    if not command:
        return True                    # nothing registered, nothing stale
    want_command, want_arguments, _ = launch_parts()

    def normalise(value: str) -> str:
        # Windows paths are case-insensitive, but os.path.normcase only folds
        # case *on* Windows — spelling it out keeps the comparison honest
        # wherever the tests happen to run.
        return os.path.normcase(value).replace("/", "\\").strip().lower()

    return (normalise(command) == normalise(want_command)
            and normalise(arguments) == normalise(want_arguments))


def _delete_task() -> None:
    if task_exists():
        _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])


# -------------------------------------------------------------------- run key
def run_key_exists() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_KEY)
            return bool(value)
    except OSError:
        return False


def _set_run_key(enabled: bool) -> tuple[bool, str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_KEY, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_KEY)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as exc:
        return False, str(exc)


# --------------------------------------------------------------------- public
def current_mode() -> str:
    if task_exists():
        return MODE_TASK
    if run_key_exists():
        return MODE_RUN
    return MODE_NONE


def is_enabled() -> bool:
    return current_mode() != MODE_NONE


def describe() -> str:
    """One line for the Settings page describing what will happen at sign-in."""
    mode = current_mode()
    if mode == MODE_TASK:
        if not task_matches_this_copy():
            command, _ = task_action()
            return ("On, but pointing at a different copy of NetPulse "
                    f"({command or 'unknown location'}). That is the one Windows "
                    "will start. Untick and re-tick this box to point it at "
                    "this copy instead.")
        return ("On, as a scheduled task — starts elevated at sign-in, so "
                "per-application tracking works from the start. Listed in Task "
                "Scheduler, not in Task Manager's Startup tab.")
    if mode == MODE_RUN:
        return ("On, as a startup entry — Windows always launches these without "
                "administrator rights, so per-application tracking will be off. "
                "Untick and re-tick this box to switch to a scheduled task, "
                "which can start elevated. Listed in Task Manager › Startup "
                "apps, where it may appear as ‘pythonw.exe’.")
    return "Off — NetPulse will not start automatically."


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Turn autostart on or off. Returns (success, message for the user)."""
    if not IS_WINDOWS:
        return False, "Start with Windows is only available on Windows."

    if not enabled:
        _delete_task()
        ok, err = _set_run_key(False)
        if not ok:
            return False, f"Could not remove the startup entry: {err}"
        return True, "NetPulse will no longer start automatically."

    # Always aim for the scheduled task: it is the only mechanism that can
    # start elevated at sign-in, and it can now be registered from an ordinary
    # session by way of one administrator prompt.
    ok, output = _create_task()
    if ok:
        _set_run_key(False)              # never leave both in place
        return True, ("Done. NetPulse will start automatically when you sign "
                      "in, with administrator rights, so the per-application "
                      "breakdown works from the start.")

    ok, err = _set_run_key(True)
    if not ok:
        return False, f"Could not set NetPulse to start automatically: {err}"
    return True, (
        "NetPulse will start automatically when you sign in — but without "
        "administrator rights, so the per-application breakdown will be off "
        "until you open it with run-as-admin.bat.\n\n"
        "The scheduled task that would have started it elevated could not be "
        f"created: {output or 'unknown error'}\n\n"
        "Untick and re-tick this box to try again.")


def elevated_relaunch() -> bool:
    """Restart the application with administrator rights."""
    if not IS_WINDOWS:
        return False
    try:
        root = app_root()
        params = "" if getattr(sys, "frozen", False) else f'"{root / "main.py"}"'
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(interpreter()), params, str(root), 1
        )
        return int(rc) > 32
    except Exception:
        return False
