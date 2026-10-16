"""Configuración común de pytest: rutas, Qt sin pantalla y carpeta config/ aislada por prueba."""
import os
import sys
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# PSU_MODULE_DIR=build/cython ejecuta las pruebas contra los módulos compilados (.so)
COMPILED_DIR = os.environ.get("PSU_MODULE_DIR")
if COMPILED_DIR:
    sys.path.insert(0, os.path.abspath(os.path.join(ROOT, COMPILED_DIR)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Cada prueba trabaja sobre una copia de config/ para no tocar los archivos reales."""
    cfg = tmp_path / "config"
    shutil.copytree(os.path.join(ROOT, "config"), cfg,
                    ignore=shutil.ignore_patterns("session.ini", "*.tmp"))
    monkeypatch.setenv("PSU_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("PSU_DATA_DIR", str(tmp_path / "recordings"))
    return cfg


class FakeInstrument:
    """Sustituto de un recurso PyVISA: registra escrituras y responde queries por tabla."""

    def __init__(self, responses=None):
        self.writes = []
        self.responses = responses or {}
        self.closed = False

    def write(self, cmd):
        self.writes.append(cmd)

    def query(self, cmd):
        self.writes.append(cmd)
        value = self.responses.get(cmd, "0")
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True
