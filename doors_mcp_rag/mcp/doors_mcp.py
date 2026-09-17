from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from doors_client import DoorsClient, DoorsError  # noqa: E402
from embeddings import (  # noqa: E402
    EmbeddingConfig,
    OpenAICompatibleEmbeddingProvider,
    embed_query,
    generate_embeddings,
)
from repository import RequirementsRepository  # noqa: E402


DEFAULT_MODULE_PATH = os.environ.get("DOORS_MODULE_PATH", "").strip()
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "DOORS_SQLITE_PATH",
        str(SYNC_DIR / "doors_requirements.db"),
    )
)
DEFAULT_ATTRIBUTES = ["Object Heading", "Object Text"]

mcp = MCPServer(
    "IBM DOORS Classic Requirements",
    instructions=(
        "Servidor MCP local para IBM DOORS Classic y su copia SQLite. "
        "Las tools local_* no necesitan una sesión DOORS. La búsqueda semántica "
        "usa el endpoint OpenAI-compatible configurado en las variables "
        "DOORS_EMBEDDING_* y compara el vector de consulta con los embeddings "
        "almacenados en SQLite."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
START_SESSION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
LOCAL_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

_CLIENT: DoorsClient | None = None
_REPOSITORY: RequirementsRepository | None = None


def _client() -> DoorsClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = DoorsClient()
    return _CLIENT


def _repository() -> RequirementsRepository:
    global _REPOSITORY
    if _REPOSITORY is None:
        _REPOSITORY = RequirementsRepository(DEFAULT_DB_PATH)
        _REPOSITORY.initialise()
    return _REPOSITORY


def _module_path(value: str | None) -> str:
    selected = (value or DEFAULT_MODULE_PATH).strip()
    if not selected:
        raise DoorsError(
            "No se ha indicado module_path y DOORS_MODULE_PATH no está definida."
        )
    return selected


def _optional_module_path(value: str | None) -> str | None:
    selected = (value or DEFAULT_MODULE_PATH).strip()
    return selected or None


def _attributes(value: list[str] | None) -> list[str]:
    result: list[str] = []
    for item in value or DEFAULT_ATTRIBUTES:
        name = item.strip()
        if name and name not in result:
            result.append(name)
    if not result:
        return DEFAULT_ATTRIBUTES.copy()
    return result


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} no puede estar vacío.")
    return text


def _error(exc: Exception, module_path: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "module_path": module_path,
    }


@mcp.tool(title="Ver configuración de DOORS", annotations=READ_ONLY)
def doors_configuration() -> dict[str, Any]:
    embedding_config = EmbeddingConfig.from_env()
    return {
        "ok": True,
        "default_module_path": DEFAULT_MODULE_PATH or None,
        "sqlite_path": str(DEFAULT_DB_PATH),
        "python_executable": sys.executable,
        "sync_directory": str(SYNC_DIR),
        "embedding": embedding_config.public_dict(),
    }


@mcp.tool(title="Iniciar sesión Automation de DOORS", annotations=START_SESSION)
def start_doors_session() -> dict[str, Any]:
    """Crea DOORS.Application. Después el usuario debe iniciar sesión en la ventana."""
    try:
        _client().start_session()
        return {
            "ok": True,
            "session_started": True,
            "next_step": "Inicia sesión en la ventana de DOORS y llama a doors_status.",
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(title="Comprobar módulo de DOORS", annotations=READ_ONLY)
def doors_status(
    module_path: Annotated[
        str | None,
        Field(description="Ruta interna del módulo; usa DOORS_MODULE_PATH si se omite."),
    ] = None,
) -> dict[str, Any]:
    selected: str | None = None
    try:
        selected = _module_path(module_path)
        return _client().status(selected)
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Listar atributos de objeto", annotations=READ_ONLY)
def list_object_attributes(
    module_path: Annotated[
        str | None,
        Field(description="Ruta interna del módulo; usa DOORS_MODULE_PATH si se omite."),
    ] = None,
) -> dict[str, Any]:
    selected: str | None = None
    try:
        selected = _module_path(module_path)
        attributes = _client().list_object_attributes(selected)
        return {
            "ok": True,
            "module_path": selected,
            "attributes": attributes,
            "count": len(attributes),
        }
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Validar atributos", annotations=READ_ONLY)
def validate_attributes(
    attributes: Annotated[
        list[str],
        Field(min_length=1, description="Nombres exactos de atributos a validar."),
    ],
    module_path: Annotated[
        str | None,
        Field(description="Ruta interna del módulo; usa DOORS_MODULE_PATH si se omite."),
    ] = None,
) -> dict[str, Any]:
    selected: str | None = None
    try:
        selected = _module_path(module_path)
        names = _attributes(attributes)
        _client().validate_attributes(selected, names)
        return {"ok": True, "module_path": selected, "attributes": names}
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Obtener requisitos directamente de DOORS", annotations=READ_ONLY)
def list_requirements(
    after_absolute_number: Annotated[
        int | None,
        Field(description="Cursor Absolute Number de la página anterior."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    attributes: Annotated[list[str] | None, Field()] = None,
    max_attribute_chars: Annotated[int, Field(ge=100, le=50000)] = 12000,
    module_path: Annotated[str | None, Field()] = None,
) -> dict[str, Any]:
    selected: str | None = None
    try:
        selected = _module_path(module_path)
        names = _attributes(attributes)
        _client().validate_attributes(selected, names)
        result = _client().list_requirements(
            selected,
            after_absolute_number=after_absolute_number,
            limit=limit,
            attributes=names,
            max_attribute_chars=max_attribute_chars,
        )
        result["module_path"] = selected
        return result
    except Exception as exc:
        return _error(exc, selected)


# ---------------------------------------------------------------------------
# Tools sobre la copia SQLite local
# ---------------------------------------------------------------------------


@mcp.tool(title="Estado de la base local de requisitos", annotations=READ_ONLY)
def local_database_status(
    module_path: Annotated[str | None, Field()] = None,
) -> dict[str, Any]:
    try:
        selected = _optional_module_path(module_path)
        repository = _repository()
        config = EmbeddingConfig.from_env()
        if selected:
            count = repository.count_requirements(selected)
        else:
            with repository.connect() as conn:
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM requirements WHERE is_deleted = 0"
                    ).fetchone()[0]
                )
        return {
            "ok": True,
            "sqlite_path": str(DEFAULT_DB_PATH),
            "module_path": selected,
            "active_requirements": count,
            "embedding": repository.embedding_status(
                module_path=selected,
                model=config.model or None,
            ),
            "embedding_config": config.public_dict(),
        }
    except Exception as exc:
        return _error(exc, _optional_module_path(module_path))


@mcp.tool(title="Buscar requisito por UniqueIdentifier", annotations=READ_ONLY)
def find_requirement_by_unique_identifier(
    unique_identifier: Annotated[
        str,
        Field(description="Valor de REM_UniqueIdentifier, por ejemplo REQ_MENSAJES."),
    ],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    selected = _optional_module_path(module_path)
    try:
        value = _required_text(unique_identifier, "unique_identifier")
        results = _repository().find_by_unique_identifier(
            value,
            module_path=selected,
            limit=limit,
        )
        return {"ok": True, "query": value, "count": len(results), "results": results}
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Buscar requisito por identifier de DOORS", annotations=READ_ONLY)
def find_requirement_by_identifier(
    identifier: Annotated[str, Field(description="Valor de identifier(obj) en DOORS.")],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    selected = _optional_module_path(module_path)
    try:
        value = _required_text(identifier, "identifier")
        results = _repository().find_by_identifier(
            value,
            module_path=selected,
            limit=limit,
        )
        return {"ok": True, "query": value, "count": len(results), "results": results}
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Obtener requisito por ID local SQLite", annotations=READ_ONLY)
def get_local_requirement_by_id(
    requirement_id: Annotated[int, Field(ge=1)],
) -> dict[str, Any]:
    try:
        requirement = _repository().get_requirement_by_database_id(requirement_id)
        return {
            "ok": requirement is not None,
            "requirement": requirement,
            "error": None if requirement else f"No existe requirement.id={requirement_id}.",
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(title="Obtener requisito local por Absolute Number", annotations=READ_ONLY)
def get_local_requirement_by_absolute_number(
    absolute_number: Annotated[int, Field(ge=1)],
    module_path: Annotated[str | None, Field()] = None,
) -> dict[str, Any]:
    selected: str | None = None
    try:
        selected = _module_path(module_path)
        requirement = _repository().get_requirement(selected, absolute_number)
        return {
            "ok": requirement is not None,
            "module_path": selected,
            "absolute_number": absolute_number,
            "requirement": requirement,
            "error": (
                None
                if requirement
                else f"No existe Absolute Number {absolute_number} en {selected}."
            ),
        }
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Estado de embeddings locales", annotations=READ_ONLY)
def embedding_status(
    module_path: Annotated[str | None, Field()] = None,
) -> dict[str, Any]:
    selected = _optional_module_path(module_path)
    try:
        config = EmbeddingConfig.from_env()
        return {
            "ok": True,
            "config": config.public_dict(),
            "status": _repository().embedding_status(
                module_path=selected,
                model=config.model or None,
            ),
        }
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Calcular embeddings pendientes", annotations=LOCAL_WRITE)
def calculate_embeddings(
    module_path: Annotated[str | None, Field()] = None,
    force: Annotated[bool, Field(description="Recalcula embeddings ya actuales.")] = False,
    limit: Annotated[int | None, Field(ge=1)] = None,
    batch_size: Annotated[int | None, Field(ge=1, le=512)] = None,
) -> dict[str, Any]:
    selected = _optional_module_path(module_path)
    try:
        config = EmbeddingConfig.from_env().with_batch_size(batch_size).validate()
        provider = OpenAICompatibleEmbeddingProvider(config)
        stats = generate_embeddings(
            _repository(),
            provider,
            module_path=selected,
            batch_size=config.batch_size,
            max_input_chars=config.max_input_chars,
            force=force,
            limit=limit,
        )
        return {
            "ok": True,
            "model": config.model,
            "module_path": selected,
            "stats": stats.as_dict(),
            "status": _repository().embedding_status(
                module_path=selected,
                model=config.model,
            ),
        }
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Buscar requisitos por similitud semántica", annotations=READ_ONLY)
def search_requirements_by_embedding(
    query: Annotated[str, Field(min_length=1, description="Consulta en lenguaje natural.")],
    module_path: Annotated[str | None, Field()] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
    min_score: Annotated[
        float | None,
        Field(ge=-1.0, le=1.0, description="Umbral opcional de similitud coseno."),
    ] = None,
) -> dict[str, Any]:
    selected = _optional_module_path(module_path)
    try:
        config = EmbeddingConfig.from_env().validate()
        provider = OpenAICompatibleEmbeddingProvider(config)
        query_vector = embed_query(provider, query)
        results = _repository().search_by_embedding(
            query_vector,
            model=config.model,
            module_path=selected,
            limit=limit,
            min_score=min_score,
        )
        return {
            "ok": True,
            "query": query,
            "model": config.model,
            "module_path": selected,
            "count": len(results),
            "results": results,
        }
    except Exception as exc:
        return _error(exc, selected)


if __name__ == "__main__":
    mcp.run()
