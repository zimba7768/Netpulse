"""SQLite storage: minute/hour/day traffic buckets, file log, rollups, retention.

Design notes
------------
* One table holds every granularity, keyed by ``bucket`` ('m', 'h', 'd').  A row
  with ``app = ''`` is the machine-wide total; any other value is a per-process
  total.  Keeping them in one table makes rollup and querying uniform.
* Bucket timestamps are aligned to *local* time boundaries so that "per hour" and
  "per day" mean what the user sees on their clock, including across DST.
* Rollups are recomputed (not accumulated) from the finer bucket, so they are
  idempotent — a crash mid-write can never double-count.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

SYSTEM = ""  # the app name used for machine-wide totals

MINUTE, HOUR, DAY = "m", "h", "d"

#: 'direct' is traffic that went straight out; 'vpn' went through a tunnel
#: adapter. They are stored side by side rather than summed, because the
#: physical adapter also carries the tunnel's bytes in encrypted form.
DIRECT, VPN = "direct", "vpn"
LINKS = (DIRECT, VPN)

TRAFFIC_DDL = """
CREATE TABLE IF NOT EXISTS traffic (
    bucket TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    app    TEXT    NOT NULL,
    link   TEXT    NOT NULL DEFAULT 'direct',
    down   INTEGER NOT NULL DEFAULT 0,
    up     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, ts, app, link)
) WITHOUT ROWID;
"""

SCHEMA = TRAFFIC_DDL + """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE INDEX IF NOT EXISTS idx_traffic_scan ON traffic(bucket, ts);

CREATE TABLE IF NOT EXISTS files (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    direction TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    path      TEXT    NOT NULL,
    folder    TEXT    NOT NULL,
    size      INTEGER NOT NULL DEFAULT 0,
    source    TEXT,
    app       TEXT,
    ext       TEXT,
    link      TEXT    NOT NULL DEFAULT 'direct'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_files_unique ON files(path, size);
CREATE INDEX IF NOT EXISTS idx_files_ts ON files(ts DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


# --------------------------------------------------------------------------
# local-time bucket helpers
# --------------------------------------------------------------------------
def floor_minute(ts: float) -> int:
    return int(ts) - int(ts) % 60


def floor_hour(ts: float) -> int:
    dt = datetime.fromtimestamp(ts).replace(minute=0, second=0, microsecond=0)
    return int(dt.timestamp())


def floor_day(ts: float) -> int:
    dt = datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(dt.timestamp())


def day_start(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp())


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            self._migrate()
        if self.get_meta("installed_at") is None:
            self.set_meta("installed_at", str(int(time.time())))

    def _migrate(self) -> None:
        """Bring a database made before the direct/VPN split up to date.

        Everything recorded then predates the distinction, so it becomes
        'direct' — which is what it was, for anyone who never used a VPN, and
        the honest default for anyone who did.
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(traffic)")}
        if columns and "link" not in columns:
            self._conn.executescript(
                "ALTER TABLE traffic RENAME TO traffic_pre_link;"
                + TRAFFIC_DDL +
                "INSERT OR IGNORE INTO traffic(bucket,ts,app,link,down,up) "
                "  SELECT bucket, ts, app, 'direct', down, up FROM traffic_pre_link;"
                "DROP TABLE traffic_pre_link;"
                "CREATE INDEX IF NOT EXISTS idx_traffic_scan ON traffic(bucket, ts);"
            )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(files)")}
        if columns and "link" not in columns:
            self._conn.execute(
                "ALTER TABLE files ADD COLUMN link TEXT NOT NULL DEFAULT 'direct'")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    # ---------------------------------------------------------------- meta
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    # ------------------------------------------------------------- writing
    def add_traffic(
        self,
        down: int,
        up: int,
        per_app: dict[str, tuple[int, int]] | None = None,
        ts: float | None = None,
        link: str = DIRECT,
    ) -> None:
        """Accumulate one sample into the minute bucket."""
        if down <= 0 and up <= 0 and not per_app:
            return
        minute = floor_minute(ts if ts is not None else time.time())
        rows: list[tuple] = [(MINUTE, minute, SYSTEM, link, int(down), int(up))]
        for app, (adown, aup) in (per_app or {}).items():
            if adown or aup:
                rows.append((MINUTE, minute, app, link, int(adown), int(aup)))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO traffic(bucket,ts,app,link,down,up) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(bucket,ts,app,link) DO UPDATE SET "
                "down = down + excluded.down, up = up + excluded.up",
                rows,
            )
            self._conn.commit()

    def add_samples(
        self,
        totals: dict[str, tuple[int, int]],
        per_app: dict[str, dict[str, tuple[int, int]]] | None = None,
        ts: float | None = None,
    ) -> None:
        """Record one sampling tick across every link at once.

        ``totals`` is {link: (down, up)}; ``per_app`` is {link: {app: (down, up)}}.
        """
        per_app = per_app or {}
        for link, (down, up) in totals.items():
            self.add_traffic(down, up, per_app.get(link), ts=ts, link=link)

    def add_file(
        self,
        path: str,
        name: str,
        folder: str,
        size: int,
        direction: str = "down",
        source: str | None = None,
        app: str | None = None,
        ts: float | None = None,
        link: str = DIRECT,
    ) -> bool:
        """Record a transferred file. Returns False if it was already logged."""
        ext = Path(name).suffix.lower().lstrip(".")
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO files"
                "(ts,direction,name,path,folder,size,source,app,ext,link) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    int(ts if ts is not None else time.time()),
                    direction, name, path, folder, int(size), source, app, ext, link,
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def enrich_file_source(self, path: str, source: str, app: str | None = None) -> None:
        """Attach a download URL discovered later from browser history."""
        with self._lock:
            self._conn.execute(
                "UPDATE files SET source=COALESCE(source,?), app=COALESCE(app,?) "
                "WHERE path=? AND source IS NULL",
                (source, app, path),
            )
            self._conn.commit()

    # ------------------------------------------------------------- rollups
    def rollup(self, since: float | None = None) -> None:
        """Rebuild hour buckets from minutes and day buckets from hours.

        Only buckets newer than ``since`` are recomputed, which keeps the
        periodic call cheap.  Pass ``since=0`` for a full rebuild.

        The default covers the last three hours *or* everything since the last
        successful rollup, whichever reaches further back.  A fixed window alone
        is not safe: if the machine sleeps, or the process is stopped for a
        while, samples older than the window would never be folded into the
        hour and day totals and would silently vanish from every view built on
        them.
        """
        if since is None:
            recent = time.time() - 3 * 3600
            last = float(self.get_meta("last_rollup") or 0)
            since = min(recent, last - 120) if last else 0
        hour_from = floor_hour(since) if since else 0
        day_from = floor_day(since) if since else 0

        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, app, link, SUM(down) d, SUM(up) u FROM traffic "
                "WHERE bucket=? AND ts>=? GROUP BY ts, app, link",
                (MINUTE, hour_from),
            )
            hours: dict[tuple[int, str, str], list[int]] = defaultdict(lambda: [0, 0])
            for row in cur:
                key = (floor_hour(row["ts"]), row["app"], row["link"])
                hours[key][0] += row["d"] or 0
                hours[key][1] += row["u"] or 0
            if hours:
                self._conn.executemany(
                    "INSERT INTO traffic(bucket,ts,app,link,down,up) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(bucket,ts,app,link) DO UPDATE SET "
                    "down=excluded.down, up=excluded.up",
                    [(HOUR, ts, app, link, d, u)
                     for (ts, app, link), (d, u) in hours.items()],
                )

            cur = self._conn.execute(
                "SELECT ts, app, link, down, up FROM traffic "
                "WHERE bucket=? AND ts>=?",
                (HOUR, day_from),
            )
            days: dict[tuple[int, str, str], list[int]] = defaultdict(lambda: [0, 0])
            for row in cur:
                key = (floor_day(row["ts"]), row["app"], row["link"])
                days[key][0] += row["down"] or 0
                days[key][1] += row["up"] or 0
            if days:
                self._conn.executemany(
                    "INSERT INTO traffic(bucket,ts,app,link,down,up) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(bucket,ts,app,link) DO UPDATE SET "
                    "down=excluded.down, up=excluded.up",
                    [(DAY, ts, app, link, d, u)
                     for (ts, app, link), (d, u) in days.items()],
                )
            self._conn.commit()
        # Only recorded after the write succeeds, so a failure mid-way means the
        # next run covers the same ground again rather than skipping it.
        self.set_meta("last_rollup", str(int(time.time())))

    def prune(self, minute_days: int = 7, hour_days: int = 90, day_days: int = 0) -> int:
        """Drop detail older than the retention windows. 0 = keep forever."""
        now = time.time()
        removed = 0
        with self._lock:
            for bucket, days in ((MINUTE, minute_days), (HOUR, hour_days), (DAY, day_days)):
                if not days:
                    continue
                cutoff = floor_day(now - days * 86400)
                cur = self._conn.execute(
                    "DELETE FROM traffic WHERE bucket=? AND ts<?", (bucket, cutoff)
                )
                removed += cur.rowcount or 0
            self._conn.commit()
        return removed

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    # ------------------------------------------------------------- reading
    def _bucket_rows(
        self, bucket: str, start: int, end: int, app: str | None = SYSTEM,
        link: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = ("SELECT ts, SUM(down) down, SUM(up) up FROM traffic "
               "WHERE bucket=? AND ts>=? AND ts<?")
        params: list = [bucket, start, end]
        if app is not None:
            sql += " AND app=?"
            params.append(app)
        if link is not None:
            sql += " AND link=?"
            params.append(link)
        sql += " GROUP BY ts ORDER BY ts"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def totals(self, start: int, end: int, app: str | None = SYSTEM,
               bucket: str = DAY, link: str | None = None) -> tuple[int, int]:
        sql = "SELECT COALESCE(SUM(down),0) d, COALESCE(SUM(up),0) u FROM traffic " \
              "WHERE bucket=? AND ts>=? AND ts<?"
        params: list = [bucket, start, end]
        if app is not None:
            sql += " AND app=?"
            params.append(app)
        if link is not None:
            sql += " AND link=?"
            params.append(link)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["d"]), int(row["u"])

    def totals_for_period(self, period: str, app: str | None = SYSTEM,
                          link: str | None = None) -> tuple[int, int]:
        """Totals for the current hour / today / this week / month / year / all time."""
        now = datetime.now()
        if period == "hour":
            return self.totals(floor_hour(now.timestamp()), int(now.timestamp()) + 60,
                               app, HOUR, link)
        if period == "day":
            return self.totals(floor_day(now.timestamp()), int(now.timestamp()) + 60,
                               app, DAY, link)
        if period == "week":
            start = day_start((now - timedelta(days=now.weekday())).date())
        elif period == "month":
            start = day_start(now.replace(day=1).date())
        elif period == "year":
            start = day_start(now.replace(month=1, day=1).date())
        else:  # all
            start = 0
        return self.totals(start, int(now.timestamp()) + 86400, app, DAY, link)

    # ---- chart series -----------------------------------------------------
    def series(self, period: str, count: int | None = None,
               app: str | None = SYSTEM, link: str | None = None) -> list[dict]:
        """Bucketed series for the history chart.

        period: 'hour'  -> the last `count` hours   (default 24)
                'day'   -> the last `count` days    (default 30)
                'week'  -> the last `count` weeks   (default 12)
                'month' -> the last `count` months  (default 12)
                'year'  -> the last `count` years   (default 5)
        Each item: {'ts', 'label', 'sublabel', 'down', 'up'}
        """
        now = datetime.now()
        out: list[dict] = []

        if period == "hour":
            count = count or 24
            end = floor_hour(now.timestamp()) + 3600
            start = end - count * 3600
            rows = {r["ts"]: r for r in self._bucket_rows(HOUR, start, end, app, link)}
            for i in range(count):
                ts = start + i * 3600
                dt = datetime.fromtimestamp(ts)
                r = rows.get(ts)
                out.append({
                    "ts": ts,
                    "label": dt.strftime("%I%p").lstrip("0").lower(),
                    "sublabel": dt.strftime("%a %I:%M %p"),
                    "down": int(r["down"]) if r else 0,
                    "up": int(r["up"]) if r else 0,
                })
            return out

        # everything coarser than an hour is derived from the day bucket
        if period == "day":
            count = count or 30
            days = [(now - timedelta(days=count - 1 - i)).date() for i in range(count)]
            keys = {day_start(d): d for d in days}
            start, end = min(keys), max(keys) + 86400
            rows = {r["ts"]: r for r in self._bucket_rows(DAY, start, end, app, link)}
            for ts, d in sorted(keys.items()):
                r = rows.get(ts)
                out.append({
                    "ts": ts,
                    "label": d.strftime("%d").lstrip("0") if count > 14 else d.strftime("%a"),
                    "sublabel": d.strftime("%A, %b %d %Y"),
                    "down": int(r["down"]) if r else 0,
                    "up": int(r["up"]) if r else 0,
                })
            return out

        if period == "week":
            count = count or 12
            monday = (now - timedelta(days=now.weekday())).date()
            starts = [monday - timedelta(weeks=count - 1 - i) for i in range(count)]
        elif period == "month":
            count = count or 12
            starts = []
            y, m = now.year, now.month
            for _ in range(count):
                starts.append(date(y, m, 1))
                m -= 1
                if m == 0:
                    y, m = y - 1, 12
            starts.reverse()
        else:  # year
            count = count or 5
            starts = [date(now.year - (count - 1 - i), 1, 1) for i in range(count)]

        bounds = [day_start(s) for s in starts]
        end = int(now.timestamp()) + 86400
        rows = self._bucket_rows(DAY, bounds[0], end, app, link)
        totals = [[0, 0] for _ in starts]
        for r in rows:
            ts = r["ts"]
            idx = 0
            for i, b in enumerate(bounds):
                if ts >= b:
                    idx = i
                else:
                    break
            totals[idx][0] += r["down"] or 0
            totals[idx][1] += r["up"] or 0

        for s, b, (d, u) in zip(starts, bounds, totals):
            if period == "week":
                label = s.strftime("%b %d").replace(" 0", " ")
                sub = f"Week of {s.strftime('%B %d, %Y')}"
            elif period == "month":
                label = s.strftime("%b")
                sub = s.strftime("%B %Y")
            else:
                label = s.strftime("%Y")
                sub = s.strftime("%Y")
            out.append({"ts": b, "label": label, "sublabel": sub, "down": d, "up": u})
        return out

    # ---- per application --------------------------------------------------
    def top_apps(self, start: int, end: int, bucket: str = DAY, limit: int = 25,
                 link: str | None = None) -> list[dict]:
        sql = ("SELECT app, SUM(down) d, SUM(up) u FROM traffic "
               "WHERE bucket=? AND ts>=? AND ts<? AND app<>''")
        params: list = [bucket, start, end]
        if link is not None:
            sql += " AND link=?"
            params.append(link)
        sql += " GROUP BY app ORDER BY (SUM(down)+SUM(up)) DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"app": r["app"], "down": int(r["d"] or 0), "up": int(r["u"] or 0)} for r in rows]

    def apps_for_period(self, period: str, limit: int = 25,
                        link: str | None = None) -> list[dict]:
        now = datetime.now()
        end = int(now.timestamp()) + 86400
        if period == "hour":
            return self.top_apps(floor_hour(now.timestamp()), end, HOUR, limit, link)
        if period == "day":
            return self.top_apps(floor_day(now.timestamp()), end, DAY, limit, link)
        if period == "week":
            start = day_start((now - timedelta(days=now.weekday())).date())
        elif period == "month":
            start = day_start(now.replace(day=1).date())
        elif period == "year":
            start = day_start(now.replace(month=1, day=1).date())
        else:
            start = 0
        return self.top_apps(start, end, DAY, limit, link)

    # ---- files ------------------------------------------------------------
    def recent_files(self, limit: int = 500, search: str = "",
                     direction: str | None = None, since: int | None = None,
                     link: str | None = None) -> list[dict]:
        sql = "SELECT * FROM files WHERE 1=1"
        params: list = []
        if link is not None:
            sql += " AND link=?"
            params.append(link)
        if search:
            sql += " AND (name LIKE ? OR source LIKE ? OR folder LIKE ?)"
            like = f"%{search}%"
            params += [like, like, like]
        if direction:
            sql += " AND direction=?"
            params.append(direction)
        if since:
            sql += " AND ts>=?"
            params.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def file_stats(self, since: int | None = None,
                   link: str | None = None) -> dict:
        sql = "SELECT COUNT(*) n, COALESCE(SUM(size),0) s FROM files WHERE 1=1"
        params: list = []
        if since:
            sql += " AND ts>=?"
            params.append(since)
        if link is not None:
            sql += " AND link=?"
            params.append(link)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return {"count": int(row["n"]), "bytes": int(row["s"])}

    def known_paths(self, limit: int = 4000) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM files ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return {r["path"] for r in rows}

    # ---- housekeeping info ------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            traffic_rows = self._conn.execute(
                "SELECT COUNT(*) n FROM traffic").fetchone()["n"]
            file_rows = self._conn.execute("SELECT COUNT(*) n FROM files").fetchone()["n"]
            first = self._conn.execute(
                "SELECT MIN(ts) t FROM traffic WHERE bucket='d'").fetchone()["t"]
        size = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.path + suffix)
            if p.exists():
                size += p.stat().st_size
        return {
            "traffic_rows": traffic_rows,
            "file_rows": file_rows,
            "first_ts": first,
            "db_bytes": size,
        }
