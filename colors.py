"""
colors.py - Temas de la aplicación (Dark / Light / High Contrast) para PyQt6.

Los colores de cada tema y el tema activo se guardan en config/themes.yaml (editable). Los valores de este
archivo se fusionan sobre los valores por defecto definidos aquí, así que un YAML incompleto o con claves
de más nunca rompe la interfaz. Toda la app (QSS, paleta Qt, gráficas, iconos, estados) toma sus colores
del tema activo mediante color(), stylesheet() y palette().
"""

import copy
import os

import yaml
from PyQt6.QtGui import QPalette, QColor

from paths import config_path

THEME_NAMES = ("dark", "light", "high_contrast")
DEFAULT_THEME = "dark"

DEFAULT_THEMES = {
    "dark": {
        "colors": {
            "bg_main": "#121216", "bg_panel": "#1E1E24", "bg_input": "#2A2A35", "border": "#3A3A45",
            "text_primary": "#FFFFFF", "text_secondary": "#A0A0B0", "accent": "#00E5FF",
            "voltage": "#00E5FF", "current": "#FFD54F", "power": "#FF4081",
            "button_bg": "#007ACC", "button_hover": "#0098FF", "button_pressed": "#005999",
            "button_text": "#FFFFFF", "button_border": "#007ACC",
            "console_text": "#00FF66", "log_info": "#00FF66", "log_warning": "#FF9800", "log_error": "#FF1744",
            "status_connected": "#00E676", "status_disconnected": "#FF1744", "status_error": "#FF9800",
            "output_on_bg": "#2E7D32", "output_on_border": "#1B5E20", "output_on_hover": "#388E3C",
            "output_off_bg": "#C62828", "output_off_border": "#B71C1C", "output_off_hover": "#D32F2F",
            "output_text": "#FFFFFF",
            "rec": "#FF1744", "icon": "#E0E0E8", "icon_disabled": "#55555F",
            "plot_bg": "#1E1E1E", "plot_axis": "#A0A0B0", "plot_voltage": "#00E676", "plot_current": "#FF9800",
        },
        # Equivalente a Breeze-Dark: la app no depende del tema GTK/KDE del escritorio
        "palette": {
            "window": "#1F2124", "window_text": "#FCFCFC", "base": "#1F2124", "alternate_base": "#1D1F22",
            "text": "#FCFCFC", "button": "#1F2124", "button_text": "#FCFCFC", "bright_text": "#FFFFFF",
            "highlight": "#3DAEE9", "highlighted_text": "#FCFCFC", "tooltip_base": "#FFFFDC",
            "tooltip_text": "#000000", "placeholder_text": "#989898", "light": "#27292D", "midlight": "#25272B",
            "mid": "#232529", "dark": "#1B1D20", "shadow": "#020202", "link": "#308CC6",
            "disabled_text": "#8D8E90",
        },
    },
    "light": {
        "colors": {
            "bg_main": "#F4F5F7", "bg_panel": "#FFFFFF", "bg_input": "#FFFFFF", "border": "#C3C8D0",
            "text_primary": "#1E1E24", "text_secondary": "#4A4F5A", "accent": "#00639E",
            "voltage": "#0277BD", "current": "#A15C00", "power": "#C2185B",
            "button_bg": "#007ACC", "button_hover": "#1A8CDB", "button_pressed": "#005999",
            "button_text": "#FFFFFF", "button_border": "#007ACC",
            "console_text": "#1B6E33", "log_info": "#1B6E33", "log_warning": "#A34E00", "log_error": "#C62828",
            "status_connected": "#1B7F3A", "status_disconnected": "#C62828", "status_error": "#A34E00",
            "output_on_bg": "#2E7D32", "output_on_border": "#1B5E20", "output_on_hover": "#388E3C",
            "output_off_bg": "#C62828", "output_off_border": "#B71C1C", "output_off_hover": "#D32F2F",
            "output_text": "#FFFFFF",
            "rec": "#D50000", "icon": "#2A2A35", "icon_disabled": "#B0B4BC",
            "plot_bg": "#FFFFFF", "plot_axis": "#4A4F5A", "plot_voltage": "#2E7D32", "plot_current": "#E65100",
        },
        "palette": {
            "window": "#F4F5F7", "window_text": "#1E1E24", "base": "#FFFFFF", "alternate_base": "#EEF0F3",
            "text": "#1E1E24", "button": "#E8EAEE", "button_text": "#1E1E24", "bright_text": "#000000",
            "highlight": "#007ACC", "highlighted_text": "#FFFFFF", "tooltip_base": "#FFFFDC",
            "tooltip_text": "#000000", "placeholder_text": "#8A8F99", "light": "#FFFFFF", "midlight": "#E8EAEE",
            "mid": "#C3C8D0", "dark": "#A0A4AC", "shadow": "#6B6F77", "link": "#00639E",
            "disabled_text": "#9AA0AA",
        },
    },
    "high_contrast": {
        "colors": {
            "bg_main": "#000000", "bg_panel": "#000000", "bg_input": "#000000", "border": "#FFFFFF",
            "text_primary": "#FFFFFF", "text_secondary": "#FFFFFF", "accent": "#FFFF00",
            "voltage": "#00FFFF", "current": "#FFFF00", "power": "#FF66FF",
            "button_bg": "#0000B0", "button_hover": "#0000FF", "button_pressed": "#000070",
            "button_text": "#FFFFFF", "button_border": "#FFFFFF",
            "console_text": "#00FF00", "log_info": "#00FF00", "log_warning": "#FFFF00", "log_error": "#FF5050",
            "status_connected": "#00FF00", "status_disconnected": "#FF5050", "status_error": "#FFFF00",
            "output_on_bg": "#00A000", "output_on_border": "#FFFFFF", "output_on_hover": "#00C000",
            "output_off_bg": "#B00000", "output_off_border": "#FFFFFF", "output_off_hover": "#D00000",
            "output_text": "#FFFFFF",
            "rec": "#FF3030", "icon": "#FFFFFF", "icon_disabled": "#707070",
            "plot_bg": "#000000", "plot_axis": "#FFFFFF", "plot_voltage": "#00FF00", "plot_current": "#FFFF00",
        },
        "palette": {
            "window": "#000000", "window_text": "#FFFFFF", "base": "#000000", "alternate_base": "#101010",
            "text": "#FFFFFF", "button": "#000000", "button_text": "#FFFFFF", "bright_text": "#FFFF00",
            "highlight": "#FFFF00", "highlighted_text": "#000000", "tooltip_base": "#000000",
            "tooltip_text": "#FFFFFF", "placeholder_text": "#C0C0C0", "light": "#FFFFFF", "midlight": "#C0C0C0",
            "mid": "#808080", "dark": "#404040", "shadow": "#000000", "link": "#00FFFF",
            "disabled_text": "#A0A0A0",
        },
    },
}

_PALETTE_ROLES = {
    "window": QPalette.ColorRole.Window, "window_text": QPalette.ColorRole.WindowText,
    "base": QPalette.ColorRole.Base, "alternate_base": QPalette.ColorRole.AlternateBase,
    "text": QPalette.ColorRole.Text, "button": QPalette.ColorRole.Button,
    "button_text": QPalette.ColorRole.ButtonText, "bright_text": QPalette.ColorRole.BrightText,
    "highlight": QPalette.ColorRole.Highlight, "highlighted_text": QPalette.ColorRole.HighlightedText,
    "tooltip_base": QPalette.ColorRole.ToolTipBase, "tooltip_text": QPalette.ColorRole.ToolTipText,
    "placeholder_text": QPalette.ColorRole.PlaceholderText, "light": QPalette.ColorRole.Light,
    "midlight": QPalette.ColorRole.Midlight, "mid": QPalette.ColorRole.Mid, "dark": QPalette.ColorRole.Dark,
    "shadow": QPalette.ColorRole.Shadow, "link": QPalette.ColorRole.Link,
}

_themes = copy.deepcopy(DEFAULT_THEMES)
_active = DEFAULT_THEME


# ----------------------------------------------------------------------
# PERSISTENCIA (config/themes.yaml)
# ----------------------------------------------------------------------
def themes_file() -> str:
    return config_path("themes.yaml")


def _read_yaml(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}


YAML_HEADER = (
    "# Temas de la aplicación (Ver → Temas). Editable: los colores se aplican al reiniciar o al volver a elegir el tema.\n"
    "# active_theme: dark | light | high_contrast  (lo guarda la app al cambiar de tema)\n"
    "# Las claves que falten toman el valor por defecto; colores en formato #RRGGBB.\n"
)


def _write_yaml(path: str, data: dict):
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(YAML_HEADER)
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def load_themes(path: str = None) -> str:
    """Carga colores y tema activo desde el YAML (lo crea con los valores por defecto si no existe)."""
    global _themes, _active
    path = path or themes_file()
    if not os.path.exists(path):
        _write_yaml(path, {"active_theme": DEFAULT_THEME, "themes": DEFAULT_THEMES})

    data = _read_yaml(path)
    merged = copy.deepcopy(DEFAULT_THEMES)
    for name, theme in (data.get("themes") or {}).items():
        if name not in merged or not isinstance(theme, dict):
            continue
        for section in ("colors", "palette"):
            values = theme.get(section) or {}
            merged[name][section].update({k: str(v) for k, v in values.items() if k in merged[name][section]})
    _themes = merged

    active = data.get("active_theme", DEFAULT_THEME)
    _active = active if active in THEME_NAMES else DEFAULT_THEME
    return _active


def save_active_theme(name: str, path: str = None):
    """Guarda el tema elegido conservando los colores que el usuario haya editado en el YAML."""
    path = path or themes_file()
    data = _read_yaml(path) or {"themes": DEFAULT_THEMES}
    data["active_theme"] = name
    data.setdefault("themes", DEFAULT_THEMES)
    _write_yaml(path, data)


# ----------------------------------------------------------------------
# TEMA ACTIVO
# ----------------------------------------------------------------------
def set_theme(name: str):
    global _active
    if name not in THEME_NAMES:
        raise ValueError(f"Tema desconocido: {name}")
    _active = name


def active_theme() -> str:
    return _active


def color(key: str, theme: str = None) -> str:
    return _themes[theme or _active]["colors"][key]


def palette(theme: str = None) -> QPalette:
    values = _themes[theme or _active]["palette"]
    pal = QPalette()
    for key, role in _PALETTE_ROLES.items():
        pal.setColor(role, QColor(values[key]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(values["disabled_text"]))
    return pal


def output_button_style(is_on: bool, theme: str = None) -> str:
    c = _themes[theme or _active]["colors"]
    state = "on" if is_on else "off"
    return f"""
QPushButton {{
    background-color: {c[f'output_{state}_bg']};
    color: {c['output_text']};
    font-weight: bold;
    border: 1px solid {c[f'output_{state}_border']};
    border-radius: 4px;
    padding: 6px;
}}
QPushButton:hover {{
    background-color: {c[f'output_{state}_hover']};
}}
"""


def status_style(state: str, theme: str = None) -> str:
    """state: connected / disconnected / error"""
    return f"color: {color(f'status_{state}', theme)}; font-weight: bold; font-size: 10px;"


def stylesheet(theme: str = None) -> str:
    c = _themes[theme or _active]["colors"]
    return f"""
QMainWindow {{
    background-color: {c['bg_main']};
    color: {c['text_primary']};
}}

QGroupBox {{
    font-weight: bold;
    font-size: 13px;
    border: 1px solid {c['border']};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 12px;
    color: {c['accent']};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}

QLabel {{
    color: {c['text_secondary']};
    font-size: 12px;
    font-weight: 600;
}}

QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {c['bg_input']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 6px;
    color: {c['text_primary']};
    font-size: 13px;
}}

QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border: 1px solid {c['accent']};
}}

QPushButton {{
    background-color: {c['button_bg']};
    color: {c['button_text']};
    border: 1px solid {c['button_border']};
    border-radius: 4px;
    padding: 8px 14px;
    font-weight: bold;
    font-size: 12px;
}}

QPushButton:hover {{
    background-color: {c['button_hover']};
}}

QPushButton:pressed {{
    background-color: {c['button_pressed']};
}}

QPushButton:checked {{
    background-color: {c['button_pressed']};
    border: 1px solid {c['accent']};
}}

#lcd_voltage {{
    font-size: 32px;
    font-weight: bold;
    color: {c['voltage']};
    background-color: {c['bg_main']};
    border: 2px solid {c['voltage']};
    border-radius: 8px;
    padding: 12px;
}}

#lcd_current {{
    font-size: 32px;
    font-weight: bold;
    color: {c['current']};
    background-color: {c['bg_main']};
    border: 2px solid {c['current']};
    border-radius: 8px;
    padding: 12px;
}}

#lcd_power {{
    font-size: 32px;
    font-weight: bold;
    color: {c['power']};
    background-color: {c['bg_main']};
    border: 2px solid {c['power']};
    border-radius: 8px;
    padding: 12px;
}}

QToolBar QToolButton {{
    background-color: {c['bg_input']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 6px 8px;
    margin: 0 1px;
    font-weight: bold;
    font-size: 12px;
}}

QToolBar QToolButton:hover {{
    border: 1px solid {c['accent']};
}}

QToolBar QToolButton:checked, QToolBar QToolButton:pressed {{
    background-color: {c['button_pressed']};
}}

QToolBar QToolButton:disabled {{
    background-color: {c['bg_panel']};
    color: {c['text_secondary']};
}}

QToolBar::separator {{
    width: 2px;
    margin: 6px 8px;
    border-left: 2px dotted {c['text_secondary']};
}}

#lbl_rec_status {{
    color: {c['rec']};
    font-weight: bold;
    padding: 0 8px;
}}

QMenuBar {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    border-bottom: 1px solid {c['border']};
    font-size: 12px;
}}

QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}

QMenuBar::item:selected, QMenu::item:selected {{
    background-color: {c['button_bg']};
    color: {c['button_text']};
}}

QMenu {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    font-size: 12px;
}}

QMenu::item {{
    padding: 5px 24px 5px 24px;
}}

QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 4px 8px;
}}

QTextEdit {{
    background-color: {c['bg_main']};
    color: {c['console_text']};
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    border: 1px solid {c['border']};
    border-radius: 4px;
}}
"""


# ----------------------------------------------------------------------
# COMPATIBILIDAD: constantes del tema oscuro por defecto
# ----------------------------------------------------------------------
STYLESHEET_QSS = stylesheet(DEFAULT_THEME)
STYLE_BTN_OUTPUT_ON = output_button_style(True, DEFAULT_THEME)
STYLE_BTN_OUTPUT_OFF = output_button_style(False, DEFAULT_THEME)
PALETTE_DARK = {role: DEFAULT_THEMES["dark"]["palette"][key] for key, role in _PALETTE_ROLES.items()}


def build_dark_palette() -> QPalette:
    return palette(DEFAULT_THEME)
