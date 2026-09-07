"""Generacion de DXL: regresiones conocidas y escapado (RNF-008, RF-041, RF-040).

Estas pruebas no necesitan DOORS: comprueban el texto que se le enviaria. Dos de ellas
existen porque el fallo correspondiente ya ocurrio una vez en este proyecto (seccion 10).
"""

import json

import pytest

from doors_kb.sources.doors import dxl

# ---------------------------------------------------------------------------------------
# Preambulo: la regresion del salto de linea (RNF-008)
# ---------------------------------------------------------------------------------------


def test_el_preambulo_termina_en_un_salto_de_linea_real():
    """El fallo original: se generaba la secuencia \\n literal y DXL no parseaba el script."""
    preambulo = dxl.build_preamble(0)

    assert preambulo == "pragma runLim, 0\n"
    assert preambulo.endswith("\n")
    assert "\\n" not in preambulo


def test_ningun_script_contiene_la_secuencia_barra_n_en_su_preambulo():
    """La misma regresion, vigilada sobre todos los scripts que se generan."""
    scripts = [
        dxl.script_list_attributes("/Proyecto/Reqs"),
        dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"]),
        dxl.script_get_requirement("/Proyecto/Reqs", 7, ["Object Text"]),
        dxl.script_search("/Proyecto/Reqs", "timeout", ["Object Text"]),
        dxl.script_get_links("/Proyecto/Reqs", 7),
    ]

    for script in scripts:
        primera_linea = script.split("\n", 1)[0]
        assert primera_linea == "pragma runLim, 0"
        assert "\\n" not in primera_linea


def test_el_watchdog_de_dxl_es_configurable():
    """RNF-007: 0 desactiva runLim y deja el control al timeout externo de Python."""
    assert dxl.build_preamble(0).startswith("pragma runLim, 0")
    assert dxl.build_preamble(500_000).startswith("pragma runLim, 500000")


# ---------------------------------------------------------------------------------------
# Escapado de valores (RF-041)
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ('comillas "dobles"', 'comillas \\"dobles\\"'),
        ("barra\\invertida", "barra\\\\invertida"),
        ("dos\nlineas", "dos\\nlineas"),
        ("tabulado\tdentro", "tabulado\\tdentro"),
        ("normal", "normal"),
    ],
)
def test_el_escapado_neutraliza_los_caracteres_peligrosos(entrada, esperado):
    assert dxl.escape_dxl_string(entrada) == esperado


def test_los_caracteres_de_control_se_descartan():
    """DXL no sabe representarlos y romperian el literal; no aportan nada a una busqueda."""
    assert dxl.escape_dxl_string("texto\x00con\x07control") == "textoconcontrol"


def test_una_ruta_de_modulo_con_comillas_no_puede_cerrar_el_literal():
    """El vector de inyeccion mas directo: una ruta que cierra la cadena y anade codigo."""
    script = dxl.script_fetch_page('/Proyecto/"; delete m; //', ["Object Text"])

    # La comilla llega escapada, asi que sigue formando parte del literal de la ruta.
    assert '\\"; delete m; //' in script
    # Y no queda ninguna ocurrencia sin escapar que cerraria la cadena.
    assert '/Proyecto/";' not in script


def test_el_json_de_error_escapa_los_dos_niveles():
    """Una ruta con comillas debe producir JSON valido, no JSON roto dentro de DXL.

    Hay dos escapados encadenados: el de JSON y el de DXL. Construir el payload con
    json.dumps y escapar el resultado entero es lo que mantiene ambos correctos.
    """
    literal = dxl.literal_json({"error": "NO_MODULE", "module_path": '/P/"raro"'})

    # Al deshacer el escapado de DXL debe quedar un JSON que se puede parsear.
    interior = literal[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    assert json.loads(interior)["module_path"] == '/P/"raro"' 


def test_un_termino_de_busqueda_con_comillas_queda_escapado():
    script = dxl.script_search("/Proyecto/Reqs", 'dice "hola"', ["Object Text"])

    assert '\\"hola\\"' in script


def test_un_nombre_de_atributo_con_comillas_queda_escapado():
    script = dxl.script_fetch_page("/Proyecto/Reqs", ['Estado " raro'])

    assert 'Estado \\" raro' in script


# ---------------------------------------------------------------------------------------
# Estructura de los scripts
# ---------------------------------------------------------------------------------------


def test_los_modulos_se_abren_siempre_en_lectura():
    """RF-004 y ADR-003: nunca se abre un modulo en modo edicion."""
    for script in (
        dxl.script_list_attributes("/Proyecto/Reqs"),
        dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"]),
        dxl.script_get_links("/Proyecto/Reqs", 1),
    ):
        assert 'read("/Proyecto/Reqs", false)' in script
        assert "edit(" not in script
        assert "share(" not in script


def test_un_modulo_que_no_abre_produce_un_error_explicito():
    """Seccion 10: NO_MODULE es un error, no una lista vacia de requisitos."""
    script = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"])

    assert "NO_MODULE" in script
    assert "halt" in script


def test_la_paginacion_salta_al_cursor_en_lugar_de_recorrer_desde_el_principio():
    """ADR-006: es la diferencia entre coste lineal y los DXL Execution Timeout."""
    primera = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"], cursor=None)
    siguiente = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"], cursor=250)

    assert "Object o = first(m)" in primera
    assert "object(250, m)" in siguiente
    assert "next(cursorObj)" in siguiente


def test_los_borrados_y_las_filas_de_tabla_se_excluyen_por_defecto():
    """RF-023 y RF-024, con opcion explicita de inclusion."""
    por_defecto = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"])
    con_todo = dxl.script_fetch_page(
        "/Proyecto/Reqs", ["Object Text"], include_deleted=True, include_table_internals=True
    )

    assert "isDeleted(o)" in por_defecto
    assert "table(o) || row(o) || cell(o)" in por_defecto
    assert "isDeleted(o)" not in con_todo.split("while (!null o)")[1]


def test_el_truncado_por_atributo_viaja_dentro_del_script():
    """RNF-011: se recorta en DOORS, no despues, para no traer texto que se va a tirar."""
    script = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"], max_attribute_chars=500)

    assert "cut(jsonEscape(o.\"Object Text\" \"\"), 500)" in script


def test_la_busqueda_distingue_mayusculas_solo_cuando_se_pide():
    """RF-032."""
    insensible = dxl.script_search("/Proyecto/Reqs", "tcp", ["Object Text"])
    sensible = dxl.script_search("/Proyecto/Reqs", "tcp", ["Object Text"], case_sensitive=True)

    assert "lower(valor)" in insensible
    assert "lower(valor)" not in sensible


def test_la_busqueda_por_expresion_regular_usa_el_motor_de_dxl():
    """RF-033."""
    script = dxl.script_search("/Proyecto/Reqs", r"\d+ segundos", ["Object Text"], regex=True)

    assert "regexp2(" in script


def test_la_trazabilidad_declara_que_no_cubre_oslc():
    """RF-037: el agente debe saber que la respuesta no incluye enlaces externos."""
    script = dxl.script_get_links("/Proyecto/Reqs", 7)

    assert "oslc_links_included" in script


def test_la_trazabilidad_entrante_reporta_los_modulos_que_no_pudo_cargar():
    """RF-036: un modulo origen sin permisos no puede pasar por 'sin enlaces'."""
    script = dxl.script_get_links("/Proyecto/Reqs", 7, direction="incoming")

    assert "load_failures" in script


def test_la_direccion_de_la_trazabilidad_filtra_los_bloques_generados():
    """RF-035."""
    salientes = dxl.script_get_links("/Proyecto/Reqs", 7, direction="outgoing")
    entrantes = dxl.script_get_links("/Proyecto/Reqs", 7, direction="incoming")

    assert 'o -> "*"' in salientes and 'o <- "*"' not in salientes
    assert 'o <- "*"' in entrantes and 'o -> "*"' not in entrantes


def test_los_atributos_se_separan_con_comas_validas():
    """El JSON emitido debe ser parseable: separadores entre atributos, no antes del primero."""
    script = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Heading", "Object Text"])

    assert 'b += "\\"Object Heading\\": \\""' in script
    assert 'b += "," "\\"Object Text\\": \\""' in script
    assert "b += , " not in script  # separador mal colocado: DXL no compilaria


def test_el_recorte_reserva_sitio_para_su_propio_marcador():
    """RNF-011: un valor recortado tampoco puede superar el limite pactado."""
    script = dxl.script_fetch_page("/Proyecto/Reqs", ["Object Text"], max_attribute_chars=100)

    assert "int marca = 14" in script
    assert "s[0 : maxChars - marca - 1]" in script
