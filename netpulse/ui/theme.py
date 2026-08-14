"""Dark theme: colour roles and the application stylesheet.

The series colours are the validated eight-slot categorical palette stepped for
a dark surface — every adjacent pair clears the colour-vision-deficiency and
normal-vision separation floors against ``SURFACE``, and all eight clear 3:1
contrast against it.  Download/upload are slots 1 and 2 (blue / orange), which
also read as a natural cool/warm pair.
"""
from __future__ import annotations

# --- surfaces & ink --------------------------------------------------------
PLANE = "#0d0d0d"           # window background
SURFACE = "#1a1a19"         # cards and chart surfaces
SURFACE_RAISED = "#222221"  # hover / header rows
SIDEBAR = "#131312"
TEXT = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"
BORDER_SOLID = "#2e2e2c"

# --- data series -----------------------------------------------------------
DOWN = "#3987e5"            # slot 1 — download
UP = "#d95926"              # slot 2 — upload
SERIES = [
    "#3987e5", "#d95926", "#199e70", "#c98500",
    "#d55181", "#008300", "#9085e9", "#e66767",
]
GOOD = "#0ca30c"
WARNING = "#fab219"
CRITICAL = "#d03b3b"

FONT_STACK = '"Segoe UI", system-ui, -apple-system, sans-serif'


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


BORDER = rgba(TEXT, 0.10)


def build_stylesheet(assets: dict[str, str] | None = None) -> str:
    """Compose the stylesheet, wiring in the generated images when present."""
    assets = assets or {}
    check = f"image: url({assets['check']});" if assets.get("check") else ""
    up_arrow = (f"image: url({assets['arrow_up']}); width: 8px; height: 8px;"
                if assets.get("arrow_up") else "")
    down_arrow = (f"image: url({assets['arrow_down']}); width: 8px; height: 8px;"
                  if assets.get("arrow_down") else "")

    return f"""
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {TEXT};
}}
QWidget#Root {{ background: {PLANE}; }}
QScrollArea {{ background: transparent; border: none; }}
QWidget#ScrollBody {{ background: transparent; }}

/* ---------------------------------------------------------------- sidebar */
QWidget#Sidebar {{
    background: {SIDEBAR};
    border-right: 1px solid {BORDER_SOLID};
}}
QLabel#BrandName {{ font-size: 17px; font-weight: 600; color: {TEXT}; }}
QLabel#BrandTag {{
    font-size: 10.5px; color: {MUTED}; letter-spacing: 0.7px;
}}
QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: left;
    font-size: 13.5px;
    color: {TEXT_SECONDARY};
}}
QPushButton#NavButton:hover {{ background: {SURFACE_RAISED}; color: {TEXT}; }}
QPushButton#NavButton:checked {{
    background: {rgba(DOWN, 0.16)};
    color: {TEXT};
    font-weight: 600;
}}

/* ------------------------------------------------------------------ cards */
QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    border-radius: 12px;
}}
QFrame#Banner {{
    background: {rgba(WARNING, 0.10)};
    border: 1px solid {rgba(WARNING, 0.35)};
    border-radius: 10px;
}}
QLabel#CardTitle {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
QLabel#CardHint  {{ font-size: 11.5px; color: {MUTED}; }}
QLabel#TileLabel {{
    font-size: 11px; color: {MUTED}; letter-spacing: 0.7px;
}}
QLabel#TileValue {{ font-size: 26px; font-weight: 600; color: {TEXT}; }}
QLabel#TileUnit  {{ font-size: 13px; color: {TEXT_SECONDARY}; }}
QFrame#Chip {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    border-radius: 16px;
}}
QFrame#Chip:hover {{ border-color: {rgba(DOWN, 0.55)}; }}
QLabel#ChipLabel {{
    font-size: 10px; color: {MUTED}; letter-spacing: 0.8px;
}}
QLabel#ChipValue {{
    font-size: 13px; font-weight: 600; color: {TEXT};
}}
QLabel#PageTitle {{ font-size: 22px; font-weight: 600; }}
QLabel#PageHint  {{ font-size: 12.5px; color: {MUTED}; }}
QLabel#Status    {{ font-size: 11.5px; color: {MUTED}; }}

/* --------------------------------------------------------------- controls */
QPushButton#Pill {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    border-radius: 15px;
    padding: 6px 15px;
    color: {TEXT_SECONDARY};
    font-size: 12.5px;
}}
QPushButton#Pill:hover {{ background: {SURFACE_RAISED}; color: {TEXT}; }}
/* No weight change on :checked — the button keeps its unchecked width, so a
   bolder label would be clipped. State reads from the fill and border. */
QPushButton#Pill:checked {{
    background: {rgba(DOWN, 0.22)};
    border-color: {rgba(DOWN, 0.65)};
    color: {TEXT};
}}
QPushButton {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_SOLID};
    border-radius: 8px;
    padding: 8px 16px;
    color: {TEXT};
}}
QPushButton:hover {{ background: #2b2b29; }}
QPushButton:pressed {{ background: #333331; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#Primary {{
    background: {DOWN}; border: none; color: #ffffff; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: #4d95ea; }}
QPushButton#Danger {{ color: {CRITICAL}; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {rgba(DOWN, 0.45)};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {rgba(DOWN, 0.7)};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ {down_arrow} }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    selection-background-color: {rgba(DOWN, 0.30)};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    background: transparent;
    border: none;
    width: 18px;
}}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {SURFACE_RAISED};
}}
QSpinBox::up-arrow {{ {up_arrow} }}
QSpinBox::down-arrow {{ {down_arrow} }}

QCheckBox {{ spacing: 9px; color: {TEXT_SECONDARY}; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid #46463f; border-radius: 5px; background: {SURFACE};
}}
QCheckBox::indicator:hover {{ border-color: {rgba(DOWN, 0.7)}; }}
QCheckBox::indicator:checked {{
    background: {DOWN}; border-color: {DOWN}; {check}
}}

/* ----------------------------------------------------------------- tables */
QTableWidget, QTableView {{
    background: {SURFACE};
    alternate-background-color: #1e1e1c;
    border: none;
    gridline-color: transparent;
    selection-background-color: {rgba(DOWN, 0.22)};
    selection-color: {TEXT};
    outline: none;
}}
QHeaderView {{ background: {SURFACE}; }}
QHeaderView::section {{
    background: {SURFACE};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER_SOLID};
    padding: 9px 10px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}
QTableWidget::item {{ padding: 8px 10px; border-bottom: 1px solid #232321; }}
QTableWidget::item:selected {{ color: {TEXT}; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QListWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {rgba(DOWN, 0.22)}; }}

/* -------------------------------------------------------------- scrollbar */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #3a3a37; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a4a46; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: #3a3a37; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: #232321;
    color: {TEXT};
    border: 1px solid {BORDER_SOLID};
    border-radius: 6px;
    padding: 6px 9px;
}}
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOLID};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {rgba(DOWN, 0.25)}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOLID}; margin: 5px 8px; }}
QMessageBox {{ background: {SURFACE}; }}
"""


STYLESHEET = build_stylesheet()
