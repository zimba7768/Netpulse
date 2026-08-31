"""File-transfer tracking: watched folders + browser download history.

Two independent sources that reinforce each other:

1. **Folder watching** (``watchdog``) sees every file that lands on disk in the
   folders you care about, whatever produced it — browser, torrent client,
   Steam, an installer, a file copy from a network share.  It knows the name,
   size, time and destination, but not where it came from.
2. **Browser history** reads the download tables Chrome, Edge and Firefox keep,
   which *do* carry the source URL.  Records are matched back to the watched
   files by path to fill in the "Source" column, and any download that landed
   outside a watched folder is added from here.

Only completed files are recorded: a file is committed once its size has stopped
changing, so half-finished ``.crdownload`` / ``.part`` files never show up.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    HAVE_WATCHDOG = True
except ImportError:  # pragma: no cover
    FileSystemEventHandler = object      # type: ignore
    Observer = None                      # type: ignore
    HAVE_WATCHDOG = False

TEMP_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp", ".!ut", ".opdownload")
IGNORE_DIR_PARTS = (
    "appdata", "node_modules", ".git", "__pycache__", "$recycle.bin",
    "system volume information", ".cache", "temp", "tmp",
)
CHROME_EPOCH_OFFSET = 11644473600     # seconds between 1601-01-01 and 1970-01-01


def host_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc
        return netloc.split("@")[-1] or url
    except Exception:
        return url


def _under(path: str, root: str) -> str | None:
    """The part of ``path`` below ``root``, or None if it isn't inside it."""
    try:
        norm = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
        if norm == base or not norm.startswith(base + os.sep):
            return None
        return norm[len(base) + 1:]
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# folder watching
# ---------------------------------------------------------------------------
class _Handler(FileSystemEventHandler):
    def __init__(self, tracker: "FileTracker") -> None:
        self.tracker = tracker

    def on_created(self, event):
        if not event.is_directory:
            self.tracker.mark_pending(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.tracker.mark_pending(event.dest_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.tracker.touch_pending(event.src_path)


class FileTracker:
    """Watches folders, settles partial files, and merges browser history."""

    def __init__(self, db, settings, on_new=None, link_of=None) -> None:
        self.db = db
        self.settings = settings
        self.on_new = on_new                     # callback(dict) for the UI
        #: Returns "direct" or "vpn" for activity happening right now, so a
        #: file is filed against whichever connection was carrying traffic
        #: when it arrived.
        self.link_of = link_of or (lambda: "direct")
        self._observer = None
        self._roots: list[str] = []
        self._pending: dict[str, float] = {}     # path -> last activity time
        self._sizes: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._browser_scan_ts = 0.0
        self.status = "stopped"

    # ------------------------------------------------------------------ start
    def start(self) -> bool:
        folders = [f for f in self.settings.get("watch_folders", []) if os.path.isdir(f)]
        self._roots = folders
        if HAVE_WATCHDOG and folders:
            try:
                self._observer = Observer()
                handler = _Handler(self)
                for folder in folders:
                    self._observer.schedule(handler, folder, recursive=True)
                self._observer.start()
                self.status = f"watching {len(folders)} folder(s)"
            except Exception as exc:
                self._observer = None
                self.status = f"folder watching unavailable: {exc}"
        elif not HAVE_WATCHDOG:
            self.status = "watchdog not installed"
        else:
            self.status = "no folders configured"

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="file-tracker", daemon=True)
        self._thread.start()
        return self._observer is not None

    def stop(self) -> None:
        self._stop.set()
        # Join before returning: restart() clears the flag again immediately and
        # a surviving worker would double up on the new one.
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=4)
        self._thread = None
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except Exception:
                pass
            self._observer = None
        self.status = "stopped"

    def restart(self) -> None:
        self.stop()
        self.start()

    # ---------------------------------------------------------------- pending
    def should_ignore(self, path: str) -> bool:
        """Skip noisy sub-directories *inside* a watched folder.

        Only the components below the watched root are inspected — a folder
        that happens to live under, say, C:\\Temp is still watched if the user
        asked for it; it is the ``node_modules`` and ``.cache`` sub-trees
        underneath that are noise.
        """
        for root in self._roots:
            rel = _under(path, root)
            if rel is None:
                continue
            parts = rel.replace("\\", "/").split("/")[:-1]
            return any(p in IGNORE_DIR_PARTS or p.startswith(".") for p in parts)
        return False

    def mark_pending(self, path: str) -> None:
        if self.should_ignore(path):
            return
        with self._lock:
            self._pending[path] = time.time()

    def touch_pending(self, path: str) -> None:
        with self._lock:
            if path in self._pending:
                self._pending[path] = time.time()

    def _settle(self) -> None:
        """Commit pending files whose size has stopped changing."""
        now = time.time()
        with self._lock:
            candidates = list(self._pending.items())
        for path, last in candidates:
            try:
                if not os.path.isfile(path):
                    # renamed away (e.g. foo.crdownload -> foo) or deleted
                    if now - last > 30:
                        with self._lock:
                            self._pending.pop(path, None)
                            self._sizes.pop(path, None)
                    continue
                size = os.path.getsize(path)
            except OSError:
                continue

            prev = self._sizes.get(path)
            self._sizes[path] = size
            if prev != size:
                with self._lock:
                    self._pending[path] = now       # still growing
                continue
            if now - last < 2.0:
                continue                            # give it one more beat

            with self._lock:
                self._pending.pop(path, None)
                self._sizes.pop(path, None)
            self._commit(path, size)

    def _commit(self, path: str, size: int, source: str | None = None,
                app: str | None = None, ts: float | None = None) -> None:
        name = os.path.basename(path)
        if name.lower().endswith(TEMP_SUFFIXES):
            return
        if os.path.splitext(name)[1].lower() in self.settings.get("ignore_extensions", []):
            return
        if size < int(self.settings.get("min_file_bytes", 0) or 0):
            return
        folder = os.path.dirname(path)
        if self.db.add_file(path, name, folder, size, "down", source, app, ts,
                            link=self.link_of()):
            if self.on_new:
                try:
                    self.on_new({
                        "path": path, "name": name, "folder": folder,
                        "size": size, "source": source, "app": app,
                        "ts": int(ts or time.time()),
                    })
                except Exception:
                    pass

    # ------------------------------------------------------------------- loop
    def _loop(self) -> None:
        while not self._stop.wait(2.0):
            if self.settings.get("paused"):
                continue
            try:
                self._settle()
            except Exception:
                pass
            if (self.settings.get("read_browser_history")
                    and time.time() - self._browser_scan_ts > 60):
                self._browser_scan_ts = time.time()
                try:
                    self.scan_browsers()
                except Exception:
                    pass

    # -------------------------------------------------------- browser history
    def browser_profiles(self) -> list[tuple[str, Path]]:
        """(browser name, history database) pairs that exist on this machine."""
        found: list[tuple[str, Path]] = []
        local = os.environ.get("LOCALAPPDATA", "")
        roaming = os.environ.get("APPDATA", "")
        chromium = {
            "Chrome": Path(local) / "Google" / "Chrome" / "User Data",
            "Edge": Path(local) / "Microsoft" / "Edge" / "User Data",
            "Brave": Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data",
            "Opera": Path(roaming) / "Opera Software" / "Opera Stable",
            "Vivaldi": Path(local) / "Vivaldi" / "User Data",
        }
        for name, root in chromium.items():
            if not root.is_dir():
                continue
            for profile in ["Default"] + [f"Profile {i}" for i in range(1, 6)]:
                hist = root / profile / "History"
                if hist.is_file():
                    found.append((name, hist))
            if (root / "History").is_file():          # Opera keeps it at the root
                found.append((name, root / "History"))

        firefox = Path(roaming) / "Mozilla" / "Firefox" / "Profiles"
        if firefox.is_dir():
            for profile in firefox.iterdir():
                places = profile / "places.sqlite"
                if places.is_file():
                    found.append(("Firefox", places))
        return found

    @staticmethod
    def _copy_locked(src: Path) -> Path | None:
        """Browsers hold their history open — work on a snapshot."""
        try:
            tmp = Path(tempfile.gettempdir()) / f"netpulse_{src.parent.name}_{src.name}"
            shutil.copy2(src, tmp)
            for extra in ("-wal", "-shm"):
                side = Path(str(src) + extra)
                if side.exists():
                    shutil.copy2(side, Path(str(tmp) + extra))
            return tmp
        except Exception:
            return None

    def scan_browsers(self, lookback_days: int = 30) -> int:
        cutoff = time.time() - lookback_days * 86400
        added = 0
        for browser, path in self.browser_profiles():
            snapshot = self._copy_locked(path)
            if snapshot is None:
                continue
            try:
                if path.name == "places.sqlite":
                    records = self._read_firefox(snapshot, cutoff)
                else:
                    records = self._read_chromium(snapshot, cutoff)
                for rec in records:
                    added += self._merge_record(rec, browser)
            except Exception:
                pass
            finally:
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.unlink(str(snapshot) + suffix)
                    except OSError:
                        pass
        return added

    @staticmethod
    def _read_chromium(dbfile: Path, cutoff: float) -> list[dict]:
        out: list[dict] = []
        conn = sqlite3.connect(f"file:{dbfile}?immutable=1", uri=True)
        try:
            rows = conn.execute(
                "SELECT target_path, received_bytes, total_bytes, start_time, "
                "       tab_url, site_url, mime_type "
                "FROM downloads ORDER BY start_time DESC LIMIT 2000"
            ).fetchall()
        except sqlite3.Error:
            conn.close()
            return out
        for target, received, total, start, tab_url, site_url, _mime in rows:
            ts = (start or 0) / 1_000_000 - CHROME_EPOCH_OFFSET
            if ts < cutoff or not target:
                continue
            out.append({
                "path": os.path.normpath(target),
                "size": int(total or received or 0),
                "ts": ts,
                "url": tab_url or site_url or "",
            })
        conn.close()
        return out

    @staticmethod
    def _read_firefox(dbfile: Path, cutoff: float) -> list[dict]:
        out: list[dict] = []
        conn = sqlite3.connect(f"file:{dbfile}?immutable=1", uri=True)
        try:
            rows = conn.execute(
                "SELECT a.content, p.url, a.dateAdded "
                "FROM moz_annos a "
                "JOIN moz_anno_attributes t ON a.anno_attribute_id = t.id "
                "JOIN moz_places p ON a.place_id = p.id "
                "WHERE t.name = 'downloads/destinationFileURI' "
                "ORDER BY a.dateAdded DESC LIMIT 2000"
            ).fetchall()
        except sqlite3.Error:
            conn.close()
            return out
        for dest, url, added in rows:
            ts = (added or 0) / 1_000_000
            if ts < cutoff or not dest:
                continue
            local = unquote(urlparse(dest).path)
            if local.startswith("/") and len(local) > 2 and local[2] == ":":
                local = local[1:]                     # /C:/Users/... -> C:/Users/...
            local = os.path.normpath(local)
            size = 0
            try:
                size = os.path.getsize(local)
            except OSError:
                pass
            out.append({"path": local, "size": size, "ts": ts, "url": url or ""})
        conn.close()
        return out

    def _merge_record(self, rec: dict, browser: str) -> int:
        path, url = rec["path"], rec["url"]
        source = host_of(url) if url else None
        self.db.enrich_file_source(path, source or "", browser)
        size = rec["size"]
        if not size:
            try:
                size = os.path.getsize(path)
            except OSError:
                return 0
        if not os.path.isfile(path):
            return 0
        if size < int(self.settings.get("min_file_bytes", 0) or 0):
            return 0
        name = os.path.basename(path)
        if self.db.add_file(path, name, os.path.dirname(path), size,
                            "down", source, browser, rec["ts"],
                            link=self.link_of()):
            if self.on_new:
                try:
                    self.on_new({"path": path, "name": name,
                                 "folder": os.path.dirname(path), "size": size,
                                 "source": source, "app": browser, "ts": int(rec["ts"])})
                except Exception:
                    pass
            return 1
        return 0
