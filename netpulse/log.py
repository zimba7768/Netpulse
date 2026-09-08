"""A small rolling log, for faults that only appear after hours of running.

Some failures cannot be reproduced from a fresh process — they need the program
to have been alive across whatever changed underneath it. Reproducing those from
the outside is guesswork; the only reliable witness is the program itself, at
the moment it happens. This exists so that witness leaves a statement.

Deliberately tiny: no configuration, no levels, no third-party dependency, a
hard size cap so it can never grow without bound on a machine nobody is
watching.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

#: Once past this, the oldest half is dropped. Small enough to paste into an
#: email, large enough to hold a day of an occasional event.
MAX_BYTES = 256_000


class RollingLog:
    """Append-only text log with a size ceiling. Safe to share across threads."""

    def __init__(self, path: Path | str | None, max_bytes: int = MAX_BYTES) -> None:
        self.path = Path(path) if path else None
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def write(self, message: str) -> None:
        """Record one line. Never raises: logging must not break the caller."""
        if self.path is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp}  {message}\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line)
                self._trim()
        except OSError:
            pass

    def _trim(self) -> None:
        """Drop the oldest half once the ceiling is passed."""
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
            text = self.path.read_text(encoding="utf-8", errors="replace")
            keep = text[len(text) // 2:]
            # Start at a line boundary, so the file never opens mid-sentence.
            newline = keep.find("\n")
            if newline != -1:
                keep = keep[newline + 1:]
            self.path.write_text(
                "… earlier entries trimmed …\n" + keep, encoding="utf-8")
        except OSError:
            pass

    def read(self) -> str:
        if self.path is None:
            return ""
        try:
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


#: A log that discards everything, so callers need no None checks.
NULL_LOG = RollingLog(None)
