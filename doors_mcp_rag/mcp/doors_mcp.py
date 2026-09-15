from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field


# Permite reutilizar la capa COM/DXL del sincronizador sin duplicarla.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from doors_client import DoorsClient, DoorsError  # noqa: E402


DEFAULT_MODULE_PATH = os.environ.get("DOORS_MODULE_PATH", "").strip()
DEFAULT_ATTRIBUTES = ["Object Heading", "Object Text"]

mcp = MCPServer(
    "IBM DOORS Classic Requirements",
    instructions=(
        "Servidor MCP local y de solo lectura para IBM DOORS Classic. "
        "Inicia primero la sesión Automation, valida los atributos y usa "
        "paginación por cursor para evitar respuestas grandes."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
START_SESSION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_CLIENT: DoorsClient | None = None


def _client() -> DoorsClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = DoorsClient()
    return _CLIENT


def _module_path(value: str | None) -> str:
    selected = (value or DEFAULT_MODULE_PATH).strip()
    if not selected:
        raise DoorsError(
            "No se ha indicado module_path y DOORS_MODULE_PATH no está definida."
        )
    return selected


def _attributes(value: list[str] | None) -> list[str]:
    result: list[str] = []
    for item in value or DEFAULT_ATTRIBUTES:
        name = item.strip()
        if name and name not in result:
            result.append(name)
    if not result:
        return DEFAULT_ATTRIBUTES.copy()
    return result


def _error(exc: Exception, module_path: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "module_path": module_path,
    }


@mcp.tool(title="Ver configuración de DOORS", annotations=READ_ONLY)
def doors_configuration() -> dict[str, Any]:
    return {
        "ok": True,
        "default_module_path": DEFAULT_MODULE_PATH or None,
        "python_executable": sys.executable,
        "sync_directory": str(SYNC_DIR),
    }


@mcp.tool(title="Iniciar sesión Automation de DOORS", annotations=START_SESSION)
def start_doors_session() -> dict[str, Any]:
    """Crea DOORS.Application. Después el usuario debe iniciar sesión en la ventana."""
    try:
        _client().start_session()
        return {
            "ok": True,
            "session_started": True,
            "next_step": (
                "Inicia sesión en la ventana de DOORS y llama a doors_status."
            ),
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
        return {
            "ok": True,
            "module_path": selected,
            "attributes": names,
        }
    except Exception as exc:
        return _error(exc, selected)


@mcp.tool(title="Obtener requisitos", annotations=READ_ONLY)
def list_requirements(
    after_absolute_number: Annotated[
        int | None,
        Field(
            description=(
                "Cursor. Si se indica, la página comienza después de este "
                "Absolute Number."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=100, description="Máximo de requisitos devueltos."),
    ] = 25,
    attributes: Annotated[
        list[str] | None,
        Field(description="Atributos devueltos."),
    ] = None,
    max_attribute_chars: Annotated[
        int,
        Field(ge=100, le=50000, description="Máximo de caracteres por atributo."),
    ] = 12000,
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


if __name__ == "__main__":
    mcp.run()
