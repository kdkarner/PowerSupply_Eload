"""Carga electrónica Keysight EL34243A: driver y widget."""
import yaml
import pytest

import main
from eload import ElectronicLoad, LOAD_MODES
from conftest import FakeInstrument

PROFILE = "EL34243A_1"


@pytest.fixture
def load():
    ld = ElectronicLoad()
    ld.load_profile_settings(PROFILE)
    return ld


def _connect(ld, responses=None):
    ld.psu = FakeInstrument(responses)
    ld.connected = True
    return ld.psu


# ---------------- Driver ----------------

def test_profile_loaded_from_eloads_yaml(load, isolated_config):
    assert load.config_file == str(isolated_config / "eloads.yaml")
    assert set(load.channels) == {1, 2}
    assert load.mode == "CC" and load.mode_limit("CC") == 60.0 and load.mode_limit("CP") == 300.0


@pytest.mark.parametrize("mode,scpi,level", [("CC", "CURR", 2.5), ("CV", "VOLT", 12.0), ("CR", "RES", 50.0), ("CP", "POW", 100.0)])
def test_set_load_sends_mode_and_level_with_channel(load, mode, scpi, level):
    inst = _connect(load)
    load.select_channel(2)
    ok, msg = load.set_load(mode, level)
    assert ok, msg
    assert inst.writes[-2:] == [f"FUNC {scpi},(@2)", f"{scpi} {level:.4f},(@2)"]


def test_set_load_rejects_out_of_range_without_sending(load):
    inst = _connect(load)
    ok, msg = load.set_load("CC", 61.0)
    assert not ok and "rango" in msg
    ok, _ = load.set_load("CP", -1.0)
    assert not ok
    assert inst.writes == []


def test_set_load_requires_connection(load):
    assert load.set_load("CC", 1.0)[0] is False


def test_input_on_off_uses_channel(load):
    inst = _connect(load)
    load.select_channel(1)
    load.set_output(True)
    load.set_output(False)
    assert inst.writes[-2:] == ["INP ON,(@1)", "INP OFF,(@1)"]


def test_telemetry_reads_each_channel(load):
    _connect(load, {"MEAS:VOLT? (@1)": "12.0", "MEAS:CURR? (@1)": "2.0", "INP? (@1)": "1",
                    "MEAS:VOLT? (@2)": "5.0", "MEAS:CURR? (@2)": "1.0", "INP? (@2)": "0"})
    data = load.read_telemetry()
    assert data[1]["ReadV"] == 12.0 and data[1]["Power"] == 24.0 and data[1]["ON"] is True
    assert data[2]["ReadI"] == 1.0 and data[2]["ON"] is False


def test_mode_and_levels_persist(load, isolated_config):
    load.select_channel(2)
    load.store_load("CP", 150.0)
    load.store_load("CR", 75.0)
    disk = yaml.safe_load((isolated_config / "eloads.yaml").read_text(encoding="utf-8"))
    ch2 = disk["profiles"][PROFILE]["channels"][2]
    assert ch2["last_mode"] == "CR" and ch2["last_levels"]["CP"] == 150.0 and ch2["max_p"] == 300.0

    again = ElectronicLoad()
    again.load_profile_settings(PROFILE, channel=2)
    assert again.mode == "CR" and again.level("CP") == 150.0


def test_psu_config_untouched_by_eload(load, isolated_config):
    before = (isolated_config / "config.yaml").read_text(encoding="utf-8")
    load.store_load("CC", 3.0)
    assert (isolated_config / "config.yaml").read_text(encoding="utf-8") == before


# ---------------- Interfaz ----------------

@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.show()
    yield win
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def _add_eload(window):
    window.btn_add_eload.click()
    return window.docks[-1]


def test_add_eload_button_creates_eload_dock(window):
    dock, ctrl, idx = _add_eload(window)
    assert isinstance(ctrl, main.SingleELoadController)
    assert isinstance(ctrl.psu, ElectronicLoad)
    assert dock.objectName() == f"ELoadDock_{idx}"
    assert dock.windowTitle() == f"{window.lang.get('eload_title')}{idx}"
    assert isinstance(dock.widget(), main.ResponsiveScrollArea)  # mismo contenedor responsivo que la fuente


def test_eload_widget_same_layout_as_psu(window):
    _, ectrl, _ = _add_eload(window)
    pctrl = window.docks[0][1]
    for group in ("group_conn", "group_ctrl", "group_readout"):
        assert getattr(ectrl, group).metaObject().className() == getattr(pctrl, group).metaObject().className()
    assert ectrl.group_ctrl.title() == window.lang.get("group_ctrl_load")


def test_mode_change_updates_unit_and_limit(window):
    _, ctrl, _ = _add_eload(window)
    ctrl.combo_load_mode.setCurrentIndex(ctrl.combo_load_mode.findData("CP"))
    assert ctrl.spin_level.suffix() == " W" and ctrl.spin_level.maximum() == 300.0
    assert "(W)" in ctrl.lbl_level_tag.text()
    ctrl.combo_load_mode.setCurrentIndex(ctrl.combo_load_mode.findData("CR"))
    assert ctrl.spin_level.suffix() == " Ω"


def test_apply_level_disconnected_stores_value(window):
    _, ctrl, _ = _add_eload(window)
    ctrl.combo_load_mode.setCurrentIndex(ctrl.combo_load_mode.findData("CV"))
    ctrl.spin_level.setValue(24.0)
    ctrl.btn_apply_level.click()
    assert ctrl.psu.mode == "CV" and ctrl.psu.level("CV") == 24.0
    assert "(Guardado)" in window.console_window.txt_console.toPlainText()


def test_eload_input_button_texts(window):
    _, ctrl, _ = _add_eload(window)
    assert ctrl.btn_output.text() == window.lang.get("btn_enable_in")
    ctrl.update_output_button_ui(True)
    assert ctrl.btn_output.text() == window.lang.get("btn_disable_in")


def test_eload_modes_translated(window):
    _, ctrl, _ = _add_eload(window)
    window.lang_actions["en"].trigger()
    assert ctrl.combo_load_mode.itemText(0) == "CC - Constant Current"
    assert ctrl.group_ctrl.title() == "Load Settings"


def test_session_restores_eloads(window, qtbot):
    _add_eload(window)
    window.save_app_session()
    window.config_mgr.sync()
    win2 = main.MultiPSUController()
    qtbot.addWidget(win2)
    kinds = [c.KIND for _, c, _ in win2.docks]
    assert kinds == ["psu", "eload"]


def test_profile_conflict_only_within_same_kind(window):
    _, ectrl, _ = _add_eload(window)
    pctrl = window.docks[0][1]
    pctrl._conn_state = "connected"
    try:
        in_use, _ = window.is_resource_in_use(pctrl.combo_profile.currentData(), "10.9.9.9", ectrl)
        assert not in_use
        in_use, _ = window.is_resource_in_use("x", pctrl.txt_ip.text(), ectrl)
        assert in_use  # misma IP sí es conflicto entre tipos
    finally:
        pctrl._conn_state = "idle"


def test_eload_logs_to_event_log_with_name(window):
    _, ctrl, idx = _add_eload(window)
    ctrl.log("Error PyVISA: timeout")
    assert f"[{window.lang.get('eload_title')}{idx}] Error PyVISA: timeout" in window.log_widget.txt_events.toPlainText()


def test_all_load_modes_offered(window):
    _, ctrl, _ = _add_eload(window)
    assert [ctrl.combo_load_mode.itemData(i) for i in range(ctrl.combo_load_mode.count())] == list(LOAD_MODES)
