"""Application paths, defaults and persisted user settings."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "NetPulse"
APP_VERSION = "1.0.5"

IS_WINDOWS = sys.platform.startswith("win")


def data_dir() -> Path:
    """Per-user writable directory for the database and settings."""
    override = os.environ.get("NETPULSE_DATA_DIR")
    if override:
        p = Path(override)
    elif IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        p = Path(base) / APP_NAME
    else:  # dev / test on Linux or macOS
        p = Path.home() / ".local" / "share" / APP_NAME.lower()
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "netpulse.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def default_watch_folders() -> list[str]:
    home = Path.home()
    candidates = ["Downloads", "Desktop", "Documents", "Pictures", "Videos", "Music"]
    return [str(home / c) for c in candidates if (home / c).is_dir()]


DEFAULTS: dict[str, Any] = {
    # --- collection -------------------------------------------------------
    "sample_interval_ms": 1000,       # live poll rate for the speed graph
    "track_per_app": True,            # ETW per-process attribution (needs admin)
    "track_files": True,
    "watch_folders": None,            # None -> default_watch_folders() at load
    "read_browser_history": True,
    "show_wan_ip": True,              # needs one small outbound request
    "min_file_bytes": 16 * 1024,      # ignore trivial files (temp, .crdownload stubs)
    "ignore_extensions": [".tmp", ".crdownload", ".part", ".partial", ".download"],
    # --- retention (days); 0 means "keep forever" -------------------------
    "retain_minute_days": 7,
    "retain_hour_days": 90,
    "retain_day_days": 0,
    # --- interface --------------------------------------------------------
    "units": "auto",                  # auto | KB | MB | GB
    "start_minimized": False,
    "close_to_tray": True,
    "autostart": False,
    "paused": False,
}


class Settings:
    """Thread-safe JSON-backed settings with dict-style access."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or settings_path()
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data.update(raw)
            except (OSError, ValueError):
                pass
            if not self._data.get("watch_folders"):
                self._data["watch_folders"] = default_watch_folders()

    def save(self) -> None:
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
                tmp.replace(self._path)
            except OSError:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
        self.save()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
        self.save()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    __getitem__ = get
    __setitem__ = set
