"""
Ciclo de conexión en el hilo del instrumento y secuencia segura al conectar.
Reproduce el cuelgue original (desconectar con una consulta en curso) y verifica que ya no ocurre.
"""
import threading
import time

import pytest
from PyQt6.QtCore import QThread

import main
from powsupply import PowerSupply, InstrumentBusyError
from eload import ElectronicLoad


class FakeDevice:
    """Instrumento SCPI simulado: IDN, salidas (arranca ENCENDIDA), setpoints y demoras interrumpibles."""

    def __init__(self, idn="Keysight Technologies,E36155A,MY0001,1.0", volt=7.5, curr=1.25,
                 outputs_stay_on=False, fail=(), mode="POW", level=150.0):
        self.idn, self.volt, self.curr = idn, volt, curr
        self.outputs_stay_on, self.fail = outputs_stay_on, set(fail)
        self.mode, self.level = mode, level
        self.on = True
        self.delay = 0.0
        self.release = threading.Event()  # libera las esperas al final de la prueba
        self.log = []                     # (hilo, comando)
        self.closed = False
        self.timeout = None

    def _record(self, cmd):
        self.log.append((threading.current_thread().name, cmd))

    def write(self, cmd):
        self._record(cmd)
        up = cmd.upper()
        if not up.startswith(("OUT", "INP")):
            return
        if ("OFF" in up or up.endswith(" 0")) and not self.outputs_stay_on:
            self.on = False
        elif "ON" in up.split(",")[0].split() or up.endswith(" 1"):
            self.on = True

    def query(self, cmd):
        self._record(cmd)
        if self.delay:
            self.release.wait(self.delay)
        up = cmd.upper()
        if any(f in up for f in self.fail):
            raise IOError(f"timeout en {cmd}")
        if up == "*IDN?":
            return self.idn
        if up.startswith("MEAS"):
            return "1.0"
        if up.startswith("FUNC?"):
            return self.mode
        if up.startswith(("OUT", "INP")):
            return "1" if self.on else "0"
        if up.startswith("VOLT?"):
            return str(self.volt)
        if up.startswith("CURR?"):
            return str(self.curr)
        if up.startswith("POW?"):
            return str(self.level)
        return "0"

    def close(self):
        self.closed = True
        self.release.set()

    def commands(self):
        return [c for _, c in self.log]


class FakeRM:
    def __init__(self, device):
        self.device = device

    def open_resource(self, name):
        return self.device


@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.show()
    devices = []
    win._devices = devices
    yield win
    for d in devices:
        d.release.set()
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()
    for _, ctrl, _ in win.docks:
        assert ctrl.wait_for_shutdown(5000)


def _prepare(window, ctrl, device, profile):
    window._devices.append(device)
    ctrl.combo_profile.setCurrentIndex(ctrl.combo_profile.findData(profile))
    ctrl.psu.rm = FakeRM(device)
    return ctrl


def _connect(qtbot, ctrl):
    ctrl.btn_connect.click()
    qtbot.waitUntil(lambda: ctrl._conn_state in ("connected", "idle"), timeout=5000)


# ---------------- Secuencia segura ----------------

def test_safe_connect_sequence_runs_in_instrument_thread(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(), "E36155A_1")
    dev = ctrl.psu.rm.device
    _connect(qtbot, ctrl)
    assert ctrl.is_online()
    cmds = dev.commands()
    assert cmds.index("*IDN?") < cmds.index("OUTP OFF") < cmds.index("VOLT?")
    assert not dev.on                                      # la salida quedó apagada y verificada
    assert ctrl.spin_v.value() == 7.5 and ctrl.spin_i.value() == 1.25   # setpoints reales del equipo
    assert all(name != threading.main_thread().name for name, _ in dev.log)  # nada de I/O en la interfaz
    text = window.log_widget.txt_events.toPlainText()
    assert "IDN verificado" in text and "Setpoints leídos del equipo" in text


def test_wrong_instrument_is_rejected(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(idn="Rigol,DP832,X,1"), "E36155A_1")
    _connect(qtbot, ctrl)
    qtbot.waitUntil(lambda: ctrl._conn_state == "idle", timeout=5000)
    assert not ctrl.is_online() and ctrl.psu.rm.device.closed
    assert "Equipo incorrecto" in window.log_widget.txt_events.toPlainText()
    assert "OUTP OFF" not in ctrl.psu.rm.device.commands()


def test_output_that_does_not_turn_off_blocks_connection(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(outputs_stay_on=True), "E36155A_1")
    _connect(qtbot, ctrl)
    qtbot.waitUntil(lambda: ctrl._conn_state == "idle", timeout=5000)
    assert not ctrl.is_online()
    assert "siguen encendidas" in window.log_widget.txt_events.toPlainText()


def test_unreadable_setpoints_connect_with_warning(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(fail=("VOLT?",)), "E36155A_1")
    saved_v = ctrl.spin_v.value()
    _connect(qtbot, ctrl)
    assert ctrl.is_online() and ctrl.spin_v.value() == saved_v
    assert "No se pudieron leer los setpoints" in window.log_widget.txt_events.toPlainText()


def test_all_channels_turned_off_on_multichannel(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(idn="BK Precision,9130"), "BK_PRECISION_1")
    _connect(qtbot, ctrl)
    cmds = ctrl.psu.rm.device.commands()
    assert ctrl.is_online()
    for ch in ctrl.psu.channels:
        assert f"INST:NSEL {ch}" in cmds
    assert cmds.count("OUTP:STAT 0") == len(ctrl.psu.channels)
    assert "identidad no verificada" in window.log_widget.txt_events.toPlainText()


def test_eload_reads_mode_and_level_on_connect(window, qtbot):
    window.add_eload()
    ctrl = _prepare(window, window.docks[-1][1], FakeDevice(idn="Keysight,EL34243A,1,1"), "EL34243A_1")
    _connect(qtbot, ctrl)
    assert ctrl.is_online()
    assert ctrl.current_load_mode() == "CP" and ctrl.spin_level.value() == 150.0
    assert "INP OFF,(@1)" in ctrl.psu.rm.device.commands() and "INP OFF,(@2)" in ctrl.psu.rm.device.commands()


# ---------------- Cuelgue original ----------------

def test_disconnect_during_slow_query_never_blocks_ui(window, qtbot, monkeypatch):
    """Antes: wait(1000) + terminate() con el lock tomado → la interfaz quedaba colgada para siempre."""
    monkeypatch.setattr(QThread, "terminate", lambda self: pytest.fail("no se debe usar terminate()"))
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(), "E36155A_1")
    dev = ctrl.psu.rm.device
    _connect(qtbot, ctrl)
    dev.delay = 3.0                                    # el equipo tarda en responder (red/instrumento lento)
    qtbot.waitUntil(lambda: dev.log and dev.log[-1][1].startswith("MEAS"), timeout=3000)

    t0 = time.monotonic()
    ctrl.disconnect_psu()
    ctrl.btn_output.click()                             # la interfaz sigue respondiendo
    assert time.monotonic() - t0 < 0.2
    assert ctrl._conn_state == "disconnecting" and not ctrl.btn_connect.isEnabled()

    qtbot.waitUntil(lambda: ctrl._conn_state == "idle", timeout=6000)
    assert dev.closed and ctrl.btn_connect.isEnabled() and ctrl.combo_profile.isEnabled()


def test_ui_commands_do_not_wait_for_instrument(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(), "E36155A_1")
    dev = ctrl.psu.rm.device
    _connect(qtbot, ctrl)
    dev.delay = 1.0
    qtbot.waitUntil(lambda: dev.log[-1][1].startswith("MEAS"), timeout=3000)
    t0 = time.monotonic()
    ctrl.btn_apply_v.click()
    ctrl.btn_output.click()
    if ctrl.combo_channel.count() > 1:
        ctrl.combo_channel.setCurrentIndex(1)
    assert time.monotonic() - t0 < 0.2
    dev.delay = 0
    dev.release.set()
    qtbot.waitUntil(lambda: any(c.startswith("VOLT ") for c in dev.commands()), timeout=5000)


def test_cancel_while_connecting(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(), "E36155A_1")
    dev = ctrl.psu.rm.device
    dev.delay = 2.0                                     # *IDN? lento
    ctrl.btn_connect.click()
    assert ctrl._conn_state == "connecting" and ctrl.btn_connect.text() == window.lang.get("btn_cancel")
    assert not ctrl.combo_profile.isEnabled()
    qtbot.wait(100)
    ctrl.btn_connect.click()                            # Cancelar
    dev.release.set()
    qtbot.waitUntil(lambda: ctrl._conn_state == "idle", timeout=6000)
    assert not ctrl.is_online() and dev.closed


def test_app_close_with_hung_instrument_returns(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(), "E36155A_1")
    dev = ctrl.psu.rm.device
    _connect(qtbot, ctrl)
    dev.delay = 30.0                                    # red colgada
    qtbot.waitUntil(lambda: dev.log[-1][1].startswith("MEAS"), timeout=3000)
    window.SHUTDOWN_TIMEOUT_S = 0.3
    t0 = time.monotonic()
    window.close()
    assert time.monotonic() - t0 < 2.0
    assert window.unclean_shutdown            # main() saldrá con os._exit en lugar de colgarse
    dev.release.set()


# ---------------- Red de seguridad del lock ----------------

def test_lock_timeout_returns_error_instead_of_hanging(isolated_config):
    psu = PowerSupply()
    psu.LOCK_TIMEOUT_S = 0.2
    psu.psu, psu.connected = FakeDevice(), True
    holder = threading.Thread(target=lambda: (psu._visa_lock.acquire(), time.sleep(1.0), psu._visa_lock.release()))
    holder.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    ok, msg = psu.set_output(True)
    assert not ok and "ocupada" in msg and time.monotonic() - t0 < 0.6
    with pytest.raises(InstrumentBusyError):
        psu.read_telemetry()
    psu.disconnect()                                     # cierra aunque el lock siga tomado
    assert psu.psu is None and not psu.connected
    holder.join()


def test_abort_interrupts_telemetry_between_queries(isolated_config):
    psu = PowerSupply()
    psu.load_profile_settings("BK_PRECISION_1")
    dev = FakeDevice()
    psu.psu, psu.connected = dev, True
    psu.request_abort()
    psu.read_telemetry()
    assert not [c for c in dev.commands() if c.startswith("MEAS") or c.startswith(":MEAS")]


def test_profile_locked_while_connected(window, qtbot):
    ctrl = _prepare(window, window.docks[0][1], FakeDevice(), "E36155A_1")
    _connect(qtbot, ctrl)
    assert not ctrl.combo_profile.isEnabled() and not ctrl.txt_ip.isEnabled()
    ctrl.disconnect_psu()
    qtbot.waitUntil(lambda: ctrl._conn_state == "idle", timeout=5000)
    assert ctrl.combo_profile.isEnabled()
