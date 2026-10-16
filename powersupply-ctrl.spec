# -*- mode: python ; coding: utf-8 -*-
# Empaqueta SOLO los módulos compilados con Cython (build/cython/*.so) y solo las dependencias que usa la app.
# Lo invoca build_deb.sh, que antes compila:  python setup_cython.py build_ext --build-lib build/cython --build-temp build/cython/tmp
import os
import re
import shutil
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.depend.bindepend import get_imports

CY_DIR = os.path.abspath('build/cython')
APP_MODULES = ['main', 'hmi', 'powsupply', 'eload', 'recorder', 'config', 'colors', 'paths']

# El lanzador es el único .py; se copia junto a los .so para que el análisis no vea el código fuente
shutil.copy('launcher.py', os.path.join(CY_DIR, 'launcher.py'))


def _no_tests(name):
    return 'testsuite' not in name and '.tests' not in name


# Iconos de pyqtgraph (menú contextual de la gráfica). Sin ejemplos ni datos de colormaps.
datas = collect_data_files('pyqtgraph', includes=['icons/*.png', 'icons/*.svg', 'icons/**/*.png', 'icons/**/*.svg'])
# pyvisa consulta su versión instalada vía importlib.metadata
datas += copy_metadata('pyvisa') + copy_metadata('pyvisa_py')

# PyInstaller no puede analizar los imports dentro de un .so: se declaran explícitamente
hiddenimports = APP_MODULES + [
    'yaml', 'numpy', 'pyqtgraph',
    'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets', 'PyQt6.sip',
]
# Backend '@py' (TCPIP INSTR/SOCKET/HiSLIP). Sin las suites de pruebas que traía collect_all.
hiddenimports += collect_submodules('pyvisa', filter=_no_tests)
hiddenimports += collect_submodules('pyvisa_py', filter=_no_tests)

EXCLUDES = [
    # Otros bindings de Qt / GUI
    'tkinter', 'PyQt5', 'PySide2', 'PySide6',
    # Módulos de PyQt6 que la app no carga
    'PyQt6.QtNetwork', 'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets', 'PyQt6.QtMultimedia', 'PyQt6.QtQml',
    'PyQt6.QtQuick', 'PyQt6.QtWebEngineCore', 'PyQt6.QtSql', 'PyQt6.QtPrintSupport', 'PyQt6.QtBluetooth',
    # Partes opcionales de pyqtgraph que no se usan
    'pyqtgraph.opengl', 'pyqtgraph.examples', 'pyqtgraph.jupyter', 'pyqtgraph.canvas', 'pyqtgraph.flowchart',
    'OpenGL', 'PIL', 'matplotlib', 'scipy', 'h5py', 'cupy', 'numba', 'pandas', 'IPython', 'jupyter_rfb',
    # Backends PyVISA no usados (solo TCPIP) y suites de pruebas
    'usb', 'serial', 'gpib', 'gpib_ctypes', 'zeroconf', 'psutil', 'pyvisa.testsuite', 'pyvisa_py.testsuite',
    # Stdlib innecesaria (OpenSSL: la comunicación SCPI no usa TLS)
    'ssl', '_ssl', '_hashlib', 'unittest', 'test', 'sqlite3', 'curses', 'lib2to3', 'idlelib', 'ensurepip',
    'venv', 'asyncio', 'readline',
]

a = Analysis(
    [os.path.join(CY_DIR, 'launcher.py')],
    pathex=[CY_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=2,
)

# ----------------------------------------------------------------------------------------------
# Poda de Qt: PyInstaller copia todos los plugins de QtGui. La app solo necesita:
#   platforms: xcb (X11), wayland y offscreen (pruebas), integración GL de xcb, shells de Wayland,
#   entrada de teclado (ibus/compose), tema xdg-portal e iconos/imágenes SVG de pyqtgraph.
# Fuera: gtk3 (arrastra GTK/cairo/pango), pdf (QtPdf+QtNetwork), vnc, eglfs, linuxfb, evdev, etc.
# Las traducciones de Qt no se cargan (la app usa config/translations.json).
# ----------------------------------------------------------------------------------------------
QT_PREFIX = 'PyQt6/Qt6/'
DROP_PATTERNS = [re.compile(p) for p in (
    r'^PyQt6/Qt6/translations/',
    r'^PyQt6/Qt6/plugins/(egldeviceintegrations|generic)/',
    r'^PyQt6/Qt6/plugins/platforms/lib(qeglfs|qlinuxfb|qminimal|qminimalegl|qvnc|qvkkhrdisplay)\.so$',
    r'^PyQt6/Qt6/plugins/platformthemes/libqgtk3\.so$',
    r'^PyQt6/Qt6/plugins/imageformats/(?!libqsvg\.so$)',
    # Wayland: solo xdg-shell (GNOME/KDE) + EGL. Fuera shells de compositores embebidos/automotrices
    r'^PyQt6/Qt6/plugins/wayland-shell-integration/(?!libxdg-shell\.so$)',
    r'^PyQt6/Qt6/plugins/wayland-graphics-integration-client/(?!libqt-plugin-wayland-egl\.so$)',
)]


def _dropped(dest):
    dest = dest.replace(os.sep, '/')
    return any(p.search(dest) for p in DROP_PATTERNS)


a.binaries = [e for e in a.binaries if not _dropped(e[0])]
a.datas = [e for e in a.datas if not _dropped(e[0])]

# Recalcula qué librerías compartidas siguen siendo necesarias (cierre de dependencias ELF DT_NEEDED)
# partiendo de las extensiones Python y los plugins Qt que quedan. Lo que solo usaban los plugins
# eliminados (libgtk-3, libQt6Pdf, libQt6Network, krb5...) desaparece.
ALWAYS_KEEP = re.compile(r'^(libpython3|libgcc_s)')  # abiertas por dlopen (bootloader / glibc)


def _is_root(dest):
    d = dest.replace(os.sep, '/')
    name = os.path.basename(d)
    return ('/plugins/' in d or '.cpython-' in name or name.endswith('.abi3.so')
            or d.startswith('python3') or 'lib-dynload' in d)


by_name = {os.path.basename(e[0]): e for e in a.binaries}
needed, queue = set(), [e for e in a.binaries if _is_root(e[0])]
while queue:
    entry = queue.pop()
    name = os.path.basename(entry[0])
    if name in needed:
        continue
    needed.add(name)
    for dep in get_imports(entry[1]):
        dep_name = os.path.basename(dep[0] if isinstance(dep, tuple) else dep)
        if dep_name in by_name and dep_name not in needed:
            queue.append(by_name[dep_name])

a.binaries = [e for e in a.binaries
              if os.path.basename(e[0]) in needed or ALWAYS_KEEP.match(os.path.basename(e[0]))]
kept_names = {os.path.basename(e[0]) for e in a.binaries}
# Enlaces simbólicos de nivel superior que apuntaban a librerías ya eliminadas
a.datas = [e for e in a.datas if e[2] != 'SYMLINK' or os.path.basename(e[0]) in kept_names]
a.binaries = [e for e in a.binaries if e[2] != 'SYMLINK' or os.path.basename(e[0]) in kept_names]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='powersupply-ctrl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,  # UPX suele corromper las librerías de Qt
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    upx_exclude=[],
    name='powersupply-ctrl',
)
