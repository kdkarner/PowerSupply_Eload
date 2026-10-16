import os
import yaml
import pyvisa
import threading
from contextlib import contextmanager

from paths import config_path

VERSION = "PSU VERSION 3.3 - OOP WITH IP_CONFIG & CHANNEL (THREAD-SAFE)"

# Todas las instancias (hasta 6 docks, hilo GUI y workers) comparten el mismo YAML
_CONFIG_FILE_LOCK = threading.Lock()

ON_RESPONSES = ('1', 'ON', 'OUT 1', 'ENABLE', 'OUTPUT ON', 'STAT 1')


class InstrumentBusyError(RuntimeError):
    """No se obtuvo el acceso exclusivo al instrumento dentro del tiempo máximo."""

class IPConfig:
    """Administra la configuración de red y protocolo de comunicación del instrumento."""
    def __init__(self, ip: str = "192.168.150.18", profile: str = "E36155A_1", conn_type: str = "INSTR", port: int = 5025):
        self.ip: str = ip
        self.active_profile: str = profile
        self.conn_type: str = conn_type
        self.port: int = port

    def update(self, ip: str = None, profile: str = None, conn_type: str = None, port: int = None):
        """Actualiza los parámetros de red si se proporcionan nuevos valores."""
        if ip: self.ip = ip
        if profile: self.active_profile = profile
        if conn_type: self.conn_type = conn_type
        if port: self.port = int(port)

class Channel:
    """Representa el estado dinámico y setpoints de un canal individual."""

    def __init__(self, ch_num: int):
        self.ch_num: int = int(ch_num)

        # Setpoints y límites
        self.SET_Volt: float = 0.0
        self.SET_MaxI: float = 0.0
        self.max_v: float = 60.0
        self.max_i: float = 10.0

        # Lecturas en tiempo real
        self.ReadV: float = 0.0
        self.ReadI: float = 0.0
        self.ReadPower: float = 0.0
        self.ON: bool = False

    def update_telemetry(self, volt: float, curr: float, state: bool):
        """Actualiza la telemetría en tiempo real del canal."""
        self.ReadV = max(0.0, round(volt, 3))
        self.ReadI = max(0.0, round(curr, 3))
        self.ReadPower = round(self.ReadV * self.ReadI, 3)
        self.ON = state


class PowerSupply:
    LOCK_TIMEOUT_S = 10.0   # nunca esperar indefinidamente el acceso al instrumento
    VISA_TIMEOUT_MS = 2500

    def __init__(self, config_file: str = None):
        self.config_file: str = config_file or config_path("config.yaml")

        # Instancia de configuración de red
        self.net_config = IPConfig()

        # Diccionario de canales {ch_num: Channel}
        self.channels: dict[int, Channel] = {}
        self.channel_num: int = 1

        self.psu = None
        self.rm = None
        self.connected: bool = False
        self.config: dict = {}

        # Candado de sincronización VISA (Thread-Safety)
        self._visa_lock = threading.Lock()
        self._abort = threading.Event()  # permite cortar la telemetría entre consultas al desconectar

        self.load_config()

    # --- PROPIEDADES DE RED (RETROCOMPATIBILIDAD) ---

    @property
    def ip(self) -> str:
        return self.net_config.ip

    @ip.setter
    def ip(self, val: str):
        self.net_config.ip = val

    @property
    def active_profile(self) -> str:
        return self.net_config.active_profile

    @active_profile.setter
    def active_profile(self, val: str):
        self.net_config.active_profile = val

    @property
    def conn_type(self) -> str:
        return self.net_config.conn_type

    @conn_type.setter
    def conn_type(self, val: str):
        self.net_config.conn_type = val

    @property
    def port(self) -> int:
        return self.net_config.port

    @port.setter
    def port(self, val: int):
        self.net_config.port = int(val)

    # --- PROPIEDADES DEL CANAL ACTIVO (RETROCOMPATIBILIDAD) ---

    @property
    def active_channel(self) -> Channel:
        """Retorna el objeto Channel activo actual (lo crea si no existe)."""
        if self.channel_num not in self.channels:
            self.channels[self.channel_num] = Channel(self.channel_num)
        return self.channels[self.channel_num]

    @property
    def channel(self) -> int:
        return self.channel_num

    @channel.setter
    def channel(self, val: int):
        self.channel_num = int(val)

    @property
    def SET_Volt(self) -> float:
        return self.active_channel.SET_Volt

    @SET_Volt.setter
    def SET_Volt(self, val: float):
        self.active_channel.SET_Volt = val

    @property
    def SET_MaxI(self) -> float:
        return self.active_channel.SET_MaxI

    @SET_MaxI.setter
    def SET_MaxI(self, val: float):
        self.active_channel.SET_MaxI = val

    @property
    def max_v(self) -> float:
        return self.active_channel.max_v

    @max_v.setter
    def max_v(self, val: float):
        self.active_channel.max_v = val

    @property
    def max_i(self) -> float:
        return self.active_channel.max_i

    @max_i.setter
    def max_i(self, val: float):
        self.active_channel.max_i = val

    @property
    def ReadV(self) -> float:
        return self.active_channel.ReadV

    @property
    def ReadI(self) -> float:
        return self.active_channel.ReadI

    @property
    def ReadPower(self) -> float:
        return self.active_channel.ReadPower

    @property
    def ON(self) -> bool:
        return self.active_channel.ON

    @ON.setter
    def ON(self, val: bool):
        self.active_channel.ON = val

    # --- CONFIGURACIÓN Y PERFILES ---

    def load_config(self):
        """Carga la configuración desde el archivo YAML y ajusta el perfil activo."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config = yaml.safe_load(f) or {}

                prof_name = self.config.get('last_active_profile', self.active_profile)
                self.net_config.active_profile = prof_name
                self.load_profile_settings(prof_name)
            except Exception as e:
                print(f"[ERROR] No se pudo leer {self.config_file}: {e}")

    def get_current_profile(self) -> dict:
        """Retorna el diccionario de configuración del perfil activo."""
        profiles = self.config.get('profiles', {})
        return profiles.get(self.active_profile, {})

    def is_multichannel(self) -> bool:
        """Indica si el perfil activo soporta múltiples canales."""
        prof = self.get_current_profile()
        return prof.get('is_multichannel', False)

    def load_profile_settings(self, profile_key: str, channel: int = None):
        """Carga datos de IP, puerto, lista de canales y parámetros V/I desde el YAML."""
        self.net_config.active_profile = profile_key
        prof = self.get_current_profile()

        self.net_config.update(
            ip=prof.get('ip', self.ip),
            conn_type=prof.get('conn_type', self.conn_type),
            port=prof.get('port', self.port)
        )

        self.channels.clear()

        if self.is_multichannel():
            channels_dict = prof.get('channels', {})
            for ch_key, ch_data in channels_dict.items():
                c_num = int(ch_key)
                ch_obj = Channel(c_num)
                ch_obj.SET_Volt = float(ch_data.get('last_v', 0.0))
                ch_obj.SET_MaxI = float(ch_data.get('last_i', 0.0))
                ch_obj.max_v = float(ch_data.get('max_v', 60.0))
                ch_obj.max_i = float(ch_data.get('max_i', 10.0))
                self._load_channel_extra(ch_obj, ch_data)
                self.channels[c_num] = ch_obj

            selected_ch = channel if channel is not None else prof.get('active_channel', 1)
            self.channel_num = int(selected_ch)
            if self.channel_num not in self.channels:
                self.channels[self.channel_num] = Channel(self.channel_num)
        else:
            self.channel_num = 1
            ch_obj = Channel(1)
            ch_obj.SET_Volt = float(prof.get('last_v', 0.0))
            ch_obj.SET_MaxI = float(prof.get('last_i', 0.0))
            ch_obj.max_v = float(prof.get('max_v', 60.0))
            ch_obj.max_i = float(prof.get('max_i', 10.0))
            self._load_channel_extra(ch_obj, prof)
            self.channels[1] = ch_obj

    # Hooks para instrumentos derivados (p. ej. cargas electrónicas) con datos propios por canal
    def _load_channel_extra(self, ch_obj: Channel, ch_data: dict):
        pass

    def _channel_extra_to_save(self, ch_obj: Channel) -> dict:
        return {}

    def select_channel(self, channel: int):
        """Cambia el canal activo y envía el comando SCPI si está conectado."""
        self.channel_num = int(channel)
        if self.channel_num not in self.channels:
            self.channels[self.channel_num] = Channel(self.channel_num)

        if self.connected and self.psu:
            try:
                with self._io():
                    self._select(self.get_current_profile(), self.channel_num)
            except Exception as e:
                print(f"[ERROR] Error SCPI al seleccionar canal {self.channel_num}: {e}")

        self.save_config()

    def save_config(self):
        """
        Persiste el perfil activo en el YAML. Relee el archivo y fusiona solo este perfil para no
        pisar los cambios de otras fuentes, y escribe de forma atómica (temporal + os.replace).
        """
        with _CONFIG_FILE_LOCK:
            self._save_config_locked()

    def _save_config_locked(self):
        try:
            disk_config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    disk_config = yaml.safe_load(f) or {}
            if disk_config.get('profiles'):
                self.config = disk_config

            self.config['last_active_profile'] = self.active_profile

            if 'profiles' in self.config and self.active_profile in self.config['profiles']:
                prof = self.config['profiles'][self.active_profile]
                prof['ip'] = self.ip
                prof['conn_type'] = self.conn_type
                prof['port'] = self.port

                if self.is_multichannel():
                    prof['active_channel'] = self.channel_num
                    if 'channels' not in prof:
                        prof['channels'] = {}

                    for ch_num, ch_obj in self.channels.items():
                        entry = {
                            'last_v': round(ch_obj.SET_Volt, 3),
                            'last_i': round(ch_obj.SET_MaxI, 3),
                            'max_v': ch_obj.max_v,
                            'max_i': ch_obj.max_i
                        }
                        entry.update(self._channel_extra_to_save(ch_obj))
                        prof['channels'][ch_num] = entry
                else:
                    ch_obj = self.active_channel
                    prof['last_v'] = round(ch_obj.SET_Volt, 3)
                    prof['last_i'] = round(ch_obj.SET_MaxI, 3)
                    prof['max_v'] = ch_obj.max_v
                    prof['max_i'] = ch_obj.max_i
                    prof.update(self._channel_extra_to_save(ch_obj))

            tmp_file = f"{self.config_file}.tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.config_file)
        except Exception as e:
            print(f"[ERROR] No se pudo guardar {self.config_file}: {e}")

    # --- COMUNICACIÓN VISA ---
    # Todo el I/O con el instrumento lo ejecuta el hilo del instrumento (worker); el hilo de la interfaz
    # nunca toma el lock. _io() además impone un tiempo máximo: si no hay acceso, error en vez de cuelgue.

    @contextmanager
    def _io(self):
        if not self._visa_lock.acquire(timeout=self.LOCK_TIMEOUT_S):
            raise InstrumentBusyError("Comunicación ocupada: el instrumento no respondió a tiempo")
        try:
            yield
        finally:
            self._visa_lock.release()

    def request_abort(self):
        """Pide cortar la telemetría en curso (seguro desde cualquier hilo, no toma el lock)."""
        self._abort.set()

    def _select(self, prof: dict, ch_num: int):
        if self.is_multichannel() and 'cmd_select_ch' in prof:
            self.psu.write(prof['cmd_select_ch'].format(ch=ch_num))

    @staticmethod
    def _is_on(resp: str) -> bool:
        return resp.strip().upper() in ON_RESPONSES

    def connect(self, ip: str = None, profile_key: str = None, conn_type: str = None, port: int = None) -> tuple[bool, str]:
        """Abre la sesión VISA utilizando la configuración de red."""
        self.net_config.update(ip=ip, profile=profile_key, conn_type=conn_type, port=port)
        self._abort.clear()

        if self.conn_type == "SOCKET":
            resource_name = f"TCPIP0::{self.ip}::{self.port}::SOCKET"
        else:
            resource_name = f"TCPIP0::{self.ip}::inst0::INSTR"

        try:
            with self._io():
                if self.rm is None:
                    try:
                        self.rm = pyvisa.ResourceManager('@py')
                    except Exception:
                        self.rm = pyvisa.ResourceManager()

                self.psu = self.rm.open_resource(resource_name)
                self.psu.read_termination = '\n'
                self.psu.write_termination = '\n'
                self.psu.timeout = self.VISA_TIMEOUT_MS
                self.connected = True
                self._select(self.get_current_profile(), self.channel_num)
        except pyvisa.VisaIOError as e:
            self.connected = False
            return False, f"Error PyVISA: {e.description}"
        except Exception as e:
            self.connected = False
            return False, f"Error de conexión: {str(e)}"

        self.save_config()
        return True, f"Conectado a {resource_name}"

    def disconnect(self):
        """Cierra la conexión VISA. Si el lock no se libera a tiempo, cierra igual (nunca se cuelga)."""
        self._abort.set()
        acquired = self._visa_lock.acquire(timeout=self.LOCK_TIMEOUT_S)
        try:
            if self.psu:
                try:
                    self.psu.close()
                except Exception:
                    pass
            self.connected = False
            self.psu = None
        finally:
            if acquired:
                self._visa_lock.release()

    # --- SECUENCIA SEGURA DE CONEXIÓN ---

    def verify_identity(self) -> tuple[bool, str]:
        """
        *IDN? del equipo. Si el perfil define 'idn_match' y la respuesta no lo contiene, el equipo conectado
        no es el del perfil (IP equivocada, perfil equivocado) y no se debe operar.
        """
        prof = self.get_current_profile()
        expected = str(prof.get('idn_match', '')).strip()
        try:
            with self._io():
                idn = self.psu.query(prof.get('cmd_idn', '*IDN?')).strip()
        except Exception as e:
            if expected:
                return False, f"El equipo no respondió a *IDN? ({e}); se esperaba '{expected}'"
            return True, f"Advertencia: el equipo no respondió a *IDN? ({e})"
        if expected and expected.upper() not in idn.upper():
            return False, f"Equipo incorrecto: *IDN? = '{idn}', el perfil espera '{expected}'"
        if not expected:
            return True, f"IDN: {idn} (el perfil no define 'idn_match': identidad no verificada)"
        return True, f"IDN verificado: {idn}"

    def all_outputs_off(self) -> tuple[bool, str]:
        """Apaga la salida/entrada de TODOS los canales y lo verifica con la consulta de estado."""
        prof = self.get_current_profile()
        still_on, unverified = [], []
        try:
            with self._io():
                for ch_num in sorted(self.channels):
                    self._select(prof, ch_num)
                    self.psu.write(prof['cmd_out_off'].format(ch=ch_num))
                for ch_num in sorted(self.channels):
                    try:
                        self._select(prof, ch_num)
                        resp = self.psu.query(prof.get('cmd_query_out', 'OUTP?').format(ch=ch_num))
                        if self._is_on(resp):
                            still_on.append(ch_num)
                        else:
                            self.channels[ch_num].ON = False
                    except Exception:
                        unverified.append(ch_num)
                self._select(prof, self.channel_num)
        except Exception as e:
            return False, f"No se pudo apagar las salidas: {e}"

        if still_on:
            return False, f"Las salidas siguen encendidas en CH{still_on} tras enviar OFF"
        if unverified:
            return False, f"No se pudo verificar el apagado en CH{unverified} (revisar 'cmd_query_out' del perfil)"
        return True, f"Salidas apagadas y verificadas en CH{sorted(self.channels)}"

    def read_setpoints(self) -> tuple[bool, str]:
        """Lee del equipo los setpoints programados (V / I límite) de cada canal y actualiza el modelo."""
        prof = self.get_current_profile()
        cmd_v = prof.get('cmd_query_set_v', 'VOLT?')
        cmd_i = prof.get('cmd_query_set_i', 'CURR?')
        read, failed = [], []
        try:
            with self._io():
                for ch_num, ch_obj in sorted(self.channels.items()):
                    try:
                        self._select(prof, ch_num)
                        v = float(self.psu.query(cmd_v.format(ch=ch_num)).strip())
                        i = float(self.psu.query(cmd_i.format(ch=ch_num)).strip())
                        ch_obj.SET_Volt, ch_obj.SET_MaxI = v, i
                        read.append(f"CH{ch_num} V={v} I={i}")
                    except Exception:
                        failed.append(ch_num)
                self._select(prof, self.channel_num)
        except Exception as e:
            return False, f"No se pudieron leer los setpoints del equipo: {e}"

        if failed:
            return False, f"No se pudieron leer los setpoints de CH{failed}; se muestran los últimos guardados"
        self.save_config()
        return True, "Setpoints leídos del equipo: " + ", ".join(read)

    # --- COMANDOS ---

    def update_settings(self, volt: float, max_i: float) -> tuple[bool, str]:
        """Aplica voltaje y corriente límites en el canal activo."""
        if not self.connected or not self.psu:
            return False, "Fuente no conectada"

        ch_obj = self.active_channel
        prof = self.get_current_profile()
        ch_num = self.channel_num
        try:
            with self._io():
                self._select(prof, ch_num)
                self.psu.write(prof['cmd_set_v'].format(val=volt, ch=ch_num))
                self.psu.write(prof['cmd_set_i'].format(val=max_i, ch=ch_num))
        except Exception as e:
            return False, f"Error al actualizar setpoints: {e}"

        ch_obj.SET_Volt = volt
        ch_obj.SET_MaxI = max_i
        self.save_config()
        return True, f"CH{ch_num} Setpoints: V={volt}V, I={max_i}A"

    def set_output(self, state: bool) -> tuple[bool, str]:
        """Habilita o deshabilita la salida del canal activo."""
        if not self.connected or not self.psu:
            return False, "Fuente no conectada"

        prof = self.get_current_profile()
        ch_num = self.channel_num
        cmd = (prof['cmd_out_on'] if state else prof['cmd_out_off']).format(ch=ch_num)
        try:
            with self._io():
                self._select(prof, ch_num)
                self.psu.write(cmd)
        except Exception as e:
            return False, f"Error al cambiar estado de salida: {e}"

        self.active_channel.ON = state
        return True, f"CH{ch_num} Salida {'HABILITADA' if state else 'DESHABILITADA'}"

    def check_power_on(self) -> bool:
        """Consulta si el canal activo está encendido."""
        if not self.connected or not self.psu:
            return False

        prof = self.get_current_profile()
        try:
            with self._io():
                self._select(prof, self.channel_num)
                is_on = self._is_on(self.psu.query(prof.get('cmd_query_out', 'OUTP?').format(ch=self.channel_num)))
        except Exception:
            return False
        self.active_channel.ON = is_on
        return is_on

    def read_telemetry(self) -> dict:
        """
        Lee voltaje, corriente y estado de TODOS los canales configurados
        y actualiza la telemetría individual de cada objeto Channel.
        Retorna un diccionario: { ch_num: {'ReadV': float, 'ReadI': float, 'Power': float, 'ON': bool} }
        Se interrumpe entre consultas si se pidió desconectar (request_abort).
        """
        results = {}

        if not self.connected or not self.psu:
            for ch_num, ch_obj in self.channels.items():
                results[ch_num] = {'ReadV': 0.0, 'ReadI': 0.0, 'Power': 0.0, 'ON': False}
            return results

        prof = self.get_current_profile()

        with self._io():
            for ch_num, ch_obj in list(self.channels.items()):
                if self._abort.is_set():
                    break
                v_val = 0.0
                i_val = 0.0
                on_state = ch_obj.ON

                try:
                    self._select(prof, ch_num)

                    try:
                        v_val = float(self.psu.query(prof['cmd_meas_v'].format(ch=ch_num)).strip())
                    except Exception:
                        pass

                    if self._abort.is_set():
                        break
                    try:
                        i_val = float(self.psu.query(prof['cmd_meas_i'].format(ch=ch_num)).strip())
                    except Exception:
                        pass

                    if self._abort.is_set():
                        break
                    try:
                        cmd_out = prof.get('cmd_query_out', 'OUTP?').format(ch=ch_num)
                        on_state = self._is_on(self.psu.query(cmd_out))
                    except Exception:
                        pass

                except Exception as e:
                    print(f"[ERROR Telemetría] CH{ch_num}: {e}")

                ch_obj.update_telemetry(v_val, i_val, on_state)

                results[ch_num] = {
                    'ReadV': ch_obj.ReadV,
                    'ReadI': ch_obj.ReadI,
                    'Power': ch_obj.ReadPower,
                    'ON': ch_obj.ON
                }

            # Restaurar la selección al canal activo seleccionado por el usuario en la UI
            if not self._abort.is_set() and self.channel_num in self.channels:
                try:
                    self._select(prof, self.channel_num)
                except Exception:
                    pass

        return results
