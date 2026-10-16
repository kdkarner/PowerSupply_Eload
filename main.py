"""
Archivo: main.py
Descripción: Interfaz gráfica y orquestador multi-fuente (hasta 6 instrumentos/canales)
             con arquitectura multihilo thread-safe, validación estricta de IP/Perfiles
             y telemetría de solo lectura no bloqueante.
"""

import sys
import time
import signal
import math
from collections import deque
import os
from PyQt6.QtGui import QIcon, QGuiApplication, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QDockWidget, QMainWindow, QWidget, QSizePolicy,
    QComboBox, QLabel, QMessageBox, QFileDialog
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, pyqtSlot, Qt, QTimer, QByteArray, QUrl, QT_VERSION_STR, PYQT_VERSION_STR
)

from powsupply import PowerSupply
from eload import ElectronicLoad, MODE_UNITS
from recorder import DataRecorder, IDLE, RECORDING, PAUSED
from hmi import (
    SinglePSUWidget, SingleELoadWidget, MultiPSUWindowUI, LogEventWidget, ResponsiveScrollArea, SCPIConsoleWindow
)
from config import LanguageManager, AppConfigManager
import colors
from paths import get_base_dir, get_config_dir

SOFTWARE_VERSION = "01.09.00"  # XX.XX.XX: mayor.medio.menor


class CustomDockWidget(QDockWidget):
    """QDockWidget personalizado que detecta el cierre manual y gestiona su callback."""

    def __init__(self, title, parent=None, psu_ctrl=None, on_close_callback=None):
        super().__init__(title, parent)
        self.psu_ctrl = psu_ctrl
        self.on_close_callback = on_close_callback

    def closeEvent(self, event):
        if self.on_close_callback and self.psu_ctrl:
            self.on_close_callback(self, self.psu_ctrl)
        super().closeEvent(event)


# ----------------------------------------------------------------------
# WORKER DE TELEMETRÍA Y COMUNICACIONES SCPI (Thread-Safe & Read-Only)
# ----------------------------------------------------------------------
# Hilos de instrumentos vivos: se conservan hasta que terminan solos (nunca se usa QThread.terminate())
_LIVE_THREADS = set()


class _ThreadReaper(QObject):
    """
    Libera hilo y worker en el hilo de la interfaz, solo cuando el hilo ya terminó. Así ningún objeto se
    destruye en un hilo mientras otro lo usa (antes deleteLater en el hilo del worker causaba un segfault).
    """

    def __init__(self, thread: QThread, worker: QObject):
        super().__init__()
        self._entry = (thread, worker, self)
        _LIVE_THREADS.add(self._entry)
        thread.finished.connect(self.reap)  # self vive en el hilo de la interfaz → llamada encolada

    def reap(self):
        if self._entry is None:
            return
        thread = self._entry[0]
        thread.wait(2000)  # finished se emite justo antes de salir: esperar el final real del hilo
        _LIVE_THREADS.discard(self._entry)
        self._entry = None


class TelemetryWorker(QObject):
    """
    Dueño de la sesión con el instrumento: conecta (con la secuencia segura), sondea la telemetría, ejecuta
    los comandos del usuario y desconecta, todo en su propio hilo. La interfaz solo envía señales.
    """
    data_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)
    command_result_signal = pyqtSignal(bool, str)
    connected_signal = pyqtSignal(bool, str)
    finished = pyqtSignal()

    def __init__(self, psu: PowerSupply, interval_ms: int = 400):
        super().__init__()
        self.psu = psu
        self.interval_ms = interval_ms
        self.timer = None
        self._is_running = False

    @pyqtSlot(str, str, str, int)
    def connect_slot(self, ip: str, profile_key: str, conn_type: str, port: int):
        """Conexión segura: sesión VISA → *IDN? → apagar y verificar todas las salidas → leer setpoints."""
        ok, msg = self.psu.connect(ip, profile_key, conn_type, port)
        self.log_signal.emit(msg)
        if ok:
            for step in (self.psu.verify_identity, self.psu.all_outputs_off):
                if self.psu._abort.is_set():
                    ok, msg = False, "Conexión cancelada"
                    break
                step_ok, step_msg = step()
                self.log_signal.emit(step_msg)
                if not step_ok:
                    ok, msg = False, step_msg
                    break
        if ok and not self.psu._abort.is_set():
            _, setpoints_msg = self.psu.read_setpoints()  # si falla, se usan los guardados (solo aviso)
            self.log_signal.emit(setpoints_msg)

        if not ok or self.psu._abort.is_set():
            self.psu.disconnect()
            self.connected_signal.emit(False, msg if not ok else "Conexión cancelada")
            self.finished.emit()
            return

        self.connected_signal.emit(True, msg)
        self.start_polling()

    @pyqtSlot()
    def shutdown_slot(self):
        """Detiene el sondeo y cierra la sesión VISA desde este hilo; luego el hilo termina solo."""
        self.stop_polling()
        self.psu.disconnect()
        self.finished.emit()

    @pyqtSlot(int)
    def select_channel_slot(self, channel: int):
        if self._is_running and self.psu.connected:
            self.psu.select_channel(channel)

    @pyqtSlot()
    def start_polling(self):
        self._is_running = True
        self.timer = QTimer(self)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self._poll_telemetry)
        self.timer.start()

    @pyqtSlot()
    def stop_polling(self):
        self._is_running = False
        if self.timer:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None

    def _poll_telemetry(self):
        """Ciclo de consulta exclusivo de LECTURA. Jamás envía comandos de escritura SCPI."""
        if self._is_running and getattr(self.psu, 'connected', False):
            try:
                data = self.psu.read_telemetry()
                if data and self._is_running:
                    self.data_signal.emit(data)
            except Exception as e:
                if self._is_running:
                    self.log_signal.emit(f"Error en telemetría: {e}")

    @pyqtSlot(float, float)
    def apply_settings_slot(self, voltage: float, current_limit: float):
        """Ejecuta orden de ESCRITURA únicamente tras acción explícita del usuario."""
        if not (self._is_running and getattr(self.psu, 'connected', False)):
            return

        ok, msg = self.psu.update_settings(voltage, current_limit)
        self.command_result_signal.emit(ok, msg)

    @pyqtSlot(str, float)
    def apply_load_slot(self, mode: str, level: float):
        """Programa modo y nivel de una carga electrónica (acción explícita del usuario)."""
        if not (self._is_running and getattr(self.psu, 'connected', False)):
            return

        ok, msg = self.psu.set_load(mode, level)
        self.command_result_signal.emit(ok, msg)

    @pyqtSlot(bool)
    def set_output_slot(self, state: bool):
        """Ejecuta orden de conmutación de salida SCPI por petición explícita."""
        if not (self._is_running and getattr(self.psu, 'connected', False)):
            return

        ok, msg = self.psu.set_output(state)
        self.command_result_signal.emit(ok, msg)


# ----------------------------------------------------------------------
# CONTROLADOR DE INSTRUMENTO INDIVIDUAL
# ----------------------------------------------------------------------
class SinglePSUController(SinglePSUWidget):
    MAX_HISTORY_POINTS = 80

    KIND = "psu"
    TITLE_KEY = "psu_title"

    sig_apply_settings = pyqtSignal(float, float)
    sig_apply_load = pyqtSignal(str, float)
    sig_set_output = pyqtSignal(bool)
    sig_connect = pyqtSignal(str, str, str, int)
    sig_shutdown = pyqtSignal()
    sig_select_channel = pyqtSignal(int)
    sig_log_event = pyqtSignal(str)  # Reenvía cada línea de la consola SCPI al Log Event global
    sig_telemetry = pyqtSignal(dict)  # Muestras de telemetría para el grabador

    def __init__(self, psu_index: int):
        super().__init__(psu_index)
        self.psu = self._create_driver()
        self.thread = None
        self.worker = None
        self._conn_state = "idle"  # idle | connecting | connected | disconnecting
        self._last_connect_failed = False

        self.v_histories = {}
        self.i_histories = {}
        self.max_scale_v = 24.0
        self._output_ui_state = None

        # Sin setMinimumSize()/minimumSizeHint() fijos: el mínimo lo calcula el layout según la
        # fuente y el DPI de cada pantalla. Un mínimo fijo permitía comprimir los labels al
        # cambiar de monitor o de PC. Si no cabe, el dock muestra barras de desplazamiento.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._bind_events()
        self._load_initial_state()
        self.update_output_button_ui(False)

    def _bind_events(self):
        self.combo_profile.currentIndexChanged.connect(self.on_profile_changed)
        self.combo_channel.currentIndexChanged.connect(self.on_channel_changed)
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.btn_apply_v.clicked.connect(self.apply_voltage)
        self.btn_apply_i.clicked.connect(self.apply_current)
        self.btn_output.clicked.connect(self.toggle_output)

    def _create_driver(self):
        return PowerSupply()

    def log(self, message: str):
        self.sig_log_event.emit(message)

    def update_output_button_ui(self, is_on: bool):
        self.psu.ON = is_on
        # Se llama en cada tick de telemetría: solo re-estilizar cuando cambia el estado
        if self._output_ui_state is is_on:
            return
        self._output_ui_state = is_on
        self.output_is_on = is_on

        key = self.OUTPUT_KEYS[1] if is_on else self.OUTPUT_KEYS[0]
        fallback = "OUTPUT ON" if is_on else "OUTPUT OFF"
        self.btn_output.setText(self.lang_mgr.get(key) if self.lang_mgr else fallback)
        self.btn_output.setStyleSheet(colors.output_button_style(is_on))

    def sync_controls_from_backend(self):
        self.spin_v.setMaximum(self.psu.max_v)
        self.spin_i.setMaximum(self.psu.max_i)
        self.spin_v.setValue(self.psu.SET_Volt)
        self.spin_i.setValue(self.psu.SET_MaxI)

    def _load_initial_state(self):
        profiles = self.psu.config.get('profiles', {})
        self.combo_profile.blockSignals(True)
        self.combo_profile.clear()

        for key, pinfo in profiles.items():
            self.combo_profile.addItem(pinfo.get('name', key), userData=key)

        profile_key = self.psu.active_profile
        for i in range(self.combo_profile.count()):
            if self.combo_profile.itemData(i) == profile_key:
                self.combo_profile.setCurrentIndex(i)
                break
        self.combo_profile.blockSignals(False)

        idx_type = self.combo_conn_type.findText(self.psu.conn_type)
        if idx_type >= 0:
            self.combo_conn_type.setCurrentIndex(idx_type)

        self.txt_ip.setText(self.psu.ip)
        self.spin_port.setValue(self.psu.port)
        self.on_profile_changed()

    def update_max_voltage_scale(self):
        if self.psu.channels:
            max_v_found = max([ch.max_v for ch in self.psu.channels.values()], default=24.0)
            self.max_scale_v = max_v_found if max_v_found > 0 else 24.0
        else:
            self.max_scale_v = 24.0

        if hasattr(self, 'plot_widget') and hasattr(self.plot_widget, 'setYRange'):
            self.plot_widget.setYRange(0, self.max_scale_v)

    def reset_histories(self):
        self.v_histories.clear()
        self.i_histories.clear()
        for ch_num in self.psu.channels.keys():
            self.v_histories[ch_num] = deque(maxlen=self.MAX_HISTORY_POINTS)
            self.i_histories[ch_num] = deque(maxlen=self.MAX_HISTORY_POINTS)

    def on_profile_changed(self, *_signal_args):
        profile_key = self.combo_profile.currentData()
        if not profile_key:
            return

        if self.is_busy():
            return  # el perfil no se cambia con el instrumento en uso (el combo está deshabilitado)

        self.psu.load_profile_settings(profile_key)
        self.txt_ip.setText(self.psu.ip)
        self.spin_port.setValue(self.psu.port)

        idx_conn = self.combo_conn_type.findText(self.psu.conn_type)
        if idx_conn >= 0:
            self.combo_conn_type.setCurrentIndex(idx_conn)

        self.combo_channel.blockSignals(True)
        self.combo_channel.clear()

        prof = self.psu.get_current_profile()
        if self.psu.is_multichannel():
            self.combo_channel.setEnabled(True)
            channels_dict = prof.get('channels', {})

            sorted_channels = sorted([int(k) for k in channels_dict.keys()])
            for ch in sorted_channels:
                self.combo_channel.addItem(f"CH {ch}", userData=ch)

            idx_ch = self.combo_channel.findData(self.psu.channel)
            if idx_ch >= 0:
                self.combo_channel.setCurrentIndex(idx_ch)
        else:
            self.combo_channel.setEnabled(False)
            self.combo_channel.addItem("CH 1", userData=1)

        self.combo_channel.blockSignals(False)
        self.sync_controls_from_backend()
        self.update_max_voltage_scale()
        self.reset_histories()

    # ------------------------------------------------------------------
    # ESTADO DE CONEXIÓN (la interfaz nunca hace I/O ni espera al hilo del instrumento)
    # ------------------------------------------------------------------
    def is_online(self) -> bool:
        return self._conn_state == "connected"

    def is_busy(self) -> bool:
        """Conectado o en transición: el perfil / la IP están en uso."""
        return self._conn_state != "idle"

    def _apply_conn_ui(self):
        state = self._conn_state
        idle = state == "idle"
        for w in (self.combo_profile, self.txt_ip, self.spin_port, self.combo_conn_type):
            w.setEnabled(idle)
        self.btn_connect.setEnabled(state != "disconnecting")
        lang = self.lang_mgr
        if state == "connecting":
            self.btn_connect.setText(lang.get("btn_cancel") if lang else "Cancelar")
            self.set_transition_status(lang.get("status_connecting") if lang else "CONECTANDO...")
        elif state == "disconnecting":
            self.set_transition_status(lang.get("status_disconnecting") if lang else "DESCONECTANDO...")

    def retranslate_ui(self, lang_mgr):
        super().retranslate_ui(lang_mgr)
        self._apply_conn_ui()

    def on_channel_changed(self, *_signal_args):
        ch = self.combo_channel.currentData()
        if ch is None:
            return
        if self.is_online():
            self.psu.channel_num = int(ch)       # la vista refleja el canal al instante
            self.sig_select_channel.emit(int(ch))  # el SCPI lo envía el hilo del instrumento
        else:
            self.psu.select_channel(ch)
        self.sync_controls_from_backend()
        self.log(f"Canal {ch} seleccionado.")

    def toggle_connection(self, *_signal_args):
        if self._conn_state == "idle":
            self._begin_connect()
        elif self._conn_state in ("connecting", "connected"):
            self.disconnect_psu()

    def _begin_connect(self):
        ip = self.txt_ip.text().strip()
        profile_key = self.combo_profile.currentData()
        conn_type = self.combo_conn_type.currentText()
        port = self.spin_port.value()

        # Validación de duplicados en la ventana principal
        main_win = self.window()
        if hasattr(main_win, 'is_resource_in_use'):
            in_use, reason = main_win.is_resource_in_use(profile_key, ip, current_ctrl=self)
            if in_use:
                QMessageBox.warning(self, "Conflicto de Conexión", reason)
                self.log(f"Rechazado: {reason}")
                return

        self._start_worker()
        self._conn_state = "connecting"
        self._last_connect_failed = False
        self._apply_conn_ui()
        self.sig_connect.emit(ip, profile_key, conn_type, port)

    def _start_worker(self):
        thread = QThread()
        worker = TelemetryWorker(self.psu, interval_ms=400)
        worker.moveToThread(thread)
        self.thread, self.worker = thread, worker
        _ThreadReaper(thread, worker)

        worker.data_signal.connect(self.on_telemetry_received)
        worker.log_signal.connect(self.log)
        worker.command_result_signal.connect(self._on_command_result)
        worker.connected_signal.connect(self._on_connected)

        self.sig_connect.connect(worker.connect_slot)
        self.sig_shutdown.connect(worker.shutdown_slot)
        self.sig_select_channel.connect(worker.select_channel_slot)
        self.sig_apply_settings.connect(worker.apply_settings_slot)
        self.sig_apply_load.connect(worker.apply_load_slot)
        self.sig_set_output.connect(worker.set_output_slot)

        # quit() directo desde el hilo del worker: funciona aunque la interfaz esté esperando al cerrar la app
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(self._on_worker_finished)
        thread.start()

    def _on_command_result(self, ok: bool, msg: str):
        self.log(msg)

    def _on_connected(self, ok: bool, msg: str):
        if ok:
            self._conn_state = "connected"
            self.set_connection_status(connected=True)
            self._output_ui_state = None
            self.update_output_button_ui(False)  # todas las salidas quedaron apagadas y verificadas
            self.reset_histories()
            self.sync_controls_from_backend()    # setpoints reales leídos del equipo
            self._apply_conn_ui()
        else:
            self._last_connect_failed = True
            self._conn_state = "disconnecting"
            self.set_connection_status(connected=False, is_error=True)
            self._apply_conn_ui()
        main_win = self.window()
        if hasattr(main_win, 'refresh_all_profile_combos'):
            main_win.refresh_all_profile_combos()

    def _detach_worker_signals(self):
        for sig in (self.sig_connect, self.sig_shutdown, self.sig_select_channel,
                    self.sig_apply_settings, self.sig_apply_load, self.sig_set_output):
            try:
                sig.disconnect()
            except TypeError:
                pass
        if self.worker:
            for sig in (self.worker.data_signal, self.worker.log_signal,
                        self.worker.command_result_signal, self.worker.connected_signal):
                try:
                    sig.disconnect()
                except TypeError:
                    pass

    def disconnect_psu(self):
        """Pide al hilo del instrumento que cierre la sesión. No bloquea la interfaz ni mata el hilo."""
        if self.thread is None:
            self._conn_state = "idle"
            self.update_output_button_ui(False)
            self.set_connection_status(connected=False)
            self._apply_conn_ui()
            return

        if self._conn_state in ("connecting", "connected"):
            self.psu.request_abort()       # corta la consulta en curso sin tomar el lock
            self.sig_shutdown.emit()
            self._detach_worker_signals()
            self._conn_state = "disconnecting"
            self.update_output_button_ui(False)
            self._apply_conn_ui()

    def _on_worker_finished(self):
        was_failed = self._last_connect_failed
        self.thread = None
        self.worker = None
        self._conn_state = "idle"
        self.set_connection_status(connected=False, is_error=was_failed)
        self._apply_conn_ui()
        if not was_failed:
            self.log("Instrumento desconectado.")
        main_win = self.window()
        if hasattr(main_win, 'refresh_all_profile_combos'):
            main_win.refresh_all_profile_combos()

    def wait_for_shutdown(self, timeout_ms: int) -> bool:
        """Solo para el cierre de la app: espera (acotado) a que el hilo del instrumento termine."""
        thread = self.thread
        return thread is None or thread.wait(max(0, int(timeout_ms)))

    @pyqtSlot(dict)
    def on_telemetry_received(self, data: dict):
        try:
            for ch_num, ch_data in data.items():
                if ch_num not in self.v_histories:
                    self.v_histories[ch_num] = deque(maxlen=self.MAX_HISTORY_POINTS)
                if ch_num not in self.i_histories:
                    self.i_histories[ch_num] = deque(maxlen=self.MAX_HISTORY_POINTS)

                self.v_histories[ch_num].append(ch_data.get('ReadV', 0.0))
                self.i_histories[ch_num].append(ch_data.get('ReadI', 0.0))

            active_ch = self.psu.channel
            active_data = data.get(active_ch, {'ReadV': 0.0, 'ReadI': 0.0, 'Power': 0.0, 'ON': False})

            is_on = bool(active_data.get('ON', False))
            self.update_output_button_ui(is_on)

            v_history = list(self.v_histories.get(active_ch, []))
            i_history = list(self.i_histories.get(active_ch, []))

            self.update_ui_telemetry(active_data, v_history, i_history)
            self.sig_telemetry.emit(data)
        except RuntimeError:
            pass

    def apply_voltage(self, *_signal_args):
        v_val = self.spin_v.value()
        i_val = self.spin_i.value()
        if self.is_online():
            self.sig_apply_settings.emit(v_val, i_val)
        else:
            self.psu.SET_Volt = v_val
            self.psu.save_config()
            self.log(f"CH{self.psu.channel} Set V: {self.psu.SET_Volt}V (Guardado)")

    def apply_current(self, *_signal_args):
        v_val = self.spin_v.value()
        i_val = self.spin_i.value()
        if self.is_online():
            self.sig_apply_settings.emit(v_val, i_val)
        else:
            self.psu.SET_MaxI = i_val
            self.psu.save_config()
            self.log(f"CH{self.psu.channel} Set I: {self.psu.SET_MaxI}A (Guardado)")

    def toggle_output(self, *_signal_args):
        if self.is_online():
            target_state = not self.psu.ON
            self.sig_set_output.emit(target_state)
        else:
            self.log("Acción rechazada: Instrumento no conectado.")

    def get_state(self) -> dict:
        return {
            "profile": self.combo_profile.currentData(),
            "channel": self.combo_channel.currentData(),
            "ip": self.txt_ip.text(),
            "port": self.spin_port.value(),
            "conn_type": self.combo_conn_type.currentText()
        }

    def restore_state(self, state: dict):
        if not state:
            return

        profile_key = state.get("profile")
        if profile_key:
            idx = self.combo_profile.findData(profile_key)
            if idx >= 0:
                self.combo_profile.setCurrentIndex(idx)

        self.txt_ip.setText(state.get("ip", self.psu.ip))
        self.spin_port.setValue(state.get("port", self.psu.port))

        conn_type = state.get("conn_type")
        if conn_type:
            idx_conn = self.combo_conn_type.findText(conn_type)
            if idx_conn >= 0:
                self.combo_conn_type.setCurrentIndex(idx_conn)

        ch = state.get("channel")
        if ch and self.combo_channel.isEnabled():
            idx_ch = self.combo_channel.findData(ch)
            if idx_ch >= 0:
                self.combo_channel.setCurrentIndex(idx_ch)


# ----------------------------------------------------------------------
# CONTROLADOR DE CARGA ELECTRÓNICA (Keysight EL34243A y compatibles)
# ----------------------------------------------------------------------
class SingleELoadController(SinglePSUController, SingleELoadWidget):
    """Misma conexión, hilo de telemetría, log y sesión que la fuente; ajustes por modo CC/CV/CR/CP."""

    KIND = "eload"
    TITLE_KEY = "eload_title"

    def _create_driver(self):
        return ElectronicLoad()

    def _bind_events(self):
        self.combo_profile.currentIndexChanged.connect(self.on_profile_changed)
        self.combo_channel.currentIndexChanged.connect(self.on_channel_changed)
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.combo_load_mode.currentIndexChanged.connect(self.on_load_mode_changed)
        self.btn_apply_level.clicked.connect(self.apply_level)
        self.btn_output.clicked.connect(self.toggle_output)

    def _show_mode_level(self, mode: str):
        """Ajusta rango, unidad y valor guardado del nivel para el modo indicado (no envía nada)."""
        self.spin_level.setMaximum(self.psu.mode_limit(mode))
        self.spin_level.setSuffix(f" {MODE_UNITS[mode]}")
        self.spin_level.setValue(self.psu.level(mode))
        self.update_level_label()

    def sync_controls_from_backend(self):
        mode = self.psu.mode
        self.combo_load_mode.blockSignals(True)
        idx = self.combo_load_mode.findData(mode)
        if idx >= 0:
            self.combo_load_mode.setCurrentIndex(idx)
        self.combo_load_mode.blockSignals(False)
        self._show_mode_level(mode)

    def on_load_mode_changed(self, *_signal_args):
        self._show_mode_level(self.current_load_mode())

    def apply_level(self, *_signal_args):
        mode = self.current_load_mode()
        level = self.spin_level.value()
        if self.is_online():
            self.sig_apply_load.emit(mode, level)
        else:
            self.psu.store_load(mode, level)
            self.log(f"CH{self.psu.channel} {mode}: {level} {MODE_UNITS[mode]} (Guardado)")


# ----------------------------------------------------------------------
# VENTANA PRINCIPAL MULTI-FUENTE
# ----------------------------------------------------------------------
class MultiPSUController(MultiPSUWindowUI):
    MAX_PSU_COUNT = 6
    SHUTDOWN_TIMEOUT_S = 5.0
    MAX_ELOAD_COUNT = 6
    # Ancho mínimo cómodo de un panel en layout angosto (incluye barra de scroll vertical)
    MIN_PANEL_WIDTH = 320
    INSTRUMENT_CLASSES = {"psu": SinglePSUController, "eload": SingleELoadController}
    LOG_DEFAULT_HEIGHT = 180
    DOCK_SEPARATOR = 6

    def __init__(self):
        super().__init__()
        self.config_mgr = AppConfigManager()
        colors.load_themes()  # config/themes.yaml: colores de cada tema y tema activo
        self.lang = LanguageManager()
        self.psu_counter = 0
        self.counters = {"psu": 0, "eload": 0}
        self.docks: list[tuple[QDockWidget, SinglePSUController, int]] = []
        self._ready = False

        dummy_central = QWidget()
        # Solo ancho 0: si también el alto fuera 0, todo el alto sobrante iría al dock inferior (Log Event)
        dummy_central.setMaximumWidth(0)
        self.setCentralWidget(dummy_central)

        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AnimatedDocks |
            QMainWindow.DockOption.GroupedDragging
        )

        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.TopDockWidgetArea)
        self.setCorner(Qt.Corner.TopRightCorner, Qt.DockWidgetArea.TopDockWidgetArea)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.BottomDockWidgetArea)

        self._setup_language_ui()
        self._setup_log_dock()
        self.btn_add_psu.clicked.connect(lambda: self.add_psu())
        self.btn_add_eload.clicked.connect(lambda: self.add_eload())

        self._restoring = False
        self._setup_console_window()
        self._setup_recorder()
        self._setup_menus()

        saved_lang = self.config_mgr.get_language("es")
        idx_lang = self.combo_lang.findData(saved_lang)
        if idx_lang >= 0:
            self.combo_lang.setCurrentIndex(idx_lang)
            self.lang.current_lang = saved_lang

        self.log_event(self.lang.get("evt_app_started", version=SOFTWARE_VERSION))
        if self.restore_app_session():
            self.log_event(self.lang.get("evt_session_restored", count=len(self.docks)))
        else:
            self.add_psu()
            QTimer.singleShot(150, self.equalize_dock_sizes)

        self.retranslate_ui()
        self._sync_log_button()
        self.apply_theme(colors.active_theme(), save=False)
        self._ready = True

    def is_resource_in_use(self, profile_key: str, ip_address: str, current_ctrl: SinglePSUController) -> tuple[bool, str]:
        """Verifica si un perfil o IP ya está siendo operado por otra fuente activa."""
        ip_clean = ip_address.strip()
        for _, ctrl, idx in self.docks:
            if ctrl is current_ctrl:
                continue

            if ctrl.is_busy():
                connected_profile = ctrl.combo_profile.currentData()
                connected_ip = ctrl.txt_ip.text().strip()

                # Los perfiles de fuentes y cargas viven en archivos distintos: solo comparar del mismo tipo
                if ctrl.KIND == current_ctrl.KIND and connected_profile == profile_key:
                    return True, f"El perfil '{connected_profile}' ya está conectado en la Fuente #{idx}."
                if connected_ip and connected_ip == ip_clean:
                    return True, f"La IP '{ip_clean}' ya está en uso por la Fuente #{idx}."

        return False, ""

    def refresh_all_profile_combos(self):
        """Actualiza visualmente el estado habilitado/deshabilitado de los perfiles en uso."""
        connected_profiles = {
            (ctrl.KIND, ctrl.combo_profile.currentData()): ctrl
            for _, ctrl, _ in self.docks if ctrl.is_busy()
        }

        for _, ctrl, _ in self.docks:
            combo = ctrl.combo_profile
            combo.blockSignals(True)
            current_selected = combo.currentData()

            for i in range(combo.count()):
                prof_key = combo.itemData(i)
                item_model = combo.model().item(i)

                owner = connected_profiles.get((ctrl.KIND, prof_key))
                if owner is not None and owner is not ctrl:
                    if item_model:
                        item_model.setEnabled(False)
                else:
                    if item_model:
                        item_model.setEnabled(True)

            combo.blockSignals(False)

    def _setup_language_ui(self):
        self.toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)

        self.lbl_lang = QLabel(self.lang.get("lang_label") + " ", self)
        self.toolbar.addWidget(self.lbl_lang)

        self.combo_lang = QComboBox(self)
        self.combo_lang.addItem("Español", userData="es")
        self.combo_lang.addItem("English", userData="en")
        self.combo_lang.addItem("Français", userData="fr")

        self.combo_lang.currentIndexChanged.connect(self.on_language_changed)
        self.toolbar.addWidget(self.combo_lang)

    def on_language_changed(self, *_signal_args):
        selected_lang = self.combo_lang.currentData()
        if selected_lang:
            self.lang.current_lang = selected_lang
            self.config_mgr.save_language(selected_lang)
            self.retranslate_ui()
            if self._ready:  # no registrar la selección inicial del idioma guardado
                self.log_event(self.lang.get("evt_lang_changed"))

    # ------------------------------------------------------------------
    # LOG EVENT: panel inferior que se muestra/oculta con "View Log"
    # ------------------------------------------------------------------
    def _setup_log_dock(self):
        self.log_widget = LogEventWidget()

        self.log_dock = QDockWidget(self.lang.get("log_dock_title"), self)
        self.log_dock.setObjectName("LogEventDock")
        self.log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.log_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.log_dock.setWidget(self.log_widget)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        # El botón de cerrar del dock lo oculta; el menú Ver se sincroniza
        self._log_height_on_screen = 0
        self.log_dock.visibilityChanged.connect(lambda _visible: self._on_log_visibility_changed())

    # ------------------------------------------------------------------
    # MENÚS: Config / View / Help
    # ------------------------------------------------------------------
    def _setup_menus(self):
        for act in self.lang_actions.values():
            act.triggered.connect(lambda _checked, code=act.data(): self.select_language(code))
        self.act_open_config.triggered.connect(self.open_config_folder)
        self.act_view_console.triggered.connect(self.set_console_visible)
        self.act_view_log.triggered.connect(self.set_log_visible)
        self.act_equalize.triggered.connect(lambda _checked=False: self.equalize_dock_sizes())
        self.act_about.triggered.connect(lambda _checked=False: self.show_about())
        self.act_about_qt.triggered.connect(lambda _checked=False: QApplication.aboutQt())
        for act in self.theme_actions.values():
            act.triggered.connect(lambda _checked, name=act.data(): self.apply_theme(name))

    def apply_theme(self, name: str, save: bool = True):
        """Aplica un tema (Ver → Temas) a toda la app y lo guarda en config/themes.yaml."""
        if save:
            colors.load_themes()  # recoger colores editados a mano en el YAML
        colors.set_theme(name)
        app = QApplication.instance()
        if app is not None:
            app.setPalette(colors.palette())
        qss = colors.stylesheet()
        self.setStyleSheet(qss)
        self.console_window.setStyleSheet(qss)
        self.refresh_toolbar_icons()
        for _, ctrl, _ in self.docks:
            ctrl.apply_theme()
            ctrl._output_ui_state = None  # forzar re-estilizado del botón de salida
            ctrl.update_output_button_ui(ctrl.output_is_on)
        self.theme_actions[name].setChecked(True)
        if save:
            colors.save_active_theme(name)
            self.log_event(self.lang.get("evt_theme_changed", name=self.lang.get(f"theme_{name}")))

    def select_language(self, code: str):
        idx = self.combo_lang.findData(code)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)

    def open_config_folder(self, *_signal_args):
        QDesktopServices.openUrl(QUrl.fromLocalFile(get_config_dir()))

    def show_about(self):
        text = self.lang.get("about_text", app=self.lang.get("window_title"), version=SOFTWARE_VERSION,
                             qt=QT_VERSION_STR, pyqt=PYQT_VERSION_STR)
        QMessageBox.about(self, self.lang.get("about_title"), text)

    # ------------------------------------------------------------------
    # GRABACIÓN DE TELEMETRÍA: Grabar / Pausa / Detener / Exportar
    # ------------------------------------------------------------------
    def _setup_recorder(self):
        self.recorder = DataRecorder()
        self.btn_record.clicked.connect(lambda: self.record_start())
        self.btn_pause.clicked.connect(self.record_pause)
        self.btn_stop.clicked.connect(lambda: self.record_stop())
        self.btn_export.clicked.connect(lambda: self.record_export())
        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(500)
        self.rec_timer.timeout.connect(self._update_record_ui)
        self._update_record_ui()

    def record_start(self):
        result = self.recorder.start()
        if result == "started":
            self.log_event(self.lang.get("evt_rec_started", file=self.recorder.file_path))
            if not any(c.is_online() for _, c, _ in self.docks):
                self.log_event(self.lang.get("rec_no_instruments"), level="warning")
        elif result == "resumed":
            self.log_event(self.lang.get("evt_rec_resumed"))
        self.rec_timer.start()
        self._update_record_ui()

    def record_pause(self, checked: bool = True):
        """Pausa conmutable: pulsar de nuevo reanuda."""
        if checked and self.recorder.pause():
            self.log_event(self.lang.get("evt_rec_paused"))
        elif not checked and self.recorder.state == PAUSED:
            self.record_start()
            return
        self._update_record_ui()

    def record_stop(self):
        if self.recorder.stop():
            self.log_event(self.lang.get("evt_rec_stopped", samples=self.recorder.samples,
                                         file=self.recorder.file_path))
        self.rec_timer.stop()
        self._update_record_ui()

    def can_export(self) -> bool:
        """Solo se exporta una grabación finalizada (después de Stop)."""
        return self.recorder.state == IDLE and self.recorder.has_data()

    def record_export(self):
        if not self.can_export():
            QMessageBox.information(self, self.lang.get("export_dialog_title"), self.lang.get("rec_no_data"))
            return
        default = os.path.join(os.path.expanduser("~"), os.path.basename(self.recorder.file_path))
        path, _ = QFileDialog.getSaveFileName(self, self.lang.get("export_dialog_title"), default, "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self.recorder.export(path)
            self.log_event(self.lang.get("evt_rec_exported", file=path))
        except Exception as e:
            self.log_event(f"Error: {e}", level="error")
            QMessageBox.warning(self, self.lang.get("export_dialog_title"), str(e))

    def _record_samples(self, ctrl: SinglePSUController, data: dict):
        self.recorder.add_samples(self.instrument_name(ctrl), ctrl.KIND, ctrl.psu.active_profile, data)

    def _update_record_ui(self):
        state = self.recorder.state
        self.btn_record.setEnabled(state != RECORDING)
        self.btn_pause.setEnabled(state != IDLE)
        self.btn_pause.setChecked(state == PAUSED)
        self.btn_stop.setEnabled(state != IDLE)
        self.btn_export.setEnabled(self.can_export())

        secs = int(self.recorder.elapsed())
        clock = f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
        samples = f"{self.recorder.samples} {self.lang.get('rec_samples')}"
        if state == RECORDING:
            text = f"● {self.lang.get('rec_status_rec')} {clock} · {samples}"
        elif state == PAUSED:
            text = f"❚❚ {self.lang.get('rec_status_paused')} {clock} · {samples}"
        else:
            text = f"■ {clock} · {samples}" if self.recorder.has_data() else ""
        self.lbl_rec_status.setText(text)

    # ------------------------------------------------------------------
    # CONSOLA SCPI: ventana independiente (Ver → Consola SCPI)
    # ------------------------------------------------------------------
    def _setup_console_window(self):
        self.console_window = SCPIConsoleWindow(self)
        geo = self.config_mgr.get_value("console_geometry")
        if isinstance(geo, QByteArray):
            self.console_window.restoreGeometry(geo)
        self.console_window.closed.connect(lambda: self._on_console_closed())
        self.console_visible = False
        if self.config_mgr.get_console_visible(False):
            QTimer.singleShot(0, lambda: self.set_console_visible(True))

    def set_console_visible(self, visible: bool):
        """Abre/cierra la ventana Consola SCPI y recuerda la preferencia."""
        self.console_visible = visible
        self.act_view_console.setChecked(visible)
        self.config_mgr.save_console_visible(visible)
        if visible:
            self.console_window.show()
            self.console_window.raise_()
            self.console_window.activateWindow()
        elif self.console_window.isVisible():
            self.console_window.hide()
            self._save_console_geometry()

    def _on_console_closed(self):
        self._save_console_geometry()
        self.console_visible = False
        self.act_view_console.setChecked(False)
        self.config_mgr.save_console_visible(False)

    def _save_console_geometry(self):
        self.config_mgr.set_value("console_geometry", self.console_window.saveGeometry())

    def _refresh_console_sources(self):
        self.console_window.set_sources([(d.objectName(), self.instrument_name(c)) for d, c, _ in self.docks])

    def set_log_visible(self, visible: bool):
        """
        El Event Log se agrega en la parte inferior AGRANDANDO la ventana (no tapa ni achica los paneles);
        al ocultarlo la ventana vuelve a su alto. Maximizada o sin espacio en pantalla, comparte el alto.
        """
        if visible == (not self.log_dock.isHidden()):
            self._sync_log_button()
            return
        if visible:
            grown = self._resize_height_by(self.LOG_DEFAULT_HEIGHT + self.DOCK_SEPARATOR)
            self.log_dock.setVisible(True)
            # Alto del log = lo que creció la ventana, para que los paneles conserven su tamaño
            log_h = grown - self.DOCK_SEPARATOR if grown > self.DOCK_SEPARATOR else self.LOG_DEFAULT_HEIGHT
            self.resizeDocks([self.log_dock], [log_h], Qt.Orientation.Vertical)
        else:
            self.log_dock.setVisible(False)  # visibilityChanged → _on_log_visibility_changed achica la ventana
        self._sync_log_button()

    def _resize_height_by(self, delta: int) -> int:
        """Cambia el alto de la ventana en delta px dentro del área disponible. Devuelve lo aplicado."""
        if self.isMaximized() or self.isFullScreen():
            return 0
        new_h = max(self.minimumSizeHint().height(), self.height() + delta)
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            decorations = self.frameGeometry().height() - self.height()
            new_h = min(new_h, screen.height() - decorations)
        applied = new_h - self.height()
        if applied:
            self.resize(self.width(), new_h)
            if screen is not None and self.frameGeometry().bottom() > screen.bottom():
                self.move(self.x(), max(screen.top(), screen.bottom() - self.frameGeometry().height()))
        return applied

    def _on_log_visibility_changed(self):
        # Oculto por el usuario (menú o botón de cerrar del dock): devolver el alto que ocupaba
        if self._ready and self.log_dock.isHidden() and self._log_height_on_screen:
            self._resize_height_by(-self._log_height_on_screen)
            self._log_height_on_screen = 0
        elif not self.log_dock.isHidden() and self.log_dock.isVisible():
            QTimer.singleShot(0, self._remember_log_height)
        self._sync_log_button()

    def _remember_log_height(self):
        if not self.log_dock.isHidden():
            self._log_height_on_screen = self.log_dock.height() + self.DOCK_SEPARATOR

    def _sync_log_button(self):
        # isHidden() y no isVisible(): al minimizar la ventana el dock no debe darse por cerrado
        self.act_view_log.setChecked(not self.log_dock.isHidden())

    @staticmethod
    def _event_level(message: str) -> str:
        low = message.lower()
        if "error" in low or "erreur" in low:
            return "error"
        if "rechazad" in low or "rejected" in low or "conflict" in low:
            return "warning"
        return "info"

    def log_event(self, message: str, level: str = None, source: str = ""):
        self.log_widget.add_event(message, level or self._event_level(message), source)

    def retranslate_ui(self):
        self.setWindowTitle(f"{self.lang.get('window_title')} - v{SOFTWARE_VERSION}")
        self.btn_add_psu.setText(self.lang.get("add_psu"))
        self.btn_add_eload.setText(self.lang.get("add_eload"))
        self.btn_record.setToolTip(self.lang.get("tip_record"))
        self.btn_pause.setToolTip(self.lang.get("tip_pause"))
        self.btn_stop.setToolTip(self.lang.get("tip_stop"))
        self.btn_export.setToolTip(self.lang.get("tip_export"))
        self.btn_export.setText(self.lang.get("btn_export"))
        self._update_record_ui()
        self.menu_config.setTitle(self.lang.get("menu_config"))
        self.menu_language.setTitle(self.lang.get("menu_language"))
        self.act_open_config.setText(self.lang.get("act_open_config"))
        self.menu_view.setTitle(self.lang.get("menu_view"))
        self.act_view_console.setText(self.lang.get("act_view_console"))
        self.act_view_log.setText(self.lang.get("act_view_log"))
        self.act_equalize.setText(self.lang.get("act_equalize"))
        self.menu_help.setTitle(self.lang.get("menu_help"))
        self.act_about.setText(self.lang.get("act_about"))
        self.act_about_qt.setText(self.lang.get("act_about_qt"))
        self.menu_theme.setTitle(self.lang.get("menu_theme"))
        for name, act in self.theme_actions.items():
            act.setText(self.lang.get(f"theme_{name}"))
        act_lang = self.lang_actions.get(self.lang.current_lang)
        if act_lang:
            act_lang.setChecked(True)
        self.lbl_lang.setText(self.lang.get("lang_label") + " ")
        self.log_dock.setWindowTitle(self.lang.get("log_dock_title"))
        self.log_widget.retranslate_ui(self.lang)
        self.console_window.retranslate_ui(self.lang)

        for dock, psu_ctrl, idx in self.docks:
            dock.setWindowTitle(self.instrument_name(psu_ctrl))
            psu_ctrl.retranslate_ui(self.lang)
        self._refresh_console_sources()

    def equalize_dock_sizes(self):
        active_docks = [dock for dock, _, _ in self.docks if dock.isVisible()]
        if not active_docks:
            return

        target_width = max(200, self.width() // len(active_docks))
        self.resizeDocks(active_docks, [target_width] * len(active_docks), Qt.Orientation.Horizontal)

        # Alto: las fuentes ocupan todo lo disponible menos el Log Event (si está visible).
        # Valores iguales = reparto equitativo entre fuentes apiladas; en una fila fija la altura de la fila.
        log_h = self._log_target_height() if not self.log_dock.isHidden() else 0
        target_height = max(150, self.height() - self.toolbar.height() - log_h)
        docks = active_docks + ([self.log_dock] if log_h else [])
        heights = [target_height] * len(active_docks) + ([log_h] if log_h else [])
        self.resizeDocks(docks, heights, Qt.Orientation.Vertical)

    def _log_target_height(self) -> int:
        current = self.log_dock.height() if self.log_dock.isVisible() else self.LOG_DEFAULT_HEIGHT
        return min(max(current, 120), self.height() // 3)

    def on_dock_location_changed(self):
        QTimer.singleShot(50, self.equalize_dock_sizes)

    def instrument_name(self, ctrl: SinglePSUController) -> str:
        return f"{self.lang.get(ctrl.TITLE_KEY)}{ctrl.psu_index}"

    def add_psu(self, state_data: dict = None) -> tuple[QDockWidget, SinglePSUController]:
        return self.add_instrument("psu", state_data)

    def add_eload(self, state_data: dict = None) -> tuple[QDockWidget, SinglePSUController]:
        return self.add_instrument("eload", state_data)

    def add_instrument(self, kind: str, state_data: dict = None) -> tuple[QDockWidget, SinglePSUController]:
        limit, warn_key = ((self.MAX_PSU_COUNT, "max_psu_warning") if kind == "psu"
                           else (self.MAX_ELOAD_COUNT, "max_eload_warning"))
        if sum(1 for _, c, _ in self.docks if c.KIND == kind) >= limit:
            self.log_event(self.lang.get(warn_key), level="warning")
            QMessageBox.warning(self, "Límite", self.lang.get(warn_key))
            return None, None

        self.counters[kind] += 1
        psu_idx = self.counters[kind]
        if kind == "psu":
            self.psu_counter = psu_idx
        psu_ctrl = self.INSTRUMENT_CLASSES[kind](psu_idx)

        dock = CustomDockWidget(
            self.instrument_name(psu_ctrl),
            parent=self,
            psu_ctrl=psu_ctrl,
            on_close_callback=self.on_dock_closed
        )
        dock.setObjectName(f"{'PSU' if kind == 'psu' else 'ELoad'}Dock_{psu_idx}")
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        # Layout responsivo; el scroll solo aparece si ni el layout apilado cabe (nunca se comprimen labels)
        dock.setWidget(ResponsiveScrollArea(psu_ctrl))
        dock.dockLocationChanged.connect(lambda area: self.on_dock_location_changed())

        if state_data:
            psu_ctrl.restore_state(state_data)

        if self.docks:
            last_dock = self.docks[-1][0]
            self.splitDockWidget(last_dock, dock, Qt.Orientation.Horizontal)
        else:
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.docks.append((dock, psu_ctrl, psu_idx))
        psu_ctrl.retranslate_ui(self.lang)
        psu_ctrl.sig_log_event.connect(
            lambda msg, c=psu_ctrl, d=dock: self._on_instrument_message(c, d, msg))
        psu_ctrl.sig_telemetry.connect(lambda data, c=psu_ctrl: self._record_samples(c, data))
        self.refresh_all_profile_combos()
        self._refresh_console_sources()
        if not self._restoring:
            self.fit_window_to_docks()
        self.log_event(self.lang.get("evt_instr_added", name=self.instrument_name(psu_ctrl)))

        return dock, psu_ctrl

    def _on_instrument_message(self, ctrl: SinglePSUController, dock: QDockWidget, message: str):
        name = self.instrument_name(ctrl)
        level = self._event_level(message)
        self.console_window.add_entry(message, dock.objectName(), name, level)
        self.log_event(message, level=level, source=name)

    def fit_window_to_docks(self):
        """
        Tras agregar/cerrar un instrumento: si los paneles no caben con su ancho mínimo, agranda la ventana
        (sin pasar del área disponible de la pantalla) y reparte el ancho en partes iguales.
        """
        count = len(self.docks)  # el dock recién agregado aún figura oculto hasta el próximo layout
        if count and not (self.isMaximized() or self.isFullScreen()):
            needed = count * self.MIN_PANEL_WIDTH + (count - 1) * 6
            if self.width() < needed:
                screen = self.screen().availableGeometry() if self.screen() else None
                new_w = min(needed, screen.width()) if screen else needed
                self.resize(new_w, self.height())
                if screen and self.geometry().right() > screen.right():
                    self.move(max(screen.left(), screen.right() - new_w), self.y())
        QTimer.singleShot(0, self.equalize_dock_sizes)

    def on_dock_closed(self, dock: QDockWidget, psu_ctrl: SinglePSUController):
        psu_ctrl.disconnect_psu()
        self.docks = [d for d in self.docks if d[0] != dock]
        self._refresh_console_sources()
        QTimer.singleShot(0, self.equalize_dock_sizes)
        self.log_event(self.lang.get("evt_instr_closed", name=self.instrument_name(psu_ctrl)))
        dock.deleteLater()
        self.refresh_all_profile_combos()

    def save_app_session(self):
        psu_states = [
            {"dock_object_name": dock.objectName(), "kind": psu_ctrl.KIND, "state": psu_ctrl.get_state()}
            for dock, psu_ctrl, _ in self.docks
        ]

        self.config_mgr.save_session(
            language=self.lang.current_lang,
            geometry=self.saveGeometry(),
            window_state=self.saveState(),
            psu_states=psu_states,
            screen_signature=screen_signature()
        )

    def restore_app_session(self) -> bool:
        session = self.config_mgr.load_session()
        if not session:
            return False

        self._restoring = True  # la disposición guardada manda: no reajustar durante la restauración
        try:
            for state_info in session.get("psu_states", []):
                kind = state_info.get("kind", "psu")
                if kind in self.INSTRUMENT_CLASSES:
                    self.add_instrument(kind, state_data=state_info.get("state"))

            geo = session.get("geometry")
            if geo and isinstance(geo, QByteArray):
                self.restoreGeometry(geo)

            # La disposición de docks solo es válida con la misma configuración de pantallas
            # (resolución/escala). Si cambió, se reparte el espacio de nuevo.
            win_state = session.get("windowState")
            same_screens = session.get("screen_signature", "") == screen_signature()
            if same_screens and win_state and isinstance(win_state, QByteArray):
                self.restoreState(win_state)
            else:
                QTimer.singleShot(150, self.equalize_dock_sizes)

            return True
        except Exception as e:
            print(f"Error al restaurar sesión: {e}")
            return False
        finally:
            self._restoring = False

    def closeEvent(self, event):
        self.record_stop()  # cierra el CSV en curso (los datos ya están en disco)
        # Cerrar la consola junto con la app sin olvidar que estaba abierta
        self.console_window.closed.disconnect()
        if self.console_window.isVisible():
            self._save_console_geometry()
            self.console_window.close()
        self.save_app_session()
        for _, psu_ctrl, _ in self.docks:
            psu_ctrl.disconnect_psu()
        # Espera acotada a que cada hilo cierre su sesión VISA; si alguno sigue bloqueado en la red,
        # main() sale igual con os._exit (nunca terminate(), nunca un cuelgue al cerrar)
        deadline = time.monotonic() + self.SHUTDOWN_TIMEOUT_S
        self.unclean_shutdown = False
        for _, psu_ctrl, _ in self.docks:
            remaining_ms = (deadline - time.monotonic()) * 1000
            if not psu_ctrl.wait_for_shutdown(remaining_ms):
                self.unclean_shutdown = True
        event.accept()

def screen_signature() -> str:
    """Identifica la configuración de monitores (nombre, resolución lógica y escala)."""
    parts = []
    for scr in QGuiApplication.screens():
        g = scr.geometry()
        parts.append(f"{scr.name()}:{g.width()}x{g.height()}@{scr.devicePixelRatio():.2f}")
    return "|".join(sorted(parts))


def load_app_icon(app: QApplication):
    possible_paths = [
        "/usr/share/pixmaps/powersupply-ctrl.png",
        os.path.join(getattr(sys, '_MEIPASS', get_base_dir()), "icon.png"),
        os.path.join(get_base_dir(), "icon.png"),
        "icon.png"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            app.setWindowIcon(QIcon(path))
            break


def main():
    # 1. ESTABLECER EL WORKING DIRECTORY EN LA CARPETA DONDE ESTÁN LOS ARCHIVOS/PERFILES
    base_dir = get_base_dir()
    try:
        os.chdir(base_dir)
    except Exception as e:
        print(f"Advertencia: No se pudo cambiar el CWD a {base_dir}: {e}")

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Escala fraccional exacta (125%, 150%...) igual en todos los monitores/PCs
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setDesktopFileName("powersupply-ctrl")
    # Estilo y paleta propios: mismo aspecto en cualquier PC, independiente del tema del escritorio
    app.setStyle("Fusion")
    colors.load_themes()
    app.setPalette(colors.palette())
    load_app_icon(app)

    sigint_timer = QTimer()
    sigint_timer.start(500)
    sigint_timer.timeout.connect(lambda: None)

    window = MultiPSUController()
    if not app.windowIcon().isNull():
        window.setWindowIcon(app.windowIcon())

    window.show()
    code = app.exec()
    if getattr(window, "unclean_shutdown", False):
        os._exit(code)  # un hilo quedó bloqueado en la red: salir sin esperar ni abortar por QThread vivo
    sys.exit(code)


if __name__ == '__main__':
    main()