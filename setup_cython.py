"""
Compila los módulos de la aplicación a extensiones nativas (.so) con Cython.

Uso:  python setup_cython.py build_ext --build-lib build/cython --build-temp build/cython/tmp

Resultado: build/cython/*.so  (código máquina, sin bytecode .pyc, sin docstrings, símbolos eliminados)
Los .c generados (build/cython/c) equivalen al código fuente: build_deb.sh los borra tras compilar.
"""
from setuptools import setup, Extension
from Cython.Build import cythonize
import Cython.Compiler.Options as CyOptions

MODULES = ["main", "hmi", "powsupply", "eload", "recorder", "config", "colors", "paths"]

# No incrustar docstrings ni comentarios con el código fuente original
CyOptions.docstrings = False
CyOptions.emit_code_comments = False

extensions = [
    Extension(
        name,
        [f"{name}.py"],
        extra_compile_args=["-O2", "-fvisibility=hidden"],
        extra_link_args=["-s"],  # strip: sin tabla de símbolos de depuración
    )
    for name in MODULES
]

setup(
    name="powersupply-ctrl",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": 3,
            "embedsignature": False,
            "binding": True,  # necesario para pyqtSlot/introspección de PyQt
            "always_allow_keywords": True,
        },
        build_dir="build/cython/c",
        quiet=True,
    ),
)
