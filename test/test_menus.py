"""Menús Config / View / Help."""
import pytest
from PyQt6.QtWidgets import QMessageBox

import main


@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.show()
    yield win
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def test_menubar_has_three_menus(window):
    titles = [a.text() for a in window.menuBar().actions()]
    assert titles == [window.lang.get(k) for k in ("menu_config", "menu_view", "menu_help")]


def test_toolbar_has_no_view_toggles(window):
    """Consola SCPI y Event Log se controlan solo desde el menú Ver."""
    assert not hasattr(window, "btn_view_log") and not hasattr(window, "btn_view_console")


def test_view_console_action(window):
    assert not window.console_visible  # por defecto cerrada
    window.act_view_console.trigger()
    assert window.console_visible and window.act_view_console.isChecked()
    window.act_view_console.trigger()
    assert not window.console_visible and not window.console_window.isVisible()


def test_view_log_action_synced_with_dock(window):
    window.act_view_log.trigger()
    assert not window.log_dock.isHidden() and window.act_view_log.isChecked()
    window.log_dock.close()
    assert not window.act_view_log.isChecked()


def test_language_menu_changes_language(window):
    window.lang_actions["fr"].trigger()
    assert window.lang.current_lang == "fr"
    assert window.combo_lang.currentData() == "fr"
    assert window.menu_view.title() == "Affichage"
    window.combo_lang.setCurrentIndex(window.combo_lang.findData("en"))
    assert window.lang_actions["en"].isChecked()


def test_about_shows_version(window, monkeypatch):
    shown = {}
    monkeypatch.setattr(QMessageBox, "about", lambda parent, title, text: shown.update(title=title, text=text))
    window.act_about.trigger()
    assert main.SOFTWARE_VERSION in shown["text"]


def test_open_config_folder(window, monkeypatch, isolated_config):
    opened = []
    monkeypatch.setattr(main.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    window.act_open_config.trigger()
    assert opened == [str(isolated_config)]
