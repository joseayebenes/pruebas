"""Cliente de DOORS: parseo de respuestas y errores, sin Windows ni DOORS.

No se puede probar COM aqui, pero si todo lo que lo rodea: como se interpreta lo que
devuelve DXL, que pasa cuando no devuelve JSON y que errores ve el usuario. El worker COM
se usa de verdad, con la inicializacion de COM sustituida.
"""

import json

import pytest

from doors_kb.config import Settings
from doors_kb.errors import DoorsModuleError, DoorsSessionError, DxlExecutionError
from doors_kb.sources.doors.client import DoorsComClient
from doors_kb.sources.doors.com_worker import ComWorker

ATRIBUTOS_JSON = {
    "attributes": [
        {"name": "Object Heading", "type": "Text", "is_system": True, "enum_values": []},
        {"name": "Object Text", "type": "Text", "is_system": True, "enum_values": []},
        {
            "name": "Estado",
            "type": "Enumeration",
            "is_system": False,
            "enum_values": ["Propuesto", "Aprobado"],
        },
    ]
}


class DoorsFalso:
    """Imita el objeto Automation: recibe un script en runStr y deja el JSON en result."""

    def __init__(self, respuestas: dict[str, object]) -> None:
        self.respuestas = respuestas
        self.scripts: list[str] = []
        self.result = ""

    def runStr(self, script: str) -> None:  # noqa: N802 (nombre impuesto por la API de DOORS)
        self.scripts.append(script)
        for marca, respuesta in self.respuestas.items():
            if marca in script:
                self.result = respuesta if isinstance(respuesta, str) else json.dumps(respuesta)
                return
        self.result = json.dumps({})


def _cliente(respuestas: dict[str, object]) -> tuple[DoorsComClient, DoorsFalso]:
    worker = ComWorker(inicializador=lambda: None, finalizador=lambda: None)
    cliente = DoorsComClient(Settings(module_path="/P/Reqs"), worker=worker)
    falso = DoorsFalso(respuestas)
    cliente._doors = falso  # se inyecta la sesion ya creada
    return cliente, falso


def test_los_atributos_se_parsean_con_sus_metadatos():
    """RF-011: tipo, ambito y valores de enumeracion llegan al agente."""
    cliente, _ = _cliente({"AttrDef ad": ATRIBUTOS_JSON})
    try:
        atributos = cliente.list_object_attributes("/P/Reqs")

        estado = next(a for a in atributos if a.name == "Estado")
        assert estado.type_name == "Enumeration"
        assert estado.enum_values == ("Propuesto", "Aprobado")
        assert estado.is_system is False
    finally:
        cliente.close()


def test_el_esquema_se_consulta_una_sola_vez_por_modulo():
    """Consultarlo en cada pagina multiplicaria las llamadas DXL sin aportar nada."""
    cliente, falso = _cliente({"AttrDef ad": ATRIBUTOS_JSON})
    try:
        cliente.list_object_attributes("/P/Reqs")
        cliente.list_object_attributes("/P/Reqs")

        assert len(falso.scripts) == 1
    finally:
        cliente.close()


def test_una_pagina_promueve_heading_y_texto_a_campos_propios():
    """RF-014: son los atributos de contenido predeterminados."""
    pagina_json = {
        "records": [
            {
                "absolute_number": 12,
                "identifier": "REQ-12",
                "outline_number": "2.1",
                "attributes": {
                    "Object Heading": "Timeout",
                    "Object Text": "Se cierra tras 30 s.",
                    "Estado": "Aprobado",
                },
            }
        ],
        "next_cursor": 12,
    }
    cliente, _ = _cliente({"AttrDef ad": ATRIBUTOS_JSON, '\\"records\\"': pagina_json})
    try:
        pagina = cliente.fetch_page("/P/Reqs", ["Object Heading", "Object Text", "Estado"])

        registro = pagina.records[0]
        assert registro.heading == "Timeout"
        assert registro.text == "Se cierra tras 30 s."
        assert registro.attributes == {"Estado": "Aprobado"}
        assert registro.module_path == "/P/Reqs"
        assert pagina.exhausted is False
    finally:
        cliente.close()


def test_un_cursor_nulo_indica_el_final_del_modulo():
    """RF-061: de esta senal depende que se puedan marcar ausentes como eliminados."""
    cliente, _ = _cliente(
        {"AttrDef ad": ATRIBUTOS_JSON, '\\"records\\"': {"records": [], "next_cursor": None}}
    )
    try:
        assert cliente.fetch_page("/P/Reqs", ["Object Text"]).exhausted is True
    finally:
        cliente.close()


def test_un_modulo_que_no_abre_produce_un_error_accionable():
    """Seccion 10: NO_MODULE es un error explicito, no una lista vacia."""
    cliente, _ = _cliente(
        {
            "AttrDef ad": ATRIBUTOS_JSON,
            '\\"records\\"': {"error": "NO_MODULE", "module_path": "/P/Reqs"},
        }
    )
    try:
        with pytest.raises(DoorsModuleError, match="fullName"):
            cliente.fetch_page("/P/Reqs", ["Object Text"])
    finally:
        cliente.close()


def test_una_respuesta_que_no_es_json_conserva_el_texto_original():
    """Suele ser un error del interprete DXL: sin el texto no hay forma de diagnosticarlo."""
    cliente, _ = _cliente({"AttrDef ad": "-E- DXL: <Line:12> incorrect arguments"})
    try:
        with pytest.raises(DxlExecutionError, match="incorrect arguments"):
            cliente.list_object_attributes("/P/Reqs")
    finally:
        cliente.close()


def test_un_atributo_inexistente_se_rechaza_antes_de_leer_la_pagina():
    """RF-012: la validacion va contra el esquema, no contra el valor leido."""
    cliente, falso = _cliente({"AttrDef ad": ATRIBUTOS_JSON})
    try:
        with pytest.raises(Exception, match="Atributos inexistentes"):
            cliente.fetch_page("/P/Reqs", ["Object Text", "Estdo"])

        # Solo se ejecuto el script del esquema: no se llego a pedir la pagina.
        assert len(falso.scripts) == 1
    finally:
        cliente.close()


def test_un_objeto_borrado_se_devuelve_como_ausente():
    """RF-023: los objetos borrados no son requisitos vigentes."""
    respuesta = {"record": {"absolute_number": 3, "is_deleted": True, "attributes": {}}}
    cliente, _ = _cliente({"AttrDef ad": ATRIBUTOS_JSON, '\\"record\\":': respuesta})
    try:
        assert cliente.get_requirement("/P/Reqs", 3, ["Object Text"]) is None
    finally:
        cliente.close()


def test_consultar_sin_sesion_dice_que_hacer():
    """RF-003: el primer paso siempre es abrir la sesion y autenticarse."""
    worker = ComWorker(inicializador=lambda: None, finalizador=lambda: None)
    cliente = DoorsComClient(Settings(module_path="/P/Reqs"), worker=worker)
    try:
        with pytest.raises(DoorsSessionError, match="start_doors_session"):
            cliente.list_object_attributes("/P/Reqs")

        assert cliente.status()["session"] == "not_started"
    finally:
        cliente.close()


def test_sin_pywin32_el_error_explica_que_falta():
    """El proyecto se desarrolla fuera de Windows: el mensaje tiene que ser claro."""
    worker = ComWorker(inicializador=lambda: None, finalizador=lambda: None)
    cliente = DoorsComClient(Settings(module_path="/P/Reqs"), worker=worker)
    try:
        import importlib.util

        if importlib.util.find_spec("win32com") is not None:
            pytest.skip("pywin32 disponible: este caso solo aplica fuera de Windows")

        with pytest.raises(DoorsSessionError, match="pywin32"):
            cliente.start_session(timeout=1)
    finally:
        cliente.close()
