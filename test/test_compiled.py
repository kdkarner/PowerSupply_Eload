"""Verifica que, en modo compilado, se cargan las extensiones nativas y no el código fuente."""
import importlib
import os

import pytest

from conftest import COMPILED_DIR

MODULES = ["main", "hmi", "powsupply", "eload", "recorder", "config", "colors", "paths"]

pytestmark = pytest.mark.skipif(not COMPILED_DIR, reason="Solo con PSU_MODULE_DIR=build/cython")


@pytest.mark.parametrize("name", MODULES)
def test_module_is_native_extension(name):
    mod = importlib.import_module(name)
    assert mod.__file__.endswith(".so"), mod.__file__


def test_no_docstrings_embedded():
    import powsupply
    assert powsupply.PowerSupply.save_config.__doc__ is None
