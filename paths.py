"""
Archivo: paths.py
Descripción: Resolución centralizada de rutas de la aplicación y de la carpeta de configuración.

- Desarrollo (no empaquetado): se usa directamente <proyecto>/config/
- Instalado (PyInstaller en /opt): /opt es de solo lectura para el usuario, por lo que
  la configuración se copia la primera vez a ~/.config/powersupply-ctrl/ y se trabaja allí.
- La variable de entorno PSU_CONFIG_DIR fuerza otra carpeta (usada por las pruebas).
"""

import os
import sys
import shutil

APP_NAME = "powersupply-ctrl"
CONFIG_DIRNAME = "config"


def get_base_dir() -> str:
    """Directorio de la aplicación (resuelve el symlink /usr/bin -> /opt en modo empaquetado)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.realpath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_default_config_dir() -> str:
    """Carpeta con los archivos de configuración por defecto que se distribuyen con la app."""
    return os.path.join(get_base_dir(), CONFIG_DIRNAME)


def get_config_dir() -> str:
    """Carpeta escribible donde se leen y guardan los archivos de configuración."""
    override = os.environ.get("PSU_CONFIG_DIR")
    if override:
        path = override
    elif getattr(sys, 'frozen', False):
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = os.path.join(xdg, APP_NAME)
    else:
        path = get_default_config_dir()

    os.makedirs(path, exist_ok=True)
    return path


def config_path(filename: str, seed: bool = True) -> str:
    """
    Ruta absoluta de un archivo de configuración. Si no existe en la carpeta escribible,
    se siembra con la copia por defecto distribuida con la app (si existe).
    seed=False para archivos de estado del usuario (p. ej. session.ini), que nunca se heredan.
    """
    target = os.path.join(get_config_dir(), filename)
    if seed and not os.path.exists(target):
        default = os.path.join(get_default_config_dir(), filename)
        if os.path.exists(default) and os.path.abspath(default) != os.path.abspath(target):
            shutil.copy2(default, target)
    return target


def get_data_dir() -> str:
    """
    Carpeta escribible para datos generados (grabaciones de telemetría).
    Desarrollo: <proyecto>/recordings · Instalado: ~/.local/share/powersupply-ctrl · PSU_DATA_DIR la fuerza.
    """
    override = os.environ.get("PSU_DATA_DIR")
    if override:
        path = override
    elif getattr(sys, 'frozen', False):
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        path = os.path.join(xdg, APP_NAME)
    else:
        path = os.path.join(get_base_dir(), "recordings")

    os.makedirs(path, exist_ok=True)
    return path
