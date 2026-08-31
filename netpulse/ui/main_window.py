"""The main window: sidebar navigation, pages, and the periodic refresh clock."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel,
                               QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..units import format_rate
from . import theme
from .assets import nav_icon
from .pages import (AppsPage, DashboardPage, FilesPage, HistoryPage,
                    SettingsPage, VpnPage)
from .tray import Tray, app_icon

NAV = [
    ("Dashboard", "dashboard"),
    ("History", "history"),
    ("Applications", "applications"),
    ("Files", "files"),
    ("VPN", "vpn"),
    ("Settings", "settings"),
]


class MainWindow(QWidget):
    def __init__(self, db, engine, settings) -> None:
        super().__init__()
        self.db, self.engine, self.settings = db, engine, settings
        self._quitting = False
        self._rendered_day = date.today()

        self.setObjectName("Root")
        self.setWindowTitle("NetPulse — Network Usage Monitor")
        self.setWindowIcon(app_icon())
        self.resize(1180, 780)
        self.setMinimumSize(940, 620)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(db, engine, settings)
        self.history = HistoryPage(db, settings)
        self.apps = AppsPage(db, engine, settings)
        self.files = FilesPage(db, engine, settings)
        self.vpn = VpnPage(db, engine, settings)
        self.settings_page = SettingsPage(db, engine, settings)
        for page in (self.dashboard, self.history, self.apps,
                     self.files, self.vpn, self.settings_page):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        self.tray = Tray(self, engine, settings)
        self.tray.show()

        engine.tick.connect(self._on_tick)
        engine.file_found.connect(self._on_file_found)
        engine.status_changed.connect(self.status_label.setText)

        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._refresh_live)
        self._live_timer.start(1000)

        self._slow_timer = QTimer(self)
        self._slow_timer.timeout.connect(self.refresh_current)
        self._slow_timer.start(5000)

        self.refresh_all()

    # --------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(212)
        box = QVBoxLayout(sidebar)
        box.setContentsMargins(14, 20, 14, 16)
        box.setSpacing(6)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        name = QLabel("NetPulse")
        name.setObjectName("BrandName")
        tag = QLabel("NETWORK USAGE")
        tag.setObjectName("BrandTag")
        brand.addWidget(name)
        brand.addWidget(tag)
        wrapper = QWidget()
        wrapper.setLayout(brand)
        wrapper.setContentsMargins(6, 0, 0, 14)
        box.addWidget(wrapper)

        self.nav_group = QButtonGroup(sidebar)
        self.nav_group.setExclusive(True)
        for index, (label, kind) in enumerate(NAV):
            button = QPushButton(f"   {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(index == 0)
            button.setIconSize(QSize(17, 17))
            idle = nav_icon(kind, theme.MUTED)
            active = nav_icon(kind, theme.TEXT)
            button.setIcon(active if index == 0 else idle)
            button.toggled.connect(
                lambda checked, b=button, a=active, i=idle:
                b.setIcon(a if checked else i))
            self.nav_group.addButton(button, index)
            box.addWidget(button)
        self.nav_group.idClicked.connect(self._navigate)

        box.addStretch(1)

        self.live_down = QLabel("0 B/s")
        self.live_up = QLabel("0 B/s")
        for label, color, arrow in ((self.live_down, theme.DOWN, "▼"),
                                    (self.live_up, theme.UP, "▲")):
            row = QHBoxLayout()
            row.setContentsMargins(6, 0, 0, 0)
            row.setSpacing(8)
            marker = QLabel(arrow)
            marker.setStyleSheet(f"color:{color}; font-size:11px;")
            label.setStyleSheet(f"color:{theme.TEXT}; font-size:13px; font-weight:600;")
            row.addWidget(marker)
            row.addWidget(label)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            box.addWidget(holder)

        self.status_label = QLabel("Starting…")
        self.status_label.setObjectName("Status")
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(6, 10, 0, 0)
        box.addWidget(self.status_label)
        return sidebar

    def _navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.refresh_current()

    # -------------------------------------------------------------- refreshes
    def _on_tick(self, down: float, up: float) -> None:
        unit = self.settings.get("units", "auto")
        self.live_down.setText(format_rate(down, unit))
        self.live_up.setText(format_rate(up, unit))
        self.tray.update_rates(down, up, unit)

    def _refresh_live(self) -> None:
        if not self.isVisible():
            return
        page = self.stack.currentWidget()
        if hasattr(page, "refresh_live"):
            page.refresh_live()

    def refresh_current(self) -> None:
        # Every "this hour / today / this week" figure is relative to the
        # current date, so when the date rolls over each page is stale by
        # definition — including ones that are not on screen. Refresh the lot
        # once, rather than trusting that whichever page happens to be visible
        # will notice.
        today = date.today()
        if today != self._rendered_day:
            self._rendered_day = today
            self.refresh_all()
            return

        if not self.isVisible():
            return
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def refresh_all(self) -> None:
        for page in (self.dashboard, self.history, self.apps,
                     self.files, self.vpn, self.settings_page):
            try:
                page.refresh()
            except Exception:
                pass
        self.status_label.setText(self.engine.status_text())

    def _on_file_found(self, record: dict) -> None:
        if self.stack.currentWidget() in (self.files, self.dashboard, self.vpn):
            self.refresh_current()

    # ------------------------------------------------------------- lifecycle
    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh_current()

    def quit_application(self) -> None:
        self._quitting = True
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting or not self.settings.get("close_to_tray", True):
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "NetPulse is still recording",
            "Usage tracking continues in the background. "
            "Double-click the tray icon to reopen.",
            app_icon(), 4000)
