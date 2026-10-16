"""
Archivo: recorder.py
Descripción: Grabación de telemetría de todos los instrumentos (Grabar / Pausa / Detener / Exportar).
             Cada muestra se escribe directamente a un CSV en disco: no crece la memoria en pruebas largas
             y los datos sobreviven a un cierre inesperado. Exportar copia el archivo al destino elegido.
"""

import csv
import os
import shutil
import time
from datetime import datetime

from paths import get_data_dir

IDLE, RECORDING, PAUSED = "idle", "recording", "paused"

CSV_HEADER = ["timestamp", "elapsed_s", "instrument", "type", "profile", "channel",
              "voltage_V", "current_A", "power_W", "output_on"]


class DataRecorder:
    def __init__(self, directory: str = None):
        self.directory = directory
        self.state = IDLE
        self.file_path = None
        self.samples = 0
        self._fh = None
        self._writer = None
        self._active_since = None  # inicio del tramo activo actual (monotónico)
        self._elapsed_before = 0.0  # tiempo grabado en tramos anteriores (excluye pausas)

    # --- ESTADO ---

    def start(self) -> str:
        """Inicia una grabación nueva, o reanuda si estaba en pausa. Devuelve 'started' / 'resumed' / ''."""
        if self.state == RECORDING:
            return ""
        if self.state == PAUSED:
            self.state = RECORDING
            self._active_since = time.monotonic()
            return "resumed"

        folder = self.directory or get_data_dir()
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file_path = os.path.join(folder, f"telemetry_{stamp}.csv")
        self._fh = open(self.file_path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(CSV_HEADER)
        self._fh.flush()
        self.samples = 0
        self._elapsed_before = 0.0
        self._active_since = time.monotonic()
        self.state = RECORDING
        return "started"

    def pause(self) -> bool:
        if self.state != RECORDING:
            return False
        self._elapsed_before = self.elapsed()
        self._active_since = None
        self.state = PAUSED
        return True

    def stop(self) -> bool:
        if self.state == IDLE:
            return False
        self._elapsed_before = self.elapsed()
        self._active_since = None
        if self._fh:
            self._fh.close()
        self._fh = None
        self._writer = None
        self.state = IDLE
        return True

    def elapsed(self) -> float:
        running = time.monotonic() - self._active_since if self._active_since is not None else 0.0
        return self._elapsed_before + running

    def has_data(self) -> bool:
        return bool(self.file_path) and os.path.exists(self.file_path) and self.samples > 0

    # --- DATOS ---

    def add_samples(self, instrument: str, kind: str, profile: str, data: dict) -> int:
        """data: {canal: {'ReadV', 'ReadI', 'Power', 'ON'}} tal como lo entrega la telemetría."""
        if self.state != RECORDING or not self._writer:
            return 0
        now = datetime.now().isoformat(timespec="milliseconds")
        elapsed = round(self.elapsed(), 3)
        rows = 0
        for ch_num, ch in sorted(data.items()):
            self._writer.writerow([
                now, elapsed, instrument, kind, profile, ch_num,
                ch.get('ReadV', 0.0), ch.get('ReadI', 0.0), ch.get('Power', 0.0), int(bool(ch.get('ON', False))),
            ])
            rows += 1
        self._fh.flush()
        self.samples += rows
        return rows

    def export(self, destination: str) -> str:
        if not self.file_path or not os.path.exists(self.file_path):
            raise FileNotFoundError("No hay datos grabados para exportar")
        if self._fh:
            self._fh.flush()
        shutil.copyfile(self.file_path, destination)
        return destination
