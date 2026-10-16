"""Temas Dark / Light / High Contrast (Ver → Temas) y su configuración en config/themes.yaml."""
import yaml
import pytest
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

import colors
import main


@pytest.fixture(autouse=True)
def reset_theme():
    yield
    colors.set_theme("dark")
    app = QApplication.instance()
    if app:
        app.setPalette(colors.palette("dark"))


@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.show()
    yield win
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def _yaml(isolated_config):
    return yaml.safe_load((isolated_config / "themes.yaml").read_text(encoding="utf-8"))


def test_themes_menu_has_three_themes_dark_default(window):
    names = [a.data() for a in window.menu_theme.actions()]
    assert names == ["dark", "light", "high_contrast"]
    assert window.theme_actions["dark"].isChecked()
    assert [a.text() for a in window.menu_theme.actions()] == ["Dark Theme", "Light Theme", "High Contrast"]


def test_dark_theme_is_current_look():
    """El tema Dark conserva exactamente el aspecto anterior."""
    assert colors.color("bg_main", "dark") == "#121216"
    assert colors.color("accent", "dark") == "#00E5FF"
    assert "#00FF66" in colors.stylesheet("dark")


@pytest.mark.parametrize("name", ["light", "high_contrast"])
def test_switching_theme_applies_everywhere(window, name):
    window.theme_actions[name].trigger()
    assert colors.active_theme() == name
    assert colors.color("bg_main") in window.styleSheet()
    assert window.console_window.styleSheet() == window.styleSheet()
    app_window = QApplication.instance().palette().color(QPalette.ColorRole.Window).name()
    assert app_window == colors.palette(name).color(QPalette.ColorRole.Window).name()
    ctrl = window.docks[0][1]
    assert colors.color("output_off_bg") in ctrl.btn_output.styleSheet()
    assert colors.color("status_disconnected") in ctrl.lbl_status.styleSheet()
    assert ctrl.plot_widget.backgroundBrush().color().name().upper() == colors.color("plot_bg").upper()


def test_theme_saved_in_yaml_and_restored(window, qtbot, isolated_config):
    window.theme_actions["light"].trigger()
    assert _yaml(isolated_config)["active_theme"] == "light"
    win2 = main.MultiPSUController()
    qtbot.addWidget(win2)
    assert colors.active_theme() == "light" and win2.theme_actions["light"].isChecked()


def test_colors_editable_in_yaml(isolated_config, qtbot):
    data = _yaml(isolated_config)
    data["themes"]["dark"]["colors"]["accent"] = "#123456"
    (isolated_config / "themes.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    assert "#123456" in win.styleSheet()


def test_incomplete_or_broken_yaml_falls_back_to_defaults(isolated_config):
    (isolated_config / "themes.yaml").write_text("active_theme: inexistente\nthemes:\n  dark:\n    colors:\n      bg_main: '#000001'\n", encoding="utf-8")
    assert colors.load_themes() == "dark"
    assert colors.color("bg_main") == "#000001" and colors.color("accent") == "#00E5FF"
    (isolated_config / "themes.yaml").write_text(": : no es yaml [", encoding="utf-8")
    assert colors.load_themes() == "dark"


def test_saving_theme_keeps_user_colors(isolated_config):
    colors.load_themes()
    data = _yaml(isolated_config)
    data["themes"]["light"]["colors"]["accent"] = "#ABCDEF"
    (isolated_config / "themes.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    colors.save_active_theme("high_contrast")
    data = _yaml(isolated_config)
    assert data["active_theme"] == "high_contrast" and data["themes"]["light"]["colors"]["accent"] == "#ABCDEF"


def test_eload_and_log_follow_theme(window):
    window.add_eload()
    window.theme_actions["high_contrast"].trigger()
    ctrl = window.docks[-1][1]
    assert colors.color("output_off_bg") in ctrl.btn_output.styleSheet()
    window.log_event("Error: prueba")
    assert colors.color("log_error").lower() in window.log_widget.txt_events.toHtml().lower()


def test_theme_names_translated(window):
    window.lang_actions["fr"].trigger()
    assert window.menu_theme.title() == "Thèmes"
    assert window.theme_actions["light"].text() == "Thème clair"


def test_yaml_header_survives_theme_change(window, isolated_config):
    window.theme_actions["light"].trigger()
    assert (isolated_config / "themes.yaml").read_text(encoding="utf-8").startswith("# Temas de la aplicación")


def test_reselecting_theme_picks_up_yaml_edits(window, isolated_config):
    data = _yaml(isolated_config)
    data["themes"]["light"]["colors"]["bg_main"] = "#FAFAF0"
    (isolated_config / "themes.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    window.theme_actions["light"].trigger()
    assert "#FAFAF0" in window.styleSheet()
