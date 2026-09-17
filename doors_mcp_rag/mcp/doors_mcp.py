from __future__ import annotations

import argparse
import atexit
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field


# El repositorio read-only vive junto al MCP y no depende de DOORS.
from local_repository import LocalRequirementsRepository

# Solo se añade sync/ para reutilizar el adaptador de embeddings Daisei.
# No se importa DoorsClient, traceability.py ni ningun modulo COM/DXL.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.append(str(SYNC_DIR))

from daisei_embedding import (  # noqa: E402
    DEFAULT_EMBEDDING_MODEL,
    DaiseiEmbeddingProvider,
)


SERVER_INSTRUCTIONS = """
Este MCP es la fuente de consulta para requisitos de ingeniería almacenados en la
base SQLite local indicada con --db.

CUANDO USAR ESTE MCP
- Úsalo siempre que la tarea dependa de conocer requisitos, especificaciones,
  atributos, identificadores o relaciones de trazabilidad que puedan estar en
  esta base de datos.
- Úsalo antes de responder preguntas como: "qué requisito habla de...",
  "busca el requisito...", "qué requisitos están relacionados con...",
  "cuál es el texto/atributo de...", "hay algún requisito sobre...",
  "qué requisito depende de..." o tareas de análisis de código/diseño que
  necesiten comprobar requisitos del proyecto.
- Si una respuesta sobre el proyecto requiere hechos procedentes de requisitos,
  consulta este MCP en lugar de inventar, asumir o basarte solo en conocimiento
  general.

CUANDO NO USARLO
- No lo uses para conocimiento general que no dependa de los requisitos locales.
- No esperes información en tiempo real de IBM DOORS: este servidor nunca abre
  DOORS, nunca ejecuta DXL y nunca sincroniza datos.
- Si algo no está en SQLite, indícalo como no encontrado; no supongas que existe
  en DOORS.

COMO ELEGIR LA TOOL
1. Si conoces REM_UniqueIdentifier (por ejemplo REQ_MENSAJES), usa
   find_requirement_by_unique_identifier.
2. Si conoces identifier(obj), usa find_requirement_by_identifier.
3. Si conoces el id interno de SQLite, usa get_requirement_by_id.
4. Si conoces Absolute Number, usa get_requirement_by_absolute_number y, si es
   posible, especifica module_path.
5. Si buscas palabras o una frase concreta, usa search_requirements_text.
6. Si la consulta es conceptual, semántica o no sabes la redacción exacta del
   requisito, usa search_requirements_by_embedding. Daisei se usa únicamente
   para crear el vector de la consulta; los resultados salen solo de SQLite.
7. Si ya dispones de un vector compatible, usa search_requirements_by_vector
   para una búsqueda completamente local sin llamar a Daisei.
8. Si preguntas por dependencias, relaciones, impactos, trazabilidad, requisitos
   origen/destino o "qué está relacionado con X", usa una tool get_relations_*.
9. Usa list_modules cuando necesites saber qué módulos están disponibles y
   database_status cuando necesites comprobar cobertura, embeddings o enlaces.

REGLAS DE RESPUESTA
- Trata SQLite como única fuente de verdad disponible para este MCP.
- Prioriza búsquedas exactas cuando el usuario proporciona un identificador.
- Usa búsqueda semántica cuando la intención esté expresada en lenguaje natural
  y no haya identificador exacto.
- No presentes una coincidencia semántica como identidad exacta: conserva su
  score de similitud y distingue entre coincidencia exacta y aproximada.
- Para preguntas de impacto o trazabilidad, consulta las relaciones además del
  requisito principal cuando sea relevante.
""".strip()


mcp = MCPServer(
    "Requirements Knowledge Base",
    instructions=SERVER_INSTRUCTIONS,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

_REPOSITORY: LocalRequirementsRepository | None = None
_EMBEDDING_PROVIDER: DaiseiEmbeddingProvider | None = None


def _repository() -> LocalRequirementsRepository:
    if _REPOSITORY is None:
        raise RuntimeError("El MCP no ha sido inicializado con --db.")
    return _REPOSITORY


def _embedding_provider() -> DaiseiEmbeddingProvider:
    global _EMBEDDING_PROVIDER
    if _EMBEDDING_PROVIDER is None:
        _EMBEDDING_PROVIDER = DaiseiEmbeddingProvider()
    return _EMBEDDING_PROVIDER


def _close_embedding_provider() -> None:
    global _EMBEDDING_PROVIDER
    if _EMBEDDING_PROVIDER is not None:
        _EMBEDDING_PROVIDER.close()
        _EMBEDDING_PROVIDER = None


atexit.register(_close_embedding_provider)


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} no puede estar vacio.")
    return text


def _optional_module_path(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _error(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def _relations_for_matches(
    requirements: list[dict],
    *,
    direction: Literal["incoming", "outgoing", "both"],
    limit: int,
) -> list[dict]:
    repository = _repository()
    return [
        {
            "requirement": requirement,
            "direction": direction,
            "relations": repository.get_relations(
                str(requirement["module_path"]),
                int(requirement["absolute_number"]),
                direction=direction,
                limit=limit,
            ),
        }
        for requirement in requirements
    ]


@mcp.tool(
    title="Estado de la base local",
    description=(
        "Usa esta tool cuando necesites comprobar qué información puede consultar el MCP: "
        "número de requisitos, módulos, enlaces y modelos de embedding disponibles. "
        "Es útil para diagnosticar una búsqueda vacía o verificar cobertura antes de una "
        "consulta semántica. No la uses como búsqueda de requisitos concretos."
    ),
    annotations=READ_ONLY,
)
def database_status() -> dict[str, Any]:
    """Describe exclusivamente el contenido disponible en SQLite."""
    try:
        status = _repository().status()
        status["ok"] = True
        status["semantic_query_model"] = DEFAULT_EMBEDDING_MODEL
        status["live_doors_access"] = False
        return status
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Listar modulos locales",
    description=(
        "Usa esta tool cuando necesites saber qué módulos de requisitos están disponibles "
        "en la base local, especialmente antes de aplicar un filtro module_path o cuando un "
        "Absolute Number pueda ser ambiguo entre módulos."
    ),
    annotations=READ_ONLY,
)
def list_modules() -> dict[str, Any]:
    try:
        modules = _repository().list_modules()
        return {"ok": True, "count": len(modules), "modules": modules}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Buscar por UniqueIdentifier",
    description=(
        "Usa esta tool como primera opción cuando el usuario o el contexto proporciona un "
        "REM_UniqueIdentifier conocido, por ejemplo REQ_MENSAJES. Realiza una coincidencia "
        "exacta; es preferible a la búsqueda textual o semántica cuando ya conoces este ID."
    ),
    annotations=READ_ONLY,
)
def find_requirement_by_unique_identifier(
    unique_identifier: Annotated[
        str,
        Field(description="Valor guardado de REM_UniqueIdentifier, p.ej. REQ_MENSAJES."),
    ],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    try:
        value = _required_text(unique_identifier, "unique_identifier")
        results = _repository().find_by_unique_identifier(
            value,
            module_path=_optional_module_path(module_path),
            limit=limit,
        )
        return {"ok": True, "query": value, "count": len(results), "results": results}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Buscar por identifier",
    description=(
        "Usa esta tool cuando conozcas el identifier(obj) de DOORS guardado en SQLite. "
        "Es una búsqueda exacta y debe preferirse a texto/embedding si el identifier está "
        "disponible."
    ),
    annotations=READ_ONLY,
)
def find_requirement_by_identifier(
    identifier: Annotated[str, Field(description="identifier(obj) guardado en SQLite.")],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    try:
        value = _required_text(identifier, "identifier")
        results = _repository().find_by_identifier(
            value,
            module_path=_optional_module_path(module_path),
            limit=limit,
        )
        return {"ok": True, "query": value, "count": len(results), "results": results}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Obtener requisito por ID SQLite",
    description=(
        "Usa esta tool cuando ya tengas la clave primaria id de la tabla requirements, por "
        "ejemplo porque apareció en el resultado de otra tool. Recupera directamente ese "
        "registro; no la confundas con identifier, UniqueIdentifier ni Absolute Number."
    ),
    annotations=READ_ONLY,
)
def get_requirement_by_id(
    requirement_id: Annotated[int, Field(ge=1)],
) -> dict[str, Any]:
    try:
        requirement = _repository().get_by_database_id(requirement_id)
        return {
            "ok": requirement is not None,
            "requirement": requirement,
            "error": None if requirement else f"No existe requirement.id={requirement_id}.",
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Buscar por Absolute Number",
    description=(
        "Usa esta tool cuando conozcas el Absolute Number original de DOORS. Si la base "
        "contiene varios módulos, proporciona module_path siempre que sea posible porque "
        "el mismo número puede existir en más de un módulo."
    ),
    annotations=READ_ONLY,
)
def get_requirement_by_absolute_number(
    absolute_number: Annotated[int, Field(ge=1)],
    module_path: Annotated[
        str | None,
        Field(description="Opcional si la DB contiene un unico modulo."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    try:
        results = _repository().find_by_absolute_number(
            absolute_number,
            module_path=_optional_module_path(module_path),
            limit=limit,
        )
        return {
            "ok": True,
            "absolute_number": absolute_number,
            "count": len(results),
            "ambiguous": len(results) > 1,
            "results": results,
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Buscar texto en requisitos locales",
    description=(
        "Usa esta tool cuando busques una palabra, código, expresión o fragmento que esperas "
        "que aparezca literalmente en identifier, UniqueIdentifier, heading o text. Para "
        "preguntas conceptuales donde no conoces la redacción exacta, prefiere la búsqueda "
        "por embedding."
    ),
    annotations=READ_ONLY,
)
def search_requirements_text(
    query: Annotated[str, Field(min_length=1)],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    try:
        value = _required_text(query, "query")
        results = _repository().search_text(
            value,
            module_path=_optional_module_path(module_path),
            limit=limit,
        )
        return {"ok": True, "query": value, "count": len(results), "results": results}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Buscar requisitos por embedding",
    description=(
        "Usa esta tool cuando el usuario describa una necesidad, comportamiento o concepto "
        "en lenguaje natural y no conozcas el texto o identificador exacto del requisito. "
        "También es apropiada para encontrar requisitos semánticamente similares. Daisei "
        "solo calcula el embedding de la consulta; toda la información devuelta procede de "
        "SQLite. No la uses si ya tienes un identificador exacto."
    ),
    annotations=READ_ONLY,
)
def search_requirements_by_embedding(
    query: Annotated[
        str,
        Field(min_length=1, description="Consulta semantica en lenguaje natural."),
    ],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
    min_score: Annotated[float | None, Field(ge=-1.0, le=1.0)] = None,
) -> dict[str, Any]:
    """Daisei calcula solo el vector de la consulta; los resultados salen de SQLite."""
    try:
        repository = _repository()
        status = repository.status()
        available_models = status["embedding_models"]
        if DEFAULT_EMBEDDING_MODEL not in available_models:
            raise ValueError(
                f"La DB no contiene embeddings para {DEFAULT_EMBEDDING_MODEL!r}. "
                f"Modelos disponibles: {available_models}. Calculalos previamente."
            )

        provider = _embedding_provider()
        value = _required_text(query, "query")
        query_vector = provider.embed_one(value)
        results = repository.search_by_embedding(
            query_vector,
            model=provider.model,
            module_path=_optional_module_path(module_path),
            limit=limit,
            min_score=min_score,
        )
        return {
            "ok": True,
            "query": value,
            "model": provider.model,
            "count": len(results),
            "results": results,
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Buscar mediante un vector ya calculado",
    description=(
        "Usa esta tool solo cuando el cliente ya tenga un vector de embedding calculado y "
        "conozca el modelo compatible con los vectores de la base. Es la variante de búsqueda "
        "semántica completamente local y no realiza ninguna llamada a Daisei."
    ),
    annotations=READ_ONLY,
)
def search_requirements_by_vector(
    embedding: Annotated[
        list[float],
        Field(min_length=1, description="Vector de consulta ya calculado."),
    ],
    model: Annotated[
        str,
        Field(description="Modelo con el que se calculo el vector y los embeddings de la DB."),
    ],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
    min_score: Annotated[float | None, Field(ge=-1.0, le=1.0)] = None,
) -> dict[str, Any]:
    """Busqueda 100% local: no realiza ninguna llamada a Daisei."""
    try:
        model_name = _required_text(model, "model")
        results = _repository().search_by_embedding(
            embedding,
            model=model_name,
            module_path=_optional_module_path(module_path),
            limit=limit,
            min_score=min_score,
        )
        return {
            "ok": True,
            "model": model_name,
            "count": len(results),
            "results": results,
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Relaciones por UniqueIdentifier",
    description=(
        "Usa esta tool cuando la pregunta trate de trazabilidad, dependencias, impacto, origen "
        "o destino de un requisito y conozcas su REM_UniqueIdentifier. Devuelve relaciones "
        "entrantes, salientes o ambas desde el grafo guardado en SQLite."
    ),
    annotations=READ_ONLY,
)
def get_relations_by_unique_identifier(
    unique_identifier: Annotated[str, Field(min_length=1)],
    module_path: Annotated[str | None, Field()] = None,
    direction: Annotated[Literal["incoming", "outgoing", "both"], Field()] = "both",
    limit: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> dict[str, Any]:
    try:
        requirements = _repository().find_by_unique_identifier(
            _required_text(unique_identifier, "unique_identifier"),
            module_path=_optional_module_path(module_path),
            limit=100,
        )
        contexts = _relations_for_matches(requirements, direction=direction, limit=limit)
        return {"ok": True, "count": len(contexts), "results": contexts}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Relaciones por identifier",
    description=(
        "Usa esta tool cuando necesites trazabilidad, dependencias o análisis de impacto y "
        "conozcas el identifier(obj) del requisito. Consulta únicamente los enlaces ya "
        "almacenados en SQLite."
    ),
    annotations=READ_ONLY,
)
def get_relations_by_identifier(
    identifier: Annotated[str, Field(min_length=1)],
    module_path: Annotated[str | None, Field()] = None,
    direction: Annotated[Literal["incoming", "outgoing", "both"], Field()] = "both",
    limit: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> dict[str, Any]:
    try:
        requirements = _repository().find_by_identifier(
            _required_text(identifier, "identifier"),
            module_path=_optional_module_path(module_path),
            limit=100,
        )
        contexts = _relations_for_matches(requirements, direction=direction, limit=limit)
        return {"ok": True, "count": len(contexts), "results": contexts}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Relaciones por ID SQLite",
    description=(
        "Usa esta tool cuando necesites navegar la trazabilidad de un requisito que ya fue "
        "devuelto por otra consulta y dispongas de su id interno de SQLite."
    ),
    annotations=READ_ONLY,
)
def get_relations_by_id(
    requirement_id: Annotated[int, Field(ge=1)],
    direction: Annotated[Literal["incoming", "outgoing", "both"], Field()] = "both",
    limit: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> dict[str, Any]:
    try:
        requirement = _repository().get_by_database_id(requirement_id)
        if requirement is None:
            raise ValueError(f"No existe requirement.id={requirement_id}.")
        result = _relations_for_matches([requirement], direction=direction, limit=limit)
        return {"ok": True, **result[0]}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    title="Relaciones por Absolute Number",
    description=(
        "Usa esta tool cuando la pregunta sea de trazabilidad o impacto y conozcas el Absolute "
        "Number. Si existen varios módulos, proporciona module_path para evitar resolver el "
        "número contra el requisito equivocado."
    ),
    annotations=READ_ONLY,
)
def get_relations_by_absolute_number(
    absolute_number: Annotated[int, Field(ge=1)],
    module_path: Annotated[str | None, Field()] = None,
    direction: Annotated[Literal["incoming", "outgoing", "both"], Field()] = "both",
    limit: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> dict[str, Any]:
    try:
        requirements = _repository().find_by_absolute_number(
            absolute_number,
            module_path=_optional_module_path(module_path),
            limit=100,
        )
        contexts = _relations_for_matches(requirements, direction=direction, limit=limit)
        return {
            "ok": True,
            "count": len(contexts),
            "ambiguous": len(contexts) > 1,
            "results": contexts,
        }
    except Exception as exc:
        return _error(exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MCP read-only sobre una base SQLite de requisitos."
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Ruta a doors_requirements.db. Es el unico argumento de configuracion.",
    )
    return parser.parse_args()


def main() -> None:
    global _REPOSITORY
    args = _parse_args()
    _REPOSITORY = LocalRequirementsRepository(args.db)
    mcp.run()


if __name__ == "__main__":
    main()
