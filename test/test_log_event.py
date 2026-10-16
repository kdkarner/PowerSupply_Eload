"""Panel inferior Log Event y botón View Log."""
import re

import pytest
from PyQt6.QtWidgets import QDockWidget

import main
from hmi import LOG_MAX_LINES


@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def test_version_format_and_title(window):
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", main.SOFTWARE_VERSION)
    assert window.windowTitle().endswith(f"v{main.SOFTWARE_VERSION}")


def test_log_dock_hidden_by_default_and_bottom(window):
    assert window.log_dock.isHidden()
    assert not window.act_view_log.isChecked()
    assert window.dockWidgetArea(window.log_dock) == main.Qt.DockWidgetArea.BottomDockWidgetArea


def test_view_log_button_toggles_panel(window):
    window.act_view_log.trigger()
    assert not window.log_dock.isHidden() and window.act_view_log.isChecked()
    window.act_view_log.trigger()
    assert window.log_dock.isHidden() and not window.act_view_log.isChecked()


def test_close_button_hides_and_unchecks(window, qtbot):
    window.act_view_log.trigger()
    window.log_dock.close()  # equivalente al botón de cerrar del dock
    qtbot.wait(10)
    assert window.log_dock.isHidden()
    assert not window.act_view_log.isChecked()
    assert window.log_dock in window.findChildren(QDockWidget)  # se oculta, no se destruye


def test_same_look_as_psu_dock(window):
    psu_dock = window.docks[0][0]
    assert window.log_dock.features() == psu_dock.features()
    assert window.log_widget.txt_events.metaObject().className() == window.console_window.txt_console.metaObject().className()


def test_psu_messages_routed_with_source(window):
    ctrl = window.docks[0][1]
    ctrl.log("Error PyVISA: timeout")
    text = window.log_widget.txt_events.toPlainText()
    assert f"[{window.lang.get('psu_title')}{ctrl.psu_index}] Error PyVISA: timeout" in text
    assert "#FF1744".lower() in window.log_widget.txt_events.toHtml().lower()


def test_app_events_logged(window):
    text = window.log_widget.txt_events.toPlainText()
    assert main.SOFTWARE_VERSION in text
    window.add_psu()
    assert window.lang.get("evt_instr_added", name=f"{window.lang.get('psu_title')}2") in window.log_widget.txt_events.toPlainText()


def test_clear_button(window):
    window.log_widget.btn_clear.click()
    assert window.log_widget.txt_events.toPlainText() == ""


def test_log_is_bounded(window):
    for i in range(LOG_MAX_LINES + 50):
        window.log_event(f"linea {i}")
    assert window.log_widget.txt_events.document().blockCount() <= LOG_MAX_LINES


def test_log_visibility_persists_in_session(window, qtbot):
    window.act_view_log.trigger()
    window.save_app_session()
    window.config_mgr.sync()
    win2 = main.MultiPSUController()
    qtbot.addWidget(win2)
    assert not win2.log_dock.isHidden()
    assert win2.act_view_log.isChecked()


def test_log_translated(window):
    window.combo_lang.setCurrentIndex(window.combo_lang.findData("en"))
    assert window.act_view_log.text() == "Event Log"
    assert window.btn_add_eload.text() == window.lang.get("add_eload")
    assert window.log_widget.btn_clear.text() == "Clear"


def test_log_panel_does_not_squeeze_psus(window, qtbot):
    window.resize(1200, 780)
    window.add_psu()
    window.act_view_log.trigger()
    qtbot.wait(50)
    window.equalize_dock_sizes()
    qtbot.wait(50)
    assert window.log_dock.height() <= window.height() // 3 + 10
    for dock, _, _ in window.docks:
        assert dock.height() > window.log_dock.height()


def test_window_resize_goes_to_psus_not_log(window, qtbot):
    """Regresión: con el widget central de alto 0, al agrandar la ventana todo el alto iba al Log Event."""
    window.act_view_log.trigger()
    window.resize(1200, 700)
    qtbot.wait(30)
    psu_h, log_h = window.docks[0][0].height(), window.log_dock.height()
    window.resize(1200, 1000)
    qtbot.wait(30)
    assert window.docks[0][0].height() > psu_h + 200
    assert window.log_dock.height() < log_h + 100
