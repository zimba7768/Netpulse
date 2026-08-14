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

import ctypes
import os
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


def _create_task() -> tuple[bool, str]:
    path = Path(tempfile.gettempdir()) / "netpulse-task.xml"
    try:
        # schtasks wants UTF-16 for /XML input.
        path.write_text(_task_xml(), encoding="utf-16")
    except OSError as exc:
        return False, f"Could not write the task definition: {exc}"
    code, output = _run(["schtasks", "/Create", "/TN", TASK_NAME,
                         "/XML", str(path), "/F"])
    try:
        path.unlink()
    except OSError:
        pass
    return code == 0, output.strip()


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
        return ("On, as a scheduled task — starts elevated at sign-in, so "
                "per-application tracking works from the start. Note this is "
                "listed in Task Scheduler, not in Task Manager's Startup tab.")
    if mode == MODE_RUN:
        where = ("Listed in Task Manager › Startup apps, where it may appear "
                 "as ‘pythonw.exe’ rather than NetPulse.")
        if is_admin():
            return ("On, as a startup entry — it will start without administrator "
                    "rights, so per-application tracking will be off. Untick and "
                    "re-tick this box now to upgrade it to a scheduled task. "
                    + where)
        return ("On, as a startup entry — it will start without administrator "
                "rights, so per-application tracking will be off. To fix that, "
                "start NetPulse with run-as-admin.bat and re-tick this box. "
                + where)
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

    # Prefer the scheduled task; it is the only way to start elevated silently.
    if is_admin():
        ok, output = _create_task()
        if ok:
            _set_run_key(False)          # never leave both in place
            return True, ("Done. NetPulse will start automatically at sign-in, "
                          "with per-application tracking enabled.")
        fallback_note = (f"\n\n(The scheduled task could not be created: "
                         f"{output or 'unknown error'} — a normal startup entry "
                         f"was used instead.)")
    else:
        fallback_note = ("\n\nIt will start without administrator rights, so the "
                         "per-application breakdown will be off until you open "
                         "NetPulse with run-as-admin.bat. To fix that "
                         "permanently: start NetPulse as administrator, then "
                         "untick and re-tick this box — it will switch to a "
                         "scheduled task that starts elevated on its own.")

    ok, err = _set_run_key(True)
    if not ok:
        return False, f"Could not create the startup entry: {err}"
    return True, "NetPulse will start automatically at sign-in." + fallback_note


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
