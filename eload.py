"""
Archivo: eload.py
Descripción: Driver de cargas electrónicas DC (Keysight EL34243A y compatibles).
             Reutiliza conexión, telemetría y persistencia de PowerSupply y agrega los modos de carga
             CC / CV / CR / CP. Los comandos SCPI se definen por perfil en config/eloads.yaml.
"""

from powsupply import PowerSupply, Channel
from paths import config_path

LOAD_MODES = ("CC", "CV", "CR", "CP")
MODE_UNITS = {"CC": "A", "CV": "V", "CR": "Ω", "CP": "W"}
DEFAULT_MAX_P = 300.0
DEFAULT_MAX_R = 4000.0


class ElectronicLoad(PowerSupply):
    """Carga electrónica: la 'salida' es la entrada de la carga (INP ON/OFF)."""

    def __init__(self, config_file: str = None):
        super().__init__(config_file or config_path("eloads.yaml"))

    # --- DATOS PROPIOS POR CANAL (modo, nivel por modo, límites) ---

    @staticmethod
    def _ensure_load_fields(ch_obj: Channel):
        if not hasattr(ch_obj, "mode"):
            ch_obj.mode = "CC"
            ch_obj.levels = {m: 0.0 for m in LOAD_MODES}
            ch_obj.max_p = DEFAULT_MAX_P
            ch_obj.max_r = DEFAULT_MAX_R

    def _load_channel_extra(self, ch_obj: Channel, ch_data: dict):
        self._ensure_load_fields(ch_obj)
        mode = str(ch_data.get('last_mode', 'CC')).upper()
        ch_obj.mode = mode if mode in LOAD_MODES else "CC"
        levels = ch_data.get('last_levels') or {}
        ch_obj.levels = {m: float(levels.get(m, 0.0)) for m in LOAD_MODES}
        ch_obj.max_p = float(ch_data.get('max_p', DEFAULT_MAX_P))
        ch_obj.max_r = float(ch_data.get('max_r', DEFAULT_MAX_R))

    def _channel_extra_to_save(self, ch_obj: Channel) -> dict:
        self._ensure_load_fields(ch_obj)
        return {
            'last_mode': ch_obj.mode,
            'last_levels': {m: round(v, 4) for m, v in ch_obj.levels.items()},
            'max_p': ch_obj.max_p,
            'max_r': ch_obj.max_r,
        }

    @property
    def mode(self) -> str:
        ch = self.active_channel
        self._ensure_load_fields(ch)
        return ch.mode

    def level(self, mode: str = None) -> float:
        ch = self.active_channel
        self._ensure_load_fields(ch)
        return ch.levels.get(mode or ch.mode, 0.0)

    def mode_limit(self, mode: str) -> float:
        ch = self.active_channel
        self._ensure_load_fields(ch)
        return {"CC": ch.max_i, "CV": ch.max_v, "CR": ch.max_r, "CP": ch.max_p}[mode]

    def store_load(self, mode: str, value: float):
        """Guarda modo/nivel sin enviarlos (instrumento desconectado)."""
        ch = self.active_channel
        self._ensure_load_fields(ch)
        ch.mode = mode
        ch.levels[mode] = float(value)
        self.save_config()

    # --- COMANDOS ---

    def set_load(self, mode: str, value: float) -> tuple[bool, str]:
        """Selecciona el modo de carga del canal activo y programa su nivel."""
        mode = str(mode).upper()
        if mode not in LOAD_MODES:
            return False, f"Modo de carga inválido: {mode}"
        limit = self.mode_limit(mode)
        if value < 0 or value > limit:
            return False, f"Nivel fuera de rango: {value} {MODE_UNITS[mode]} (máx. {limit} {MODE_UNITS[mode]})"
        if not self.connected or not self.psu:
            return False, "Carga no conectada"

        prof = self.get_current_profile()
        scpi_mode = prof.get('mode_map', {}).get(mode, mode)
        level_cmds = prof.get('cmd_set_level', {})
        if 'cmd_set_mode' not in prof or mode not in level_cmds:
            return False, f"El perfil '{self.active_profile}' no define comandos para el modo {mode}"

        ch_num = self.channel_num
        try:
            with self._io():
                self.psu.write(prof['cmd_set_mode'].format(mode=scpi_mode, ch=ch_num))
                self.psu.write(level_cmds[mode].format(val=float(value), ch=ch_num))
        except Exception as e:
            return False, f"Error al programar la carga: {e}"

        self.store_load(mode, value)
        return True, f"CH{ch_num} Modo {mode} = {value} {MODE_UNITS[mode]}"

    def read_setpoints(self) -> tuple[bool, str]:
        """Lee del equipo el modo de carga y su nivel programado en cada canal."""
        prof = self.get_current_profile()
        cmd_mode = prof.get('cmd_query_mode')
        level_cmds = prof.get('cmd_query_level', {})
        if not cmd_mode or not level_cmds:
            return False, f"El perfil '{self.active_profile}' no define consultas de modo/nivel; se muestran los guardados"
        scpi_to_mode = {str(v).upper(): k for k, v in prof.get('mode_map', {}).items()}

        read, failed = [], []
        try:
            with self._io():
                for ch_num, ch_obj in sorted(self.channels.items()):
                    self._ensure_load_fields(ch_obj)
                    try:
                        resp = self.psu.query(cmd_mode.format(ch=ch_num)).strip().upper()
                        mode = next((m for scpi, m in scpi_to_mode.items() if resp.startswith(scpi[:3])), None)
                        if mode is None:
                            raise ValueError(f"modo desconocido '{resp}'")
                        level = float(self.psu.query(level_cmds[mode].format(ch=ch_num)).strip())
                        ch_obj.mode = mode
                        ch_obj.levels[mode] = level
                        read.append(f"CH{ch_num} {mode}={level} {MODE_UNITS[mode]}")
                    except Exception:
                        failed.append(ch_num)
        except Exception as e:
            return False, f"No se pudieron leer modo/nivel del equipo: {e}"

        if failed:
            return False, f"No se pudieron leer modo/nivel de CH{failed}; se muestran los últimos guardados"
        self.save_config()
        return True, "Modo y nivel leídos del equipo: " + ", ".join(read)
