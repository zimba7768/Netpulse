"""Byte / rate / time formatting shared by the whole application."""
from __future__ import annotations

from datetime import datetime, timedelta

_KB = 1024.0
_MB = _KB * 1024
_GB = _MB * 1024
_TB = _GB * 1024

_FIXED = {"KB": _KB, "MB": _MB, "GB": _GB}


def format_bytes(num: float, unit: str = "auto", decimals: int | None = None) -> str:
    """Human readable size. `unit` may be auto, KB, MB or GB."""
    num = float(num or 0)
    if unit in _FIXED:
        div = _FIXED[unit]
        dec = 2 if decimals is None else decimals
        return f"{num / div:,.{dec}f} {unit}"
    if num < _KB:
        return f"{num:,.0f} B"
    if num < _MB:
        return f"{num / _KB:,.1f} KB"
    if num < _GB:
        return f"{num / _MB:,.1f} MB"
    if num < _TB:
        return f"{num / _GB:,.2f} GB"
    return f"{num / _TB:,.2f} TB"


def split_bytes(num: float, unit: str = "auto") -> tuple[str, str]:
    """Return (value, unit) separately, for stat tiles that style them apart."""
    text = format_bytes(num, unit)
    value, _, suffix = text.rpartition(" ")
    return value, suffix


def format_rate(bytes_per_sec: float, unit: str = "auto") -> str:
    return format_bytes(bytes_per_sec, unit, decimals=1) + "/s"


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def format_when(ts: float) -> str:
    """Relative-ish timestamp used in the file log."""
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    if dt.date() == now.date():
        return "Today " + dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == (now - timedelta(days=1)).date():
        return "Yesterday " + dt.strftime("%I:%M %p").lstrip("0")
    if (now - dt).days < 365:
        return dt.strftime("%b %d, %I:%M %p").replace(" 0", " ")
    return dt.strftime("%b %d %Y")


def truncate(text: str, limit: int = 48) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
