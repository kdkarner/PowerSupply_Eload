"""Pruebas de interfaz (pytest-qt, plataforma offscreen)."""
import pytest
from PyQt6.QtWidgets import QLabel, QScrollArea

import main


@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    yield win
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def _tag_labels(ctrl):
    return [w for w in ctrl.findChildren(QLabel) if w.text()]


def test_docks_are_wrapped_in_scroll_area(window):
    for dock, ctrl, _ in window.docks:
        assert isinstance(dock.widget(), QScrollArea)
        assert dock.widget().widget() is ctrl


def test_labels_never_compressed_in_small_window(window, qtbot):
    """Regresión: al reducir el espacio (otro monitor / otra PC) los labels se aplastaban."""
    while len(window.docks) < window.MAX_PSU_COUNT:
        window.add_psu()
    window.resize(640, 360)
    window.show()
    qtbot.waitExposed(window)
    window.equalize_dock_sizes()
    qtbot.wait(50)

    for _, ctrl, _ in window.docks:
        for lbl in _tag_labels(ctrl):
            hint = lbl.minimumSizeHint()
            assert lbl.width() >= hint.width(), f"'{lbl.text()}' comprimido: {lbl.width()} < {hint.width()}"


def test_output_button_styles_only_on_change(window):
    ctrl = window.docks[0][1]
    ctrl.update_output_button_ui(True)
    first = ctrl.btn_output.styleSheet()
    ctrl.btn_output.setStyleSheet("marker")
    ctrl.update_output_button_ui(True)  # mismo estado → no re-estiliza
    assert ctrl.btn_output.styleSheet() == "marker"
    ctrl.update_output_button_ui(False)
    assert ctrl.btn_output.styleSheet() != first and ctrl.output_is_on is False


def test_output_button_text_is_translated(window):
    window.combo_lang.setCurrentIndex(window.combo_lang.findData("en"))
    ctrl = window.docks[0][1]
    ctrl.update_output_button_ui(True)
    assert ctrl.btn_output.text() == window.lang.get("btn_disable_out")


def test_dock_layout_not_restored_on_different_screens(window, monkeypatch, qtbot):
    window.save_app_session()
    window.config_mgr.sync()
    monkeypatch.setattr(main, "screen_signature", lambda: "OTRO-MONITOR")
    restored = []
    monkeypatch.setattr(main.MultiPSUController, "restoreState", lambda self, s: restored.append(s))
    win2 = main.MultiPSUController()
    qtbot.addWidget(win2)
    assert restored == []


def test_signals_dispatch_without_errors(window, qtbot):
    """En módulos Cython PyQt no recorta los argumentos de la señal: todos los slots deben aceptarlos."""
    ctrl = window.docks[0][1]
    ctrl.btn_apply_v.click()
    ctrl.btn_apply_i.click()
    ctrl.btn_output.click()
    if ctrl.combo_profile.count() > 1:
        ctrl.combo_profile.setCurrentIndex(1)
    if ctrl.combo_channel.count() > 1:
        ctrl.combo_channel.setCurrentIndex(1)
    window.combo_lang.setCurrentIndex(window.combo_lang.findData("fr"))
    window.btn_add_psu.click()
    assert len(window.docks) == 2
    ctrl.resize(900, 500)
    qtbot.wait(20)


def test_dark_palette_independent_of_desktop_theme(qapp):
    """La app fija su propia paleta: sin ella, sin el tema GTK del escritorio la interfaz salía clara."""
    from PyQt6.QtGui import QPalette
    from colors import build_dark_palette, PALETTE_DARK
    pal = build_dark_palette()
    assert pal.color(QPalette.ColorRole.Window).name() == PALETTE_DARK[QPalette.ColorRole.Window].lower()
    assert pal.color(QPalette.ColorRole.WindowText).name() == PALETTE_DARK[QPalette.ColorRole.WindowText].lower()


def test_layout_levels_follow_available_width(window, qtbot):
    """Resize: lado a lado → apilado → angosto, y vuelta atrás al agrandar."""
    window.show()
    qtbot.waitExposed(window)
    ctrl = window.docks[0][1]
    sizes = [(1300, 0), (640, 1), (420, 2), (1300, 0)]
    for width, expected in sizes:
        window.resize(width, 900)
        qtbot.wait(30)
        assert ctrl.layout_level() == expected, f"ancho {width}: nivel {ctrl.layout_level()}"
        assert window.docks[0][0].widget().horizontalScrollBar().maximum() == 0


def test_left_column_matches_telemetry_height(window, qtbot):
    """En modo amplio, Conexión + Ajustes terminan a la misma altura que Telemetría."""
    window.resize(1400, 900)
    window.show()
    qtbot.waitExposed(window)
    qtbot.wait(50)
    ctrl = window.docks[0][1]
    assert ctrl.layout_level() == ctrl.LAYOUT_WIDE
    assert abs(ctrl.group_ctrl.geometry().bottom() - ctrl.group_readout.geometry().bottom()) <= 2
    assert ctrl.group_conn.geometry().top() == ctrl.group_readout.geometry().top()
