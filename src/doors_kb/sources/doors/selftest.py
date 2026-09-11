"""Autodiagnostico de las primitivas de DXL contra un DOORS real.

Existe por una razon concreta: la capa DXL no se puede probar fuera de Windows, y cada
suposicion equivocada sobre una primitiva del lenguaje costaba una ejecucion completa, una
captura de la ventana *DXL output* y una correccion. Con tres fallos seguidos asi -at.size
fuera de una enumeracion, el escapado de comillas y la direccion de la concatenacion- el
patron quedo claro: conviene comprobar **todas** las primitivas de una vez.

Cada prueba es un script independiente y minimo. No usa las funciones auxiliares del
protocolo a proposito: si una de ellas estuviera rota, todas las pruebas fallarian y no se
sabria cual es la causa. La respuesta se marca con el testigo de la llamada, de modo que un
script que aborta se distingue de uno que devuelve un valor (ADR-015).
"""

from __future__ import annotations

from dataclasses import dataclass

from .dxl import build_preamble, escape_dxl_string

SEPARADOR = "|"


@dataclass(frozen=True)
class Prueba:
    """Una primitiva de DXL que hay que confirmar contra la instalacion real."""

    nombre: str
    cuerpo: str
    """Fragmento DXL que debe dejar el resultado en una variable ``valor`` de tipo string."""

    necesita_modulo: bool = True
    esperado: str | None = None
    """Valor exacto que deberia devolver, cuando se conoce de antemano."""

    porque_importa: str = ""


# Las pruebas van de lo mas basico a lo mas especifico: si falla la conversion de numeros,
# lo demas da igual.
PRUEBAS: tuple[Prueba, ...] = (
    Prueba(
        "concatenacion con la cadena primero",
        'string valor = "" 42',
        necesita_modulo=False,
        esperado="42",
        porque_importa="Toda conversion de numero a texto depende de esta forma.",
    ),
    Prueba(
        "concatenacion con el numero primero",
        'string valor = 42 ""',
        necesita_modulo=False,
        esperado="42",
        porque_importa="Si falla, confirma por que hay que empezar por una cadena.",
    ),
    Prueba(
        "length de una cadena",
        'string valor = "" length("abcde")',
        necesita_modulo=False,
        esperado="5",
        porque_importa="Es la longitud que precede a cada campo del protocolo.",
    ),
    Prueba(
        "abrir el modulo en lectura",
        'string valor = fullName(m)',
        porque_importa="Sin esto no hay nada mas que probar (RF-004).",
    ),
    Prueba(
        "recorrido con first y next",
        "Object o = first(m)\nint c = 0\n"
        "while (!null o && c < 5) { c++; o = next(o) }\n"
        'string valor = "" c',
        esperado="5",
        porque_importa="Es el recorrido por cursor de la paginacion (ADR-006).",
    ),
    Prueba(
        "Absolute Number de un objeto",
        'Object o = first(m)\nstring valor = "" ((int)(o."Absolute Number"))',
        porque_importa="Es la identidad logica de un requisito (RF-052).",
    ),
    Prueba(
        "identifier(o)",
        "Object o = first(m)\nstring valor = identifier(o)",
        porque_importa="Campo minimo de todo requisito devuelto (RF-025).",
    ),
    Prueba(
        "number(o) para el outline number",
        "Object o = first(m)\nstring valor = number(o)",
        porque_importa="Campo minimo de todo requisito devuelto (RF-025).",
    ),
    Prueba(
        "isDeleted(o)",
        'Object o = first(m)\nstring valor = isDeleted(o) ? "1" : "0"',
        porque_importa="Los objetos borrados se excluyen por defecto (RF-023).",
    ),
    Prueba(
        "table(o), row(o) y cell(o)",
        'Object o = first(m)\nstring valor = (table(o) || row(o) || cell(o)) ? "1" : "0"',
        porque_importa="Las filas internas de tabla no son requisitos (RF-024).",
    ),
    Prueba(
        "isVisible(o) para el display set",
        'Object o = first(m)\nstring valor = isVisible(o) ? "1" : "0"',
        porque_importa="Es el predicado de RF-022, el unico que quedaba sin confirmar.",
    ),
    Prueba(
        "object(absno, m) para saltar al cursor",
        "Object p = first(m)\nint n = (int)(p.\"Absolute Number\")\n"
        'Object o = object(n, m)\nstring valor = (null o) ? "no" : "si"',
        esperado="si",
        porque_importa="Es lo que evita reescanear el modulo en cada pagina (RNF-015).",
    ),
    Prueba(
        "lectura de un atributo por nombre",
        'Object o = first(m)\nstring valor = o."Object Heading" ""',
        porque_importa="Es como se leen todos los atributos de un requisito.",
    ),
    Prueba(
        "at.type == attrEnumeration",
        "AttrDef ad\nstring valor = \"ninguna\"\n"
        "for ad in m do {\n"
        "    if (!ad.object) { continue }\n"
        "    AttrType at = ad.type\n"
        "    if (at.type == attrEnumeration) { valor = ad.name; break }\n"
        "}",
        porque_importa="Guarda que evita el fallo 'wrong attribute type for Enumeration'.",
    ),
    Prueba(
        "index y lower para la busqueda literal",
        'string valor = "" index(lower("ABCdef"), "cde")',
        necesita_modulo=False,
        esperado="2",
        porque_importa="Es la busqueda literal insensible a mayusculas (RF-031, RF-032).",
    ),
    Prueba(
        "regexp2 para la busqueda por expresion regular",
        'Regexp r = regexp2("[0-9]+")\nstring valor = (r "abc123") ? "si" : "no"',
        necesita_modulo=False,
        esperado="si",
        porque_importa="Es la busqueda por expresion regular (RF-033).",
    ),
    Prueba(
        "enlaces salientes",
        "Object o = first(m)\nint c = 0\nLink l\n"
        'for l in o -> "*" do { c++ }\nstring valor = "" c',
        porque_importa="Es la trazabilidad saliente (RF-035).",
    ),
    Prueba(
        "enlaces entrantes",
        "Object o = first(m)\nint c = 0\nLink li\n"
        'for li in o <- "*" do { c++ }\nstring valor = "" c',
        porque_importa="Es la trazabilidad entrante, que carga modulos origen (RF-036).",
    ),
)


def construir_script(prueba: Prueba, module_path: str, token: str) -> str:
    """Genera el script de una prueba.

    Deliberadamente **no** usa las funciones auxiliares del protocolo: si una de ellas
    estuviera rota, todas las pruebas fallarian a la vez y el diagnostico no serviria de
    nada. Solo emplea concatenacion con la cadena delante, que es la forma que la primera
    prueba confirma.
    """
    if not token.isalnum():
        raise ValueError(f"El testigo de la llamada debe ser alfanumerico: {token!r}")

    apertura = ""
    if prueba.necesita_modulo:
        ruta = escape_dxl_string(module_path)
        apertura = (
            f'Module m = read("{ruta}", false)\n'
            f'if (null m) {{ oleSetResult("{token}{SEPARADOR}NO_MODULE"); halt }}\n'
        )

    return (
        build_preamble(0)
        + apertura
        + prueba.cuerpo
        + f'\noleSetResult("{token}{SEPARADOR}" valor)\n'
    )


def interpretar(prueba: Prueba, crudo: str, token: str) -> tuple[bool, str]:
    """Traduce la respuesta de una prueba a (funciona, detalle)."""
    marca = f"{token}{SEPARADOR}"
    if not crudo.startswith(marca):
        return False, (
            "el script no llego a terminar; el error esta en la ventana 'DXL output' de DOORS"
        )
    valor = crudo[len(marca) :]
    if valor == "NO_MODULE":
        return False, "no se pudo abrir el modulo en lectura"
    if prueba.esperado is not None and valor != prueba.esperado:
        return False, f"devolvio {valor!r} y se esperaba {prueba.esperado!r}"
    return True, valor
