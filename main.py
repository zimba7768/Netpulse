"""NetPulse â€” a network usage monitor for Windows.

    python main.py            open the window
    python main.py --tray     start hidden in the notification area
    python main.py --reset-window
                              forget the saved window position

Run it elevated ("Run as administrator") to enable per-application tracking.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from netpulse.config import APP_NAME, APP_VERSION, Settings, data_dir, db_path
from netpulse.db import Database
from netpulse.engine import Engine
from netpulse.ui import theme
from netpulse.ui.assets import ensure_assets, write_ico
from netpulse.ui.main_window import MainWindow
from netpulse.ui.tray import app_icon

SINGLE_INSTANCE_KEY = "NetPulse.SingleInstance.v1"

#: Windows groups taskbar buttons â€” and picks their icon â€” by this string.
#: Without an explicit one the process inherits pythonw.exe's identity, which
#: is why an unconfigured PySide app shows the Python logo on the taskbar even
#: though its window icon is set correctly. This must be set before any window
#: exists.
APP_USER_MODEL_ID = "zimba7768.NetPulse.Monitor.1"


def claim_windows_identity() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
    except Exception:
        pass                                    # cosmetic only, never fatal


def already_running() -> bool:
    """True when another copy is live; also nudges it to show its window."""
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_KEY)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    return False


def main() -> int:
    claim_windows_identity()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.build_stylesheet(ensure_assets(data_dir())))
    # Application-wide default: covers the taskbar button, alt-tab and every
    # dialog, not just the main window.
    app.setWindowIcon(app_icon())
    # A real .ico on disk, for anyone making a shortcut or pinning it.
    icon_file = Path(__file__).resolve().parent / "netpulse.ico"
    written = write_ico(icon_file)
    if "--write-ico" in sys.argv:
        print(f"Wrote {written}" if written
              else f"Could not write {icon_file}")
        return 0 if written else 1

    if already_running():
        return 0

    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    server.listen(SINGLE_INSTANCE_KEY)

    settings = Settings()
    database = Database(db_path())
    engine = Engine(database, settings)

    window = MainWindow(database, engine, settings)

    def on_second_instance() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.readyRead.connect(connection.deleteLater)
        window.show_from_tray()

    server.newConnection.connect(on_second_instance)

    engine.start()

    start_hidden = "--tray" in sys.argv or settings.get("start_minimized", False)
    if start_hidden and QSystemTrayIcon.isSystemTrayAvailable():
        window.tray.showMessage(
            "NetPulse is recording",
            "Double-click this icon to open the dashboard.",
            QSystemTrayIcon.Information, 3500)
    else:
        window.show()

    app.aboutToQuit.connect(engine.stop)
    app.aboutToQuit.connect(database.close)
    try:
        return app.exec()
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main())
