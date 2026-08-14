"""The five pages of the application."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                               QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import autostart
from ..db import floor_day
from ..units import format_bytes, format_rate, format_when, truncate
from . import theme
from .widgets import (BarChart, Card, Legend, ShareBarDelegate, SpeedGraph,
                      StatTile, WanIpChip)

PERIODS = [
    ("hour", "Hourly", "Last 24 hours"),
    ("day", "Daily", "Last 30 days"),
    ("week", "Weekly", "Last 12 weeks"),
    ("month", "Monthly", "Last 12 months"),
    ("year", "Yearly", "Last 5 years"),
]

APP_PERIODS = [
    ("hour", "This hour"),
    ("day", "Today"),
    ("week", "This week"),
    ("month", "This month"),
    ("year", "This year"),
    ("all", "All time"),
]


def open_in_explorer(path: str, select: bool = True) -> None:
    """Reveal a file or folder in the system file manager."""
    try:
        if sys.platform.startswith("win"):
            if select and os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(os.path.normpath(path))  # type: ignore[attr-defined]
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(
                path if os.path.isdir(path) else os.path.dirname(path)))
    except Exception:
        pass


def pill_row(options: list[tuple[str, str]], on_change) -> tuple[QWidget, QButtonGroup]:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    group = QButtonGroup(holder)
    group.setExclusive(True)
    for i, (key, label) in enumerate(options):
        button = QPushButton(label)
        button.setObjectName("Pill")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty("key", key)
        if i == 0:
            button.setChecked(True)
        group.addButton(button, i)
        row.addWidget(button)
    row.addStretch(1)
    group.buttonClicked.connect(lambda b: on_change(b.property("key")))
    return holder, group


def make_table(headers: list[str], stretch_column: int = 0,
               right_align: set[int] | None = None) -> QTableWidget:
    right_align = right_align or set()
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    for i in range(len(headers)):
        item = table.horizontalHeaderItem(i)
        item.setTextAlignment(
            Qt.AlignVCenter | (Qt.AlignRight if i in right_align else Qt.AlignLeft))
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setFocusPolicy(Qt.NoFocus)
    table.verticalHeader().setDefaultSectionSize(38)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    for i in range(len(headers)):
        header.setSectionResizeMode(
            i, QHeaderView.Stretch if i == stretch_column else QHeaderView.ResizeToContents)
    return table


def cell(text: str, align=Qt.AlignVCenter | Qt.AlignLeft, color: str | None = None,
         mono: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(align)
    if color:
        item.setForeground(QColor(color))
    if mono:
        font = QFont()
        font.setStyleHint(QFont.Monospace)
        item.setFont(font)
    return item


class Page(QWidget):
    """Common page chrome: title, hint and a scrollable body."""

    def __init__(self, title: str, hint: str = "", parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(16)

        head = QVBoxLayout()
        head.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("PageTitle")
        head.addWidget(label)
        if hint:
            sub = QLabel(hint)
            sub.setObjectName("PageHint")
            head.addWidget(sub)

        # Title on the left, room for extras on the right of the same row.
        self.header_row = QHBoxLayout()
        self.header_row.setSpacing(16)
        self.header_row.addLayout(head)
        self.header_row.addStretch(1)
        outer.addLayout(self.header_row)

        self.content = QVBoxLayout()
        self.content.setSpacing(16)
        outer.addLayout(self.content, 1)

    def add_header_widget(self, widget: QWidget) -> None:
        """Place a widget at the right of the title row, vertically centred."""
        self.header_row.addWidget(widget, 0, Qt.AlignVCenter)


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------
class DashboardPage(Page):
    def __init__(self, db, engine, settings, parent=None) -> None:
        super().__init__("Dashboard", "Live throughput and totals for this machine.", parent)
        self.db, self.engine, self.settings = db, engine, settings

        self.wan_chip = WanIpChip()
        self.add_header_widget(self.wan_chip)
        engine.wan.resolved.connect(self.wan_chip.set_address)
        self.wan_chip.update_from(engine.wan, settings.get("show_wan_ip", True))

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.tiles: dict[str, StatTile] = {}
        for key, label in (("day", "Today"), ("week", "This week"),
                           ("month", "This month"), ("year", "This year")):
            tile = StatTile(label)
            self.tiles[key] = tile
            tiles.addWidget(tile)
        self.content.addLayout(tiles)

        live_card = Card("Live throughput", "last 2 minutes")
        self.legend = Legend([("Download", theme.DOWN), ("Upload", theme.UP)])
        live_card.add_header_widget(self.legend)
        self.graph = SpeedGraph()
        live_card.add(self.graph, 1)
        self.content.addWidget(live_card, 1)

        columns = QHBoxLayout()
        columns.setSpacing(14)

        apps_card = Card("Top applications today")
        self.apps_table = make_table(["Application", "Down", "Up", "Share"], 0, {1, 2})
        self.apps_table.setItemDelegateForColumn(3, ShareBarDelegate(theme.DOWN, self))
        self.apps_table.setMinimumHeight(200)
        apps_card.add(self.apps_table, 1)
        self.apps_note = QLabel("")
        self.apps_note.setObjectName("CardHint")
        self.apps_note.setWordWrap(True)
        self.apps_note.hide()
        apps_card.add(self.apps_note)
        columns.addWidget(apps_card, 1)

        files_card = Card("Recent downloads")
        self.files_table = make_table(["File", "Size", "When"], 0, {1, 2})
        self.files_table.setMinimumHeight(200)
        self.files_table.itemDoubleClicked.connect(self._open_file_row)
        files_card.add(self.files_table, 1)
        columns.addWidget(files_card, 1)

        self.content.addLayout(columns, 1)
        self._file_paths: list[str] = []

    def _open_file_row(self, item) -> None:
        row = item.row()
        if 0 <= row < len(self._file_paths):
            open_in_explorer(self._file_paths[row])

    def refresh_live(self) -> None:
        _, down, up = self.engine.live_series()
        unit = self.settings.get("units", "auto")
        self.graph.unit = unit
        self.graph.set_data(down, up)
        self.legend.set_value("Download", format_rate(down[-1] if down else 0, unit))
        self.legend.set_value("Upload", format_rate(up[-1] if up else 0, unit))

    def refresh(self) -> None:
        unit = self.settings.get("units", "auto")
        self.wan_chip.update_from(self.engine.wan,
                                  self.settings.get("show_wan_ip", True))
        for key, tile in self.tiles.items():
            d, u = self.db.totals_for_period(key)
            tile.set_values(d, u, unit)

        rows = self.db.apps_for_period("day", limit=8)
        total = max(1, sum(r["down"] + r["up"] for r in rows))
        self.apps_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.apps_table.setItem(i, 0, cell(truncate(row["app"], 28)))
            self.apps_table.setItem(i, 1, cell(format_bytes(row["down"], unit),
                                               Qt.AlignVCenter | Qt.AlignRight, theme.DOWN))
            self.apps_table.setItem(i, 2, cell(format_bytes(row["up"], unit),
                                               Qt.AlignVCenter | Qt.AlignRight, theme.UP))
            share = QTableWidgetItem()
            share.setData(Qt.UserRole, (row["down"] + row["up"]) / total)
            self.apps_table.setItem(i, 3, share)

        if not rows:
            self.apps_note.setText(
                self.engine.per_app_note()
                or "No per-application traffic recorded yet today.")
            self.apps_note.show()
        else:
            self.apps_note.hide()

        files = self.db.recent_files(limit=8)
        self._file_paths = [f["path"] for f in files]
        self.files_table.setRowCount(len(files))
        for i, f in enumerate(files):
            name_item = cell(truncate(f["name"], 34))
            name_item.setToolTip(f["path"] + (f"\nFrom {f['source']}" if f["source"] else ""))
            self.files_table.setItem(i, 0, name_item)
            self.files_table.setItem(i, 1, cell(format_bytes(f["size"], unit),
                                                Qt.AlignVCenter | Qt.AlignRight))
            self.files_table.setItem(i, 2, cell(format_when(f["ts"]),
                                                Qt.AlignVCenter | Qt.AlignRight,
                                                theme.TEXT_SECONDARY))


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------
class HistoryPage(Page):
    def __init__(self, db, settings, parent=None) -> None:
        super().__init__("History",
                         "Usage per hour, day, week, month and year.", parent)
        self.db, self.settings = db, settings
        self.period = "hour"

        row, self.group = pill_row([(k, label) for k, label, _ in PERIODS],
                                   self.set_period)
        self.content.addWidget(row)

        summary = QHBoxLayout()
        summary.setSpacing(14)
        self.total_tile = StatTile("Period total")
        self.peak_tile = StatTile("Busiest bucket")
        self.avg_tile = StatTile("Average per bucket")
        for tile in (self.total_tile, self.peak_tile, self.avg_tile):
            summary.addWidget(tile)
        self.content.addLayout(summary)

        self.chart_card = Card("Last 24 hours")
        self.legend = Legend([("Download", theme.DOWN), ("Upload", theme.UP)])
        self.chart_card.add_header_widget(self.legend)
        self.chart = BarChart()
        self.chart_card.add(self.chart, 1)
        self.content.addWidget(self.chart_card, 3)

        table_card = Card("Breakdown", "the same numbers as a table")
        self.table = make_table(["Period", "Download", "Upload", "Total"], 0, {1, 2, 3})
        table_card.add(self.table, 1)
        self.content.addWidget(table_card, 2)

    def set_period(self, period: str) -> None:
        self.period = period
        for button in self.group.buttons():
            button.setChecked(button.property("key") == period)
        self.refresh()

    def refresh(self) -> None:
        unit = self.settings.get("units", "auto")
        data = self.db.series(self.period)
        title = next(h for k, _, h in PERIODS if k == self.period)
        self.chart_card.title_label.setText(title)
        self.chart.set_data(data, unit)

        down = sum(d["down"] for d in data)
        up = sum(d["up"] for d in data)
        self.total_tile.set_values(down, up, unit)
        self.legend.set_value("Download", format_bytes(down, unit))
        self.legend.set_value("Upload", format_bytes(up, unit))

        if data:
            peak = max(data, key=lambda d: d["down"] + d["up"])
            self.peak_tile.set_values(peak["down"], peak["up"], unit)
            self.peak_tile.label.setText(f"BUSIEST — {peak['sublabel'].upper()}")
            active = [d for d in data if d["down"] + d["up"] > 0] or data
            self.avg_tile.set_values(down // len(active), up // len(active), unit)

        self.table.setRowCount(len(data))
        for i, item in enumerate(reversed(data)):
            self.table.setItem(i, 0, cell(item["sublabel"]))
            self.table.setItem(i, 1, cell(format_bytes(item["down"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight, theme.DOWN))
            self.table.setItem(i, 2, cell(format_bytes(item["up"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight, theme.UP))
            self.table.setItem(i, 3, cell(format_bytes(item["down"] + item["up"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight))


# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------
class AppsPage(Page):
    def __init__(self, db, engine, settings, parent=None) -> None:
        super().__init__("Applications",
                         "Which programs used the connection.", parent)
        self.db, self.engine, self.settings = db, engine, settings
        self.period = "day"

        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(16, 12, 16, 12)
        banner_row.setSpacing(12)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        banner_row.addWidget(self.banner_text, 1)
        self.restart_button = QPushButton("Restart as administrator")
        self.restart_button.clicked.connect(self._restart_elevated)
        banner_row.addWidget(self.restart_button)
        self.content.addWidget(self.banner)

        row, self.group = pill_row(APP_PERIODS, self.set_period)
        for button in self.group.buttons():
            button.setChecked(button.property("key") == "day")
        self.content.addWidget(row)

        card = Card("Traffic by application")
        self.table = make_table(
            ["#", "Application", "Download", "Upload", "Total", "Share"], 1,
            {0, 2, 3, 4})
        self.table.setItemDelegateForColumn(5, ShareBarDelegate(theme.DOWN, self))
        card.add(self.table, 1)
        self.content.addWidget(card, 1)

    def _restart_elevated(self) -> None:
        if autostart.elevated_relaunch():
            from PySide6.QtWidgets import QApplication
            QApplication.quit()
        else:
            QMessageBox.information(
                self, "NetPulse",
                "Windows declined the elevation request. Right-click "
                "run-as-admin.bat and choose 'Run as administrator' instead.")

    def set_period(self, period: str) -> None:
        self.period = period
        self.refresh()

    def refresh(self) -> None:
        unit = self.settings.get("units", "auto")
        note = self.engine.per_app_note()
        self.banner.setVisible(bool(note))
        self.restart_button.setVisible("administrator" in note)
        if note:
            self.banner_text.setText(
                note + "  Machine-wide totals on the other pages are unaffected.")

        rows = self.db.apps_for_period(self.period, limit=60)
        total = max(1, sum(r["down"] + r["up"] for r in rows))
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            rank = cell(str(i + 1), Qt.AlignVCenter | Qt.AlignRight, theme.MUTED)
            self.table.setItem(i, 0, rank)
            self.table.setItem(i, 1, cell(row["app"]))
            self.table.setItem(i, 2, cell(format_bytes(row["down"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight, theme.DOWN))
            self.table.setItem(i, 3, cell(format_bytes(row["up"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight, theme.UP))
            self.table.setItem(i, 4, cell(format_bytes(row["down"] + row["up"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight))
            share = QTableWidgetItem()
            share.setData(Qt.UserRole, (row["down"] + row["up"]) / total)
            self.table.setItem(i, 5, share)


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
class FilesPage(Page):
    def __init__(self, db, engine, settings, parent=None) -> None:
        super().__init__(
            "Files",
            "Every file that arrived in a watched folder, with its source where known.",
            parent)
        self.db, self.engine, self.settings = db, engine, settings

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by file name, source or folder…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        controls.addWidget(self.search, 1)

        self.range_box = QComboBox()
        self.range_box.addItems(["All time", "Today", "Last 7 days", "Last 30 days"])
        self.range_box.currentIndexChanged.connect(lambda _: self.refresh())
        controls.addWidget(self.range_box)

        self.rescan_button = QPushButton("Rescan browsers")
        self.rescan_button.clicked.connect(self._rescan)
        controls.addWidget(self.rescan_button)
        self.content.addLayout(controls)

        self.summary = QLabel("")
        self.summary.setObjectName("PageHint")
        self.content.addWidget(self.summary)

        card = Card("Transferred files", "double-click a row to show it in Explorer")
        self.table = make_table(
            ["Name", "Size", "When", "Source", "Via", "Folder"], 0, {1})
        self.table.itemDoubleClicked.connect(self._open_row)
        card.add(self.table, 1)
        self.content.addWidget(card, 1)
        self._paths: list[str] = []

    def _rescan(self) -> None:
        self.rescan_button.setEnabled(False)
        self.rescan_button.setText("Scanning…")
        added = 0
        try:
            added = self.engine.files.scan_browsers(lookback_days=365)
        except Exception:
            pass
        self.rescan_button.setText("Rescan browsers")
        self.rescan_button.setEnabled(True)
        self.refresh()
        QMessageBox.information(
            self, "NetPulse",
            f"Browser history scan complete — {added} new file(s) added.")

    def _open_row(self, item) -> None:
        row = item.row()
        if 0 <= row < len(self._paths):
            open_in_explorer(self._paths[row])

    def refresh(self) -> None:
        unit = self.settings.get("units", "auto")
        since = None
        choice = self.range_box.currentIndex()
        if choice == 1:
            since = floor_day(time.time())
        elif choice == 2:
            since = int(time.time() - 7 * 86400)
        elif choice == 3:
            since = int(time.time() - 30 * 86400)

        files = self.db.recent_files(limit=1000, search=self.search.text().strip(),
                                     since=since)
        self._paths = [f["path"] for f in files]
        total = sum(f["size"] for f in files)
        self.summary.setText(
            f"{len(files):,} file(s) · {format_bytes(total, unit)}"
            + ("" if self.settings.get("track_files")
               else "  —  file tracking is switched off in Settings"))

        self.table.setRowCount(len(files))
        for i, f in enumerate(files):
            name = cell(truncate(f["name"], 46))
            name.setToolTip(f["path"])
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, cell(format_bytes(f["size"], unit),
                                          Qt.AlignVCenter | Qt.AlignRight))
            self.table.setItem(i, 2, cell(format_when(f["ts"]),
                                          Qt.AlignVCenter | Qt.AlignLeft,
                                          theme.TEXT_SECONDARY))
            self.table.setItem(i, 3, cell(truncate(f["source"] or "—", 30),
                                          Qt.AlignVCenter | Qt.AlignLeft,
                                          theme.DOWN if f["source"] else theme.MUTED))
            self.table.setItem(i, 4, cell(f["app"] or "—",
                                          Qt.AlignVCenter | Qt.AlignLeft,
                                          theme.TEXT_SECONDARY))
            folder = cell(truncate(f["folder"], 40), Qt.AlignVCenter | Qt.AlignLeft,
                          theme.MUTED)
            folder.setToolTip(f["folder"])
            self.table.setItem(i, 5, folder)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
class SettingsPage(Page):
    def __init__(self, db, engine, settings, parent=None) -> None:
        super().__init__("Settings", "Collection, retention and appearance.", parent)
        self.db, self.engine, self.settings = db, engine, settings

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("ScrollBody")
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(16)
        scroll.setWidget(inner)
        self.content.addWidget(scroll, 1)

        # ---- collection ----------------------------------------------------
        collection = Card("Collection")
        self.per_app_check = QCheckBox(
            "Track usage per application (needs administrator rights)")
        self.per_app_check.setChecked(settings.get("track_per_app", True))
        self.per_app_check.toggled.connect(engine.enable_per_app)
        collection.add(self.per_app_check)

        self.files_check = QCheckBox("Log files that arrive in the watched folders")
        self.files_check.setChecked(settings.get("track_files", True))
        self.files_check.toggled.connect(
            lambda v: (settings.set("track_files", v),
                       engine.files.restart() if v else engine.files.stop()))
        collection.add(self.files_check)

        self.history_check = QCheckBox(
            "Read browser download history to identify where files came from")
        self.history_check.setChecked(settings.get("read_browser_history", True))
        self.history_check.toggled.connect(
            lambda v: settings.set("read_browser_history", v))
        collection.add(self.history_check)

        self.wan_check = QCheckBox("Show my public (WAN) IP on the dashboard")
        self.wan_check.setChecked(settings.get("show_wan_ip", True))
        self.wan_check.toggled.connect(engine.enable_wan_ip)
        collection.add(self.wan_check)

        wan_note = QLabel(
            "Finding this needs one small request to an outside service "
            "(ipify.org and similar) at start-up and every 15 minutes. "
            "Nothing but the request itself is sent.")
        wan_note.setObjectName("CardHint")
        wan_note.setWordWrap(True)
        wan_note.setContentsMargins(26, 0, 0, 4)
        collection.add(wan_note)

        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Ignore files smaller than"))
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 1024 * 1024)
        self.min_size.setSuffix(" KB")
        self.min_size.setValue(int(settings.get("min_file_bytes", 16384)) // 1024)
        self.min_size.valueChanged.connect(
            lambda v: settings.set("min_file_bytes", v * 1024))
        min_row.addWidget(self.min_size)
        min_row.addStretch(1)
        holder = QWidget()
        holder.setLayout(min_row)
        collection.add(holder)
        body.addWidget(collection)

        # ---- folders -------------------------------------------------------
        folders = Card("Watched folders",
                       "any file that lands in these is logged, whatever downloaded it")
        self.folder_list = QListWidget()
        self.folder_list.addItems(settings.get("watch_folders", []))
        self.folder_list.setMaximumHeight(150)
        folders.add(self.folder_list)
        folder_buttons = QHBoxLayout()
        add_button = QPushButton("Add folder…")
        add_button.clicked.connect(self._add_folder)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self._remove_folder)
        folder_buttons.addWidget(add_button)
        folder_buttons.addWidget(remove_button)
        folder_buttons.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(folder_buttons)
        folders.add(wrap)
        body.addWidget(folders)

        # ---- appearance / behaviour ----------------------------------------
        behaviour = Card("Appearance and behaviour")
        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel("Show sizes in"))
        self.units_box = QComboBox()
        self.units_box.addItems(["Automatic", "KB", "MB", "GB"])
        current = settings.get("units", "auto")
        self.units_box.setCurrentIndex(
            {"auto": 0, "KB": 1, "MB": 2, "GB": 3}.get(current, 0))
        self.units_box.currentIndexChanged.connect(self._change_units)
        unit_row.addWidget(self.units_box)
        unit_row.addStretch(1)
        unit_wrap = QWidget()
        unit_wrap.setLayout(unit_row)
        behaviour.add(unit_wrap)

        self.autostart_check = QCheckBox("Start NetPulse when I sign in to Windows")
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.toggled.connect(self._toggle_autostart)
        behaviour.add(self.autostart_check)

        self.autostart_note = QLabel(autostart.describe())
        self.autostart_note.setObjectName("CardHint")
        self.autostart_note.setWordWrap(True)
        self.autostart_note.setContentsMargins(26, 0, 0, 6)
        behaviour.add(self.autostart_note)

        self.minimized_check = QCheckBox("Start minimised to the notification area")
        self.minimized_check.setChecked(settings.get("start_minimized", False))
        self.minimized_check.toggled.connect(
            lambda v: settings.set("start_minimized", v))
        behaviour.add(self.minimized_check)

        self.tray_check = QCheckBox("Keep recording in the tray when I close the window")
        self.tray_check.setChecked(settings.get("close_to_tray", True))
        self.tray_check.toggled.connect(lambda v: settings.set("close_to_tray", v))
        behaviour.add(self.tray_check)
        body.addWidget(behaviour)

        # ---- retention -----------------------------------------------------
        retention = Card("Data retention",
                         "detail is thinned as it ages so the database stays small")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.retain_minute = QSpinBox()
        self.retain_hour = QSpinBox()
        for spin, key, default in ((self.retain_minute, "retain_minute_days", 7),
                                   (self.retain_hour, "retain_hour_days", 90)):
            spin.setRange(1, 3650)
            spin.setSuffix(" days")
            spin.setValue(int(settings.get(key, default)))
            spin.valueChanged.connect(lambda v, k=key: settings.set(k, v))
        grid.addWidget(QLabel("Keep minute-by-minute detail for"), 0, 0)
        grid.addWidget(self.retain_minute, 0, 1)
        grid.addWidget(QLabel("Keep hourly detail for"), 1, 0)
        grid.addWidget(self.retain_hour, 1, 1)
        daily_note = QLabel("Daily totals are kept forever.")
        daily_note.setObjectName("CardHint")
        grid.addWidget(daily_note, 2, 0, 1, 2)
        grid.setColumnStretch(2, 1)
        grid_wrap = QWidget()
        grid_wrap.setLayout(grid)
        retention.add(grid_wrap)
        body.addWidget(retention)

        # ---- data ----------------------------------------------------------
        data_card = Card("Data")
        self.db_info = QLabel("")
        self.db_info.setObjectName("CardHint")
        data_card.add(self.db_info)
        buttons = QHBoxLayout()
        for label, slot in (("Open data folder", self._open_data),
                            ("Export CSV…", self._export),
                            ("Compact database", self._compact)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        reset = QPushButton("Reset all statistics")
        reset.setObjectName("Danger")
        reset.clicked.connect(self._reset)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        button_wrap = QWidget()
        button_wrap.setLayout(buttons)
        data_card.add(button_wrap)
        body.addWidget(data_card)

        about = QLabel(
            "NetPulse · totals come from the Windows adapter counters; "
            "per-application figures come from the kernel network trace.")
        about.setObjectName("CardHint")
        about.setWordWrap(True)
        body.addWidget(about)
        body.addStretch(1)

    # ------------------------------------------------------------------ slots
    def _change_units(self, index: int) -> None:
        self.settings.set("units", ["auto", "KB", "MB", "GB"][index])

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to watch")
        if folder:
            folders = list(self.settings.get("watch_folders", []))
            if folder not in folders:
                folders.append(folder)
                self.settings.set("watch_folders", folders)
                self.folder_list.addItem(folder)
                self.engine.files.restart()

    def _remove_folder(self) -> None:
        row = self.folder_list.currentRow()
        if row < 0:
            return
        item = self.folder_list.takeItem(row)
        folders = [f for f in self.settings.get("watch_folders", []) if f != item.text()]
        self.settings.set("watch_folders", folders)
        self.engine.files.restart()

    def _toggle_autostart(self, enabled: bool) -> None:
        ok, message = autostart.set_enabled(enabled)
        self.settings.set("autostart", enabled and ok)
        self.autostart_note.setText(autostart.describe())
        if not ok:
            QMessageBox.warning(self, "NetPulse", message)
            # Put the tick back where reality is.
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_check.blockSignals(False)
        elif enabled:
            QMessageBox.information(self, "NetPulse", message)

    def _open_data(self) -> None:
        from ..config import data_dir
        open_in_explorer(str(data_dir()), select=False)

    def _compact(self) -> None:
        removed = self.db.prune(
            int(self.settings.get("retain_minute_days", 7)),
            int(self.settings.get("retain_hour_days", 90)),
            int(self.settings.get("retain_day_days", 0)))
        self.db.vacuum()
        self.refresh()
        QMessageBox.information(self, "NetPulse",
                                f"Database compacted — {removed:,} stale row(s) removed.")

    def _export(self) -> None:
        import csv
        path, _ = QFileDialog.getSaveFileName(
            self, "Export usage history", "netpulse-export.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["section", "period", "label", "download_bytes",
                                 "upload_bytes", "total_bytes"])
                for period in ("hour", "day", "week", "month", "year"):
                    for item in self.db.series(period):
                        writer.writerow(["traffic", period, item["sublabel"],
                                         item["down"], item["up"],
                                         item["down"] + item["up"]])
                writer.writerow([])
                writer.writerow(["section", "application", "period",
                                 "download_bytes", "upload_bytes", "total_bytes"])
                for period in ("day", "week", "month", "year", "all"):
                    for row in self.db.apps_for_period(period, limit=200):
                        writer.writerow(["application", row["app"], period,
                                         row["down"], row["up"],
                                         row["down"] + row["up"]])
                writer.writerow([])
                writer.writerow(["section", "file", "size_bytes", "when",
                                 "source", "via", "folder"])
                for f in self.db.recent_files(limit=20000):
                    writer.writerow(["file", f["name"], f["size"],
                                     datetime.fromtimestamp(f["ts"]).isoformat(" ", "seconds"),
                                     f["source"] or "", f["app"] or "", f["folder"]])
            QMessageBox.information(self, "NetPulse", f"Exported to {path}")
        except OSError as exc:
            QMessageBox.warning(self, "NetPulse", f"Could not write the file: {exc}")

    def _reset(self) -> None:
        confirm = QMessageBox.question(
            self, "Reset all statistics",
            "This permanently deletes every recorded usage figure and the file "
            "log. This cannot be undone.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        with self.db._lock:                     # noqa: SLF001 - intentional
            self.db._conn.execute("DELETE FROM traffic")
            self.db._conn.execute("DELETE FROM files")
            self.db._conn.commit()
        self.db.vacuum()
        self.refresh()

    def refresh(self) -> None:
        # Autostart can be changed outside the app, so re-read it rather than
        # trusting the tick.
        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(autostart.is_enabled())
        self.autostart_check.blockSignals(False)
        self.autostart_note.setText(autostart.describe())

        stats = self.db.stats()
        since = ("since " + datetime.fromtimestamp(stats["first_ts"]).strftime("%d %b %Y")
                 if stats["first_ts"] else "no data yet")
        self.db_info.setText(
            f"{format_bytes(stats['db_bytes'])} on disk · "
            f"{stats['traffic_rows']:,} usage rows · "
            f"{stats['file_rows']:,} file records · {since}")
