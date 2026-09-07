"""Configuracion por entorno: valores por defecto y validacion estricta (seccion 9.1)."""

import pytest

from doors_kb.config import Settings
from doors_kb.errors import ConfigurationError


def test_valores_por_defecto_coinciden_con_la_especificacion():
    """Sin entorno, los valores son los documentados en la seccion 9.1."""
    s = Settings.from_env({})

    assert s.prog_id == "DOORS.Application"
    assert s.start_timeout_seconds == 30  # RNF-003
    assert s.dxl_timeout_seconds == 90  # RNF-004
    assert s.hard_max_response_chars == 500_000  # RNF-010
    assert s.dxl_run_limit_cycles == 0  # RNF-007: watchdog DXL desactivado
    assert s.sync_attributes == ("Object Heading", "Object Text")  # RF-014


def test_lista_de_atributos_conserva_los_espacios_internos():
    """Los nombres de DOORS llevan espacios: solo se recortan los extremos."""
    s = Settings.from_env({"DOORS_SYNC_ATTRIBUTES": " Object Heading , Object Text ,Estado "})

    assert s.sync_attributes == ("Object Heading", "Object Text", "Estado")


def test_un_entero_mal_escrito_impide_arrancar():
    """Un timeout invalido falla al arrancar, no a mitad de una sincronizacion."""
    with pytest.raises(ConfigurationError, match="DOORS_DXL_TIMEOUT_SECONDS"):
        Settings.from_env({"DOORS_DXL_TIMEOUT_SECONDS": "noventa"})


def test_un_timeout_de_cero_se_rechaza():
    with pytest.raises(ConfigurationError, match=">= 1"):
        Settings.from_env({"DOORS_START_TIMEOUT_SECONDS": "0"})


def test_resolve_module_path_prefiere_el_modulo_explicito():
    """RF-006: una tool puede consultar otro modulo sin reiniciar el proceso."""
    s = Settings.from_env({"DOORS_MODULE_PATH": "/Proyecto/Por defecto"})

    assert s.resolve_module_path("/Proyecto/Otro") == "/Proyecto/Otro"
    assert s.resolve_module_path(None) == "/Proyecto/Por defecto"


def test_resolve_module_path_sin_modulo_da_un_error_accionable():
    with pytest.raises(ConfigurationError, match="DOORS_MODULE_PATH"):
        Settings.from_env({}).resolve_module_path(None)
