"""Consola SCPI en ventana independiente, reparto de paneles al agregar y Event Log que agranda la ventana."""
import pytest
from PyQt6.QtWidgets import QTextEdit

import main


@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.resize(1000, 600)
    win.show()
    qtbot.waitExposed(win)
    yield win
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def _available(window):
    return window.screen().availableGeometry()


# ---------------- Consola SCPI ----------------

def test_panels_have_no_embedded_console(window):
    ctrl = window.docks[0][1]
    assert not hasattr(ctrl, "group_log") and not hasattr(ctrl, "txt_log")
    assert ctrl.findChildren(QTextEdit) == []


def test_console_is_independent_window(window):
    window.act_view_console.trigger()
    cw = window.console_window
    assert cw.isWindow() and cw.isVisible() and cw is not window
    assert cw.windowTitle() == window.lang.get("group_log")


def test_console_close_button_unchecks_and_persists(window, qtbot):
    window.act_view_console.trigger()
    window.console_window.close()
    assert not window.act_view_console.isChecked()
    assert window.config_mgr.get_console_visible() is False


def test_console_receives_messages_and_filters(window):
    window.add_eload()
    psu, eload = window.docks[0], window.docks[1]
    psu[1].log("VOLT 5.0")
    eload[1].log("INP ON,(@1)")
    cw = window.console_window
    text = cw.txt_console.toPlainText()
    assert f"[{window.instrument_name(psu[1])}] VOLT 5.0" in text and "INP ON,(@1)" in text
    cw.combo_filter.setCurrentIndex(cw.combo_filter.findData(eload[0].objectName()))
    text = cw.txt_console.toPlainText()
    assert "INP ON,(@1)" in text and "VOLT 5.0" not in text
    assert "VOLT 5.0" in window.log_widget.txt_events.toPlainText()  # el Event Log sigue recibiendo todo


def test_console_sources_follow_docks(window):
    window.add_psu()
    cw = window.console_window
    assert cw.combo_filter.count() == 1 + len(window.docks)
    window.docks[-1][0].close()
    assert cw.combo_filter.count() == 1 + len(window.docks)


def test_console_open_state_survives_restart(window, qtbot):
    window.act_view_console.trigger()
    window.close()  # cerrar la app no debe olvidar que la consola estaba abierta
    win2 = main.MultiPSUController()
    qtbot.addWidget(win2)
    qtbot.waitUntil(lambda: win2.console_window.isVisible(), timeout=1000)
    assert win2.act_view_console.isChecked()


# ---------------- Reparto de paneles ----------------

def test_adding_instruments_equalizes_widths(window, qtbot):
    window.add_psu()
    window.add_eload()
    qtbot.wait(50)
    widths = [d.width() for d, _, _ in window.docks]
    assert max(widths) - min(widths) <= 8, widths


def test_window_grows_when_panels_do_not_fit(window, qtbot):
    window.resize(500, 600)
    qtbot.wait(20)
    for _ in range(2):
        window.add_psu()
    qtbot.wait(50)
    needed = 3 * window.MIN_PANEL_WIDTH + 2 * 6
    assert window.width() >= min(needed, _available(window).width()) - 2


def test_closing_instrument_equalizes(window, qtbot):
    window.add_psu()
    window.add_psu()
    qtbot.wait(30)
    window.docks[1][0].close()
    qtbot.wait(50)
    widths = [d.width() for d, _, _ in window.docks]
    assert max(widths) - min(widths) <= 8, widths


# ---------------- Event Log en la parte inferior ----------------

def test_event_log_grows_window_instead_of_covering_panels(window, qtbot):
    window.resize(1000, 380)
    qtbot.wait(30)
    h0 = window.height()
    panel_h0 = window.docks[0][0].height()
    window.act_view_log.trigger()
    qtbot.wait(50)
    grown = window.height() - h0
    room = _available(window).height() - (window.frameGeometry().height() - window.height()) - h0
    assert grown == min(window.LOG_DEFAULT_HEIGHT + window.DOCK_SEPARATOR, room)
    if grown == window.LOG_DEFAULT_HEIGHT + window.DOCK_SEPARATOR:
        assert abs(window.docks[0][0].height() - panel_h0) <= 4  # los paneles no se achican
    assert window.log_dock.geometry().top() > window.docks[0][0].geometry().bottom()  # debajo de los paneles

    window.act_view_log.trigger()  # ocultar → la ventana vuelve a su alto
    qtbot.wait(50)
    assert abs(window.height() - h0) <= 4


def test_event_log_close_button_restores_height(window, qtbot):
    window.resize(1000, 380)
    qtbot.wait(30)
    h0 = window.height()
    window.act_view_log.trigger()
    qtbot.wait(50)
    window.log_dock.close()
    qtbot.wait(50)
    assert abs(window.height() - h0) <= 4
