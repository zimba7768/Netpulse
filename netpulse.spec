# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition — produces a single self-contained NetPulse.exe.

Used by both build-exe.bat (local builds) and the release workflow, so the two
can never drift apart.

    pyinstaller netpulse.spec
"""
from PyInstaller.utils.hooks import collect_submodules


def _optional_submodules(package: str) -> list[str]:
    """Collect a package's submodules, tolerating it being absent."""
    try:
        return collect_submodules(package)
    except Exception:
        return []


# watchdog picks its observer at runtime and pywintrace is imported lazily
# inside a function, so neither is visible to static analysis.
hidden_imports = (
    _optional_submodules("watchdog.observers")
    + _optional_submodules("etw")
)

# Qt ships far more than this application uses. Dropping the heavyweights keeps
# the executable to a sane size.
excludes = [
    "tkinter", "unittest", "pydoc", "doctest",
    "numpy", "pandas", "matplotlib", "scipy", "PIL",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtNfc",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtSql", "PySide6.QtSvgWidgets", "PySide6.QtUiTools",
]

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # Ship the icon so shortcuts and the tray have it without a first run.
    datas=[("netpulse.ico", ".")],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="NetPulse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX-packed binaries trip antivirus heuristics
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # no console window; it is a GUI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="netpulse.ico",
)
