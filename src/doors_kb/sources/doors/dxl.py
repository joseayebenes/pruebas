"""Generacion de scripts DXL.

Este modulo no habla con DOORS: solo construye texto. Esa separacion permite probar en
cualquier plataforma las dos cosas que ya fallaron una vez en este proyecto (seccion 10):

* **El salto de linea del preambulo** (RNF-008). Una version anterior generaba la secuencia
  ``\\n`` literal en lugar de un salto real, y el interprete DXL rompia el parseo con un
  error confuso. ``test_dxl_generation.py`` lo vigila.
* **El escapado de valores** (RF-041). Todo lo que Python inserta en un script pasa por
  ``escape_dxl_string``. No existe ninguna via para que un agente ejecute DXL arbitrario
  (RF-040, ADR-003): los scripts se construyen aqui, con plantillas cerradas.

Los scripts devuelven su resultado como JSON mediante ``oleSetResult``, que es lo que la
capa Automation recoge despues.

Recorrido por cursor (ADR-006): los scripts de paginacion localizan el objeto del cursor
con ``object(absno, m)`` y siguen con ``next(o)``, en lugar de recorrer el modulo desde el
principio descartando objetos. Es la diferencia entre coste lineal y coste cuadratico, y la
causa de los *DXL Execution Timeout* que motivaron el cambio (seccion 7.1).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

# Caracteres que hay que escapar dentro de un literal de cadena DXL.
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def escape_dxl_string(valor: str) -> str:
    """Escapa un valor para insertarlo en un literal de cadena DXL (RF-041).

    Es la unica puerta por la que un dato de Python entra en un script. Sin ella, una ruta
    de modulo o un termino de busqueda con comillas cerraria el literal y el resto del texto
    se interpretaria como codigo.

    Los caracteres de control que DXL no sabe representar se descartan en lugar de
    escaparse: no aportan nada a una busqueda y romperian el parseo.
    """
    salida = []
    for caracter in valor:
        if caracter in _ESCAPES:
            salida.append(_ESCAPES[caracter])
        elif ord(caracter) < 0x20:
            continue
        else:
            salida.append(caracter)
    return "".join(salida)


def build_preamble(run_limit_cycles: int = 0) -> str:
    """Genera el preambulo del script con el watchdog interno de DXL (RNF-007).

    ``pragma runLim, 0`` desactiva el limite interno y deja el control al timeout externo de
    Python (RNF-004), que es la configuracion recomendada para sincronizar: el watchdog de
    DXL no distingue entre un script colgado y uno que simplemente recorre un modulo grande.

    Termina en un salto de linea **real**. Es un detalle diminuto con una regresion propia:
    generar la secuencia ``\\n`` literal deja el pragma y la primera instruccion en la misma
    linea y DXL falla con un error de sintaxis que no menciona el pragma (RNF-008).
    """
    return f"pragma runLim, {int(run_limit_cycles)}\n"


# ---------------------------------------------------------------------------------------
# Funciones auxiliares que se inyectan en todos los scripts
# ---------------------------------------------------------------------------------------

# Escapa un valor de DOORS para poder emitirlo dentro del JSON de respuesta. Se hace en DXL
# y no en Python porque el texto de los requisitos llega ya dentro de la cadena resultado.
_JSON_HELPERS = """
string jsonEscape(string s) {
    Buffer b = create
    int i
    for (i = 0; i < length(s); i++) {
        char c = s[i]
        if (c == '"') { b += "\\\\\\"" }
        else if (c == '\\\\') { b += "\\\\\\\\" }
        else if (c == '\\n') { b += "\\\\n" }
        else if (c == '\\r') { b += "\\\\r" }
        else if (c == '\\t') { b += "\\\\t" }
        else { b += c }
    }
    string r = stringOf b
    delete b
    return r
}

// Recorta dejando sitio para el marcador, de modo que el resultado nunca supere maxChars:
// el limite pactado con el agente tiene que cumplirse tambien cuando hay recorte (RNF-011).
string cut(string s, int maxChars) {
    if (maxChars <= 0) { return s }
    if (length(s) <= maxChars) { return s }
    int marca = 14  // longitud de "... [truncado]"
    if (maxChars <= marca) { return s[0 : maxChars - 1] }
    return s[0 : maxChars - marca - 1] "... [truncado]"
}
"""


def literal_json(datos: object) -> str:
    """Convierte un valor de Python en un literal de cadena DXL que contiene su JSON.

    Hay **dos** niveles de escapado y confundirlos es facil: el JSON escapa las comillas del
    dato, y DXL escapa a su vez las comillas del JSON. Construir el JSON con ``json.dumps``
    y escapar el resultado entero garantiza que ambos niveles quedan bien, incluso cuando el
    dato lleva comillas (por ejemplo, una ruta de modulo con un nombre raro).
    """
    return '"' + escape_dxl_string(json.dumps(datos, ensure_ascii=False)) + '"'


def _abrir_modulo(module_path: str) -> str:
    """Abre el modulo en lectura y aborta con un error explicito si no se puede (RF-004).

    Se abre siempre por ``fullName`` y de forma explicita: la sesion Automation que crea
    Python no comparte el modulo que el usuario tenga abierto en su ventana, y confiar en
    ello producia errores ``NO_MODULE`` (seccion 10, ADR-002).
    """
    ruta = escape_dxl_string(module_path)
    error = literal_json({"error": "NO_MODULE", "module_path": module_path})
    return f"""
Module m = read("{ruta}", false)
if (null m) {{
    oleSetResult({error})
    halt
}}
"""


def _posicionar_cursor(cursor: int | None) -> str:
    """Situa el recorrido en el objeto siguiente al cursor (ADR-006, RF-058).

    ``object(absno, m)`` salta directamente al objeto por su Absolute Number, sin recorrer
    los anteriores. Si el objeto del cursor ya no existe (lo han borrado entre dos paginas),
    se cae al recorrido desde el principio: es correcto aunque cueste mas, y es preferible a
    interrumpir la sincronizacion.
    """
    if cursor is None:
        return "Object o = first(m)\n"
    return f"""
Object o = null
Object cursorObj = object({int(cursor)}, m)
if (null cursorObj) {{
    o = first(m)
    while (!null o && (int)(o."Absolute Number") <= {int(cursor)}) {{ o = next(o) }}
}} else {{
    o = next(cursorObj)
}}
"""


def _filtros_de_objeto(include_deleted: bool, include_table_internals: bool) -> str:
    """Descarta los objetos que no son requisitos (RF-023, RF-024)."""
    condiciones = []
    if not include_deleted:
        condiciones.append("isDeleted(o)")
    if not include_table_internals:
        condiciones.append("table(o) || row(o) || cell(o)")
    if not condiciones:
        return ""
    return "    if (" + " || ".join(f"({c})" for c in condiciones) + ") { o = next(o); continue }\n"


def _emitir_atributos(attributes: Sequence[str], max_attribute_chars: int) -> str:
    """Genera el fragmento que serializa los atributos pedidos de un objeto.

    Los nombres se insertan escapados y como literales cerrados: no hay forma de que un
    nombre de atributo se convierta en codigo DXL (RF-041).
    """
    piezas = []
    for indice, nombre in enumerate(attributes):
        escapado = escape_dxl_string(nombre)
        separador = "" if indice == 0 else '"," '
        piezas.append(
            f'    b += {separador}"\\"{escapado}\\": \\""'
            f' cut(jsonEscape(o."{escapado}" ""), {int(max_attribute_chars)}) "\\""\n'
        )
    return "".join(piezas)


# ---------------------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------------------


def script_list_attributes(module_path: str, run_limit_cycles: int = 0) -> str:
    """Lista los atributos de objeto del modulo con sus metadatos (RF-010, RF-011)."""
    return (
        build_preamble(run_limit_cycles)
        + _JSON_HELPERS
        + _abrir_modulo(module_path)
        + """
Buffer b = create
b += "{\\"attributes\\": ["
AttrDef ad
bool primero = true
for ad in m do {
    if (!ad.object) { continue }
    if (!primero) { b += "," }
    primero = false
    AttrType at = ad.type
    b += "{\\"name\\": \\"" jsonEscape(ad.name) "\\""
    b += ", \\"type\\": \\"" jsonEscape(at.name "") "\\""
    b += ", \\"is_object\\": true"
    b += ", \\"is_system\\": " (ad.system ? "true" : "false")
    b += ", \\"multi_valued\\": " (ad.multi ? "true" : "false")
    b += ", \\"enum_values\\": ["
    int i
    for (i = 0; i < at.size; i++) {
        if (i > 0) { b += "," }
        b += "\\"" jsonEscape(at.strings[i]) "\\""
    }
    b += "]}"
}
b += "]}"
oleSetResult(stringOf b)
delete b
"""
    )


def script_fetch_page(
    module_path: str,
    attributes: Sequence[str],
    *,
    cursor: int | None = None,
    page_size: int = 25,
    max_attribute_chars: int = 20_000,
    include_deleted: bool = False,
    include_table_internals: bool = False,
    run_limit_cycles: int = 0,
) -> str:
    """Lee una pagina de requisitos a partir del cursor (RF-020, RF-057, RF-058).

    Devuelve ``next_cursor`` solo si quedan objetos por recorrer. Que valga ``null`` es la
    unica senal de que el modulo se ha recorrido entero, y de ella depende que el
    sincronizador pueda marcar ausentes como eliminados (RF-061).
    """
    return (
        build_preamble(run_limit_cycles)
        + _JSON_HELPERS
        + _abrir_modulo(module_path)
        + _posicionar_cursor(cursor)
        + f"""
Buffer b = create
b += "{{\\"records\\": ["
int emitidos = 0
int ultimo = -1
bool primero = true
bool agotado = true
while (!null o) {{
    if (emitidos >= {int(page_size)}) {{ agotado = false; break }}
{_filtros_de_objeto(include_deleted, include_table_internals)}    if (!primero) {{ b += "," }}
    primero = false
    ultimo = (int)(o."Absolute Number")
    b += "{{\\"absolute_number\\": " ultimo ""
    b += ", \\"identifier\\": \\"" jsonEscape(identifier(o)) "\\""
    b += ", \\"outline_number\\": \\"" jsonEscape(number(o)) "\\""
    b += ", \\"attributes\\": {{"
{_emitir_atributos(attributes, max_attribute_chars)}    b += "}}}}"
    emitidos++
    o = next(o)
}}
b += "], \\"next_cursor\\": "
if (agotado) {{ b += "null" }} else {{ b += ultimo "" }}
b += "}}"
oleSetResult(stringOf b)
delete b
"""
    )


def script_get_requirement(
    module_path: str,
    absolute_number: int,
    attributes: Sequence[str],
    *,
    max_attribute_chars: int = 20_000,
    run_limit_cycles: int = 0,
) -> str:
    """Obtiene un objeto concreto por su Absolute Number (RF-021)."""
    return (
        build_preamble(run_limit_cycles)
        + _JSON_HELPERS
        + _abrir_modulo(module_path)
        + f"""
Object o = object({int(absolute_number)}, m)
if (null o) {{
    oleSetResult({literal_json({"record": None})})
    halt
}}
Buffer b = create
b += "{{\\"record\\": {{\\"absolute_number\\": " (int)(o."Absolute Number") ""
b += ", \\"identifier\\": \\"" jsonEscape(identifier(o)) "\\""
b += ", \\"outline_number\\": \\"" jsonEscape(number(o)) "\\""
b += ", \\"is_deleted\\": " (isDeleted(o) ? "true" : "false")
b += ", \\"attributes\\": {{"
{_emitir_atributos(attributes, max_attribute_chars)}b += "}}}}}}"
oleSetResult(stringOf b)
delete b
"""
    )


def script_search(
    module_path: str,
    query: str,
    attributes: Sequence[str],
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    cursor: int | None = None,
    page_size: int = 25,
    max_attribute_chars: int = 20_000,
    run_limit_cycles: int = 0,
) -> str:
    """Busca texto literal o expresion regular dentro del modulo (RF-030..RF-034).

    La busqueda ocurre **dentro de DOORS**: al agente solo llegan las coincidencias, no el
    modulo entero (RF-030). La respuesta indica en que atributo se encontro y en que
    posicion (RF-034).
    """
    patron = escape_dxl_string(query)
    comparacion = (
        f'Regexp patron = regexp2("{patron}")'
        if regex
        else f'string aguja = "{patron}"'
    )
    if regex:
        deteccion = "        if (patron valor) { encontrado = true; posicion = start(patron) }"
    elif case_sensitive:
        deteccion = (
            "        int p = index(valor, aguja)\n"
            "        if (p >= 0) { encontrado = true; posicion = p }"
        )
    else:
        deteccion = (
            "        int p = index(lower(valor), lower(aguja))\n"
            "        if (p >= 0) { encontrado = true; posicion = p }"
        )

    nombres = ", ".join(f'"{escape_dxl_string(n)}"' for n in attributes)
    return (
        build_preamble(run_limit_cycles)
        + _JSON_HELPERS
        + _abrir_modulo(module_path)
        + _posicionar_cursor(cursor)
        + f"""
{comparacion}
string nombres[] = {{{nombres}}}
Buffer b = create
b += "{{\\"hits\\": ["
int emitidos = 0
int ultimo = -1
bool primero = true
bool agotado = true
while (!null o) {{
    if (emitidos >= {int(page_size)}) {{ agotado = false; break }}
    if (isDeleted(o) || table(o) || row(o) || cell(o)) {{ o = next(o); continue }}
    ultimo = (int)(o."Absolute Number")
    int j
    for (j = 0; j < {len(attributes)}; j++) {{
        string valor = o.(nombres[j]) ""
        bool encontrado = false
        int posicion = -1
{deteccion}
        if (encontrado) {{
            if (!primero) {{ b += "," }}
            primero = false
            b += "{{\\"absolute_number\\": " ultimo ""
            b += ", \\"identifier\\": \\"" jsonEscape(identifier(o)) "\\""
            b += ", \\"outline_number\\": \\"" jsonEscape(number(o)) "\\""
            b += ", \\"matched_attribute\\": \\"" jsonEscape(nombres[j]) "\\""
            b += ", \\"match_start\\": " posicion ""
            b += ", \\"value\\": \\"" cut(jsonEscape(valor), {int(max_attribute_chars)}) "\\"}}"
            emitidos++
            break
        }}
    }}
    o = next(o)
}}
b += "], \\"next_cursor\\": "
if (agotado) {{ b += "null" }} else {{ b += ultimo "" }}
b += "}}"
oleSetResult(stringOf b)
delete b
"""
    )


def script_get_links(
    module_path: str, absolute_number: int, *, direction: str = "both", run_limit_cycles: int = 0
) -> str:
    """Obtiene la trazabilidad estandar de un objeto (RF-035, RF-036).

    Para los enlaces entrantes hay que cargar en lectura los modulos origen, cosa que puede
    fallar por permisos; el script lo reporta en ``load_failures`` en vez de callarselo
    (RF-036). No cubre enlaces externos OSLC (RF-037).
    """
    salientes = direction in ("outgoing", "both")
    entrantes = direction in ("incoming", "both")
    bloques = []
    if salientes:
        bloques.append(
            """
Link l
for l in o -> "*" do {
    Object destino = target(l)
    if (null destino) { continue }
    if (!primero) { b += "," }
    primero = false
    b += "{\\"direction\\": \\"outgoing\\""
    b += ", \\"target_module\\": \\"" jsonEscape(fullName(module(destino))) "\\""
    b += ", \\"target_absolute_number\\": " (int)(destino."Absolute Number") ""
    b += ", \\"link_module\\": \\"" jsonEscape(l."LinkModuleName" "") "\\"}"
}
"""
        )
    if entrantes:
        bloques.append(
            """
// Los enlaces entrantes exigen tener cargados los modulos origen; si alguno no se puede
// cargar se anota y se sigue, en lugar de devolver una trazabilidad incompleta en silencio.
ModName_ otro
for otro in o <- "*" do {
    if (null read(fullName(otro), false)) {
        if (!primerFallo) { f += "," }
        primerFallo = false
        f += "\\"" jsonEscape(fullName(otro)) "\\""
    }
}
Link li
for li in o <- "*" do {
    Object origen = source(li)
    if (null origen) { continue }
    if (!primero) { b += "," }
    primero = false
    b += "{\\"direction\\": \\"incoming\\""
    b += ", \\"source_module\\": \\"" jsonEscape(fullName(module(origen))) "\\""
    b += ", \\"source_absolute_number\\": " (int)(origen."Absolute Number") ""
    b += ", \\"link_module\\": \\"" jsonEscape(li."LinkModuleName" "") "\\"}"
}
"""
        )

    return (
        build_preamble(run_limit_cycles)
        + _JSON_HELPERS
        + _abrir_modulo(module_path)
        + f"""
Object o = object({int(absolute_number)}, m)
if (null o) {{
    oleSetResult({literal_json({"links": [], "error": "OBJECT_NOT_FOUND"})})
    halt
}}
Buffer b = create
Buffer f = create
bool primero = true
bool primerFallo = true
b += "{{\\"links\\": ["
f += "\\"load_failures\\": ["
{"".join(bloques)}
b += "], "
f += "], \\"oslc_links_included\\": false}}"
oleSetResult(stringOf b stringOf f)
delete b
delete f
"""
    )
