<h1 align="center">NetPulse</h1>

<p align="center">
  A network usage monitor for Windows.<br>
  How much you upload and download â€” per hour, day, week, month and year â€”
  broken down by application, with a log of the files that arrive on your machine.
</p>

<p align="center">
  <a href="https://github.com/zimba7768/netpulse/actions/workflows/tests.yml">
    <img alt="tests" src="https://github.com/zimba7768/netpulse/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <a href="LICENSE"><img alt="licence" src="https://img.shields.io/badge/licence-MIT-green"></a>
</p>

![The NetPulse dashboard](docs/screenshots/dashboard.png)

---

## What it does

- **Totals for every period you'd want** â€” this hour, today, this week, this
  month, this year, all time. Hourly, daily, weekly, monthly and yearly charts,
  each hoverable, each also rendered as a plain table.
- **Per-application breakdown** â€” which programs actually used the connection,
  ranked, for any period.
- **A file log** â€” every file that lands in your watched folders, with its size,
  when it arrived, and where it came from.
- **Live throughput** â€” a two-minute rolling graph, plus a tray icon whose
  arrows brighten with activity.
- **Local and private** â€” one SQLite file in `%APPDATA%\NetPulse`. No account,
  no cloud, no telemetry, no network calls of its own.

## Screenshots

<details>
<summary>History, Applications and Files</summary>

**History** â€” five bucketings of the same data, with the numbers repeated as a
table underneath.

![Monthly history](docs/screenshots/history-month.png)
![Hourly history](docs/screenshots/history-hour.png)

**Applications** â€” which programs used the connection.

![Applications](docs/screenshots/applications.png)

**Files** â€” what arrived, how big, and from where.

![Files](docs/screenshots/files.png)

</details>

## Install

Requires **Windows 10 or 11** and **Python 3.10+**.

1. Download or clone this repository.
2. Double-click **`install.bat`**. It locates Python for you â€” the `py`
   launcher, your `PATH`, the registry, the usual python.org locations, and
   Anaconda / Miniconda â€” then installs the dependencies.
   If it finds no suitable Python it offers to install one via winget.
3. Start it:
   - **`run.bat`** â€” normal start.
   - **`run-as-admin.bat`** â€” start elevated, which additionally enables the
     per-application breakdown.

Optionally run **`make-shortcut.bat`** for a Desktop shortcut carrying the app's
own icon, which you can drag to the taskbar to pin.

The interpreter that ends up being used is cached in `python-path.txt`. To point
NetPulse at a different Python, edit that file â€” one line, the full path to
`python.exe`.

## How it measures

Three independent sources, and it's worth knowing which number comes from where.

### Totals â€” adapter counters

Machine-wide upload and download come from the byte counters Windows keeps per
network adapter, the same source Task Manager uses. Exact, never misses traffic,
needs no special permissions. Virtual adapters (Hyper-V, WSL, VMware,
VirtualBox, loopback) are excluded so nothing is counted twice.

### Per-application â€” kernel network trace

Windows exposes no ordinary API for per-process byte counts. NetPulse reads what
Task Manager's own network column reads: the `Microsoft-Windows-Kernel-Network`
ETW provider, which emits an event for every TCP/UDP send and receive along with
the owning process ID.

Opening a real-time kernel trace requires administrator rights, so this is the
one feature that needs the elevated start. Without it everything else still
works and the Applications page says so. Traffic to `127.0.0.1` is excluded, so
local dev servers don't inflate the numbers.

### Files â€” watched folders + browser history

Two sources that reinforce each other:

- **Folder watching** sees every file that lands in Downloads, Desktop,
  Documents, Pictures, Videos and Music â€” whatever put it there: a browser, a
  torrent client, Steam, an installer, a copy from a network share. A file is
  only logged once its size stops changing, so half-finished `.crdownload` and
  `.part` files never appear.
- **Browser download history** (Chrome, Edge, Brave, Opera, Vivaldi, Firefox â€”
  all profiles) supplies what folder watching can't know: the **source URL**.
  Records are matched back by path, and downloads that landed outside a watched
  folder are added from here too.

## What it deliberately doesn't do

**Track individual file uploads.** Seeing a photo posted to a website or an
attachment sent through webmail would mean decrypting your HTTPS traffic through
a local proxy â€” installing a root certificate, breaking every app that pins its
certificate, and getting flagged by antivirus. It would also still miss anything
that bypasses the system proxy. NetPulse doesn't go there. What it gives you
instead is upload **volume** per application and per period, which answers "what
has been uploading?" without touching encrypted traffic.

## Starting with Windows

Off by default; turn it on in **Settings â†’ Appearance and behaviour**. The line
under the tick box always says which of two mechanisms is in use:

- **Scheduled task** â€” used when NetPulse is elevated at the moment you tick the
  box. Windows starts it elevated and silently at sign-in, so per-application
  tracking works from the start. It lives in Task Scheduler and, importantly,
  **does not appear in Task Manager's Startup tab**.
- **Startup entry** â€” the fallback when NetPulse isn't elevated. Windows always
  launches `Run` entries unelevated and cannot show a UAC prompt at sign-in, so
  per-application tracking will be off.

So: start with `run-as-admin.bat`, then tick the box. If you already ticked it
unelevated, start elevated and untick/re-tick â€” it upgrades itself.

## Storage and retention

Detail is thinned as it ages, so the database stays small however long it runs:

| Granularity | Kept for | Feeds |
|---|---|---|
| Per minute | 7 days | short-term detail |
| Per hour | 90 days | the hourly and daily views |
| Per day | forever | the weekly, monthly and yearly views |

Both windows are adjustable in Settings; daily totals are never deleted. Expect
a few megabytes per year. Rollups are recomputed rather than accumulated, so an
abrupt shutdown can never double-count. Buckets align to **local** midnight and
local hour boundaries, so "per day" means what your clock says, across DST
changes included.

## Development

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v   # 35 tests
python -m pyflakes netpulse main.py tools tests
```

Two extra harnesses, both used by CI:

```bash
python tools/screenshot.py out/     # render every page offscreen, with demo data
python tools/smoketest.py           # run the real engine against real traffic
```

`tools/screenshot.py` is how the images above are produced â€” it seeds a database
with a year of plausible traffic and grabs each page with the offscreen Qt
platform, so the interface can be reviewed without a desktop session.

<details>
<summary>Layout</summary>

```
main.py                     entry point, single-instance guard
install.bat                 finds Python, installs dependencies
run.bat / run-as-admin.bat  launchers
make-shortcut.bat / .ps1    Desktop shortcut with the app icon
_find-python.bat            shared interpreter discovery
netpulse/
  config.py                 paths and persisted settings
  units.py                  byte / rate / time formatting
  db.py                     SQLite schema, rollups, retention, queries
  engine.py                 background collection loop
  autostart.py              scheduled task / Run key, elevated relaunch
  collectors/
    net_system.py           adapter counter sampling
    net_etw.py              per-process attribution via ETW
    files.py                folder watching + browser history
  ui/
    theme.py                colour roles and stylesheet
    assets.py               generated icons and control artwork
    widgets.py              cards, stat tiles, both charts
    pages.py                the five pages
    main_window.py          sidebar navigation and refresh clock
    tray.py                 notification-area icon and app icon
tests/                      storage, autostart and icon tests
tools/                      screenshot and smoke-test harnesses
```

</details>

The charts are painted directly with `QPainter` rather than pulled from a
plotting library, which keeps the dependency list to three packages and gives
exact control over the marks. The colours are a categorical palette validated
for colour-vision deficiency against the dark surface.

## Troubleshooting

**"No Python 3.10 or newer was found."** Run `py -0p` in a Command Prompt to
list every interpreter the launcher knows about, then paste the full path into
`python-path.txt` and run `install.bat` again. Anaconda is fine â€” it just keeps
itself off the system `PATH`, which is the usual reason `python` "isn't found"
on a machine that clearly has it.

**"pywintrace is not installed."** `pip install pywintrace` â€” the current
release is 0.2.0, don't pin higher. It's optional; only the per-application
breakdown needs it.

**"Could not start the ETW session."** A previous session is still registered.
Run `logman stop NetPulseKernelNet -ets` in an elevated Command Prompt and
restart.

**Numbers look higher than my ISP reports.** Adapter counters include protocol
overhead and local network traffic â€” copying from a NAS, casting to a TV. ISPs
count only what crosses their border.

**A VPN is running and totals look doubled.** Some VPN clients present a second
adapter carrying the same traffic. Most are excluded by name already; if yours
isn't, add it to `EXCLUDE_HINTS` in `netpulse/collectors/net_system.py`.

**I ticked "start when I sign in" but it's not in Task Manager's Startup tab.**
Expected if it registered as a scheduled task â€” check `taskschd.msc` instead.

## Licence

MIT â€” see [LICENSE](LICENSE).
