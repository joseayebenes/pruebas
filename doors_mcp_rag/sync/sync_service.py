from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from models import RequirementRecord
from repository import RequirementsRepository


UNIQUE_IDENTIFIER_ATTRIBUTE = "REM_UniqueIdentifier"


class RequirementsSource(Protocol):
    """Interfaz mínima que necesita el sincronizador."""

    def validate_attributes(self, module_path: str, attributes: list[str]) -> None:
        ...

    def list_requirements(
        self,
        module_path: str,
        *,
        after_absolute_number: int | None,
        limit: int,
        attributes: list[str],
        max_attribute_chars: int = 20_000,
    ) -> dict:
        ...


@dataclass(slots=True)
class SyncStats:
    module_path: str
    requirements_seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    marked_deleted: int = 0
    pages: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _nullable_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _requirement_from_doors(
    module_path: str,
    item: dict,
    *,
    modified_attribute: str | None,
) -> RequirementRecord:
    attributes = item.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise ValueError("DOORS devolvió 'attributes' con un formato no válido.")

    absolute_number = item.get("absolute_number")
    if not isinstance(absolute_number, int):
        raise ValueError("DOORS devolvió un requisito sin Absolute Number válido.")

    heading = str(attributes.get("Object Heading", ""))
    text = str(attributes.get("Object Text", ""))
    unique_identifier = _nullable_text(attributes.get(UNIQUE_IDENTIFIER_ATTRIBUTE))

    source_last_modified = None
    if modified_attribute:
        value = attributes.get(modified_attribute)
        if value not in (None, ""):
            source_last_modified = str(value)

    return RequirementRecord(
        module_path=module_path,
        absolute_number=absolute_number,
        identifier=str(item.get("identifier", "")),
        unique_identifier=unique_identifier,
        outline_number=str(item.get("outline_number", "")),
        heading=heading,
        text=text,
        attributes={str(k): str(v) for k, v in attributes.items()},
        is_deleted=bool(item.get("is_deleted", False)),
        source_last_modified=source_last_modified,
    )


def sync_module(
    source: RequirementsSource,
    repository: RequirementsRepository,
    module_path: str,
    *,
    attributes: list[str] | None = None,
    page_size: int = 50,
    max_attribute_chars: int = 20_000,
    modified_attribute: str | None = None,
) -> SyncStats:
    """
    Hace una sincronización completa y segura de un módulo DOORS.

    `REM_UniqueIdentifier` se descarga siempre y se proyecta a la columna
    `requirements.unique_identifier`. Un valor vacío se guarda como NULL.

    Regla crítica: los requisitos ausentes solo se marcan como eliminados cuando
    hemos llegado correctamente al final de todas las páginas.
    """
    if page_size < 1 or page_size > 1000:
        raise ValueError("page_size debe estar entre 1 y 1000.")

    selected_attributes = list(attributes or ["Object Heading", "Object Text"])
    if "Object Heading" not in selected_attributes:
        selected_attributes.insert(0, "Object Heading")
    if "Object Text" not in selected_attributes:
        selected_attributes.insert(1, "Object Text")
    if UNIQUE_IDENTIFIER_ATTRIBUTE not in selected_attributes:
        selected_attributes.append(UNIQUE_IDENTIFIER_ATTRIBUTE)
    if modified_attribute and modified_attribute not in selected_attributes:
        selected_attributes.append(modified_attribute)

    source.validate_attributes(module_path, selected_attributes)

    repository.initialise()
    run_id = repository.start_sync_run(module_path)
    stats = SyncStats(module_path=module_path)
    seen_absolute_numbers: set[int] = set()
    cursor: int | None = None
    used_cursors: set[int] = set()

    try:
        while True:
            page = source.list_requirements(
                module_path,
                after_absolute_number=cursor,
                limit=page_size,
                attributes=selected_attributes,
                max_attribute_chars=max_attribute_chars,
            )
            stats.pages += 1

            raw_requirements = page.get("requirements", [])
            if not isinstance(raw_requirements, list):
                raise ValueError("DOORS devolvió una página sin lista de requisitos.")

            with repository.transaction() as connection:
                for raw in raw_requirements:
                    if not isinstance(raw, dict):
                        raise ValueError("Elemento de requisito DOORS no válido.")

                    requirement = _requirement_from_doors(
                        module_path,
                        raw,
                        modified_attribute=modified_attribute,
                    )
                    seen_absolute_numbers.add(requirement.absolute_number)
                    stats.requirements_seen += 1

                    result = repository.upsert_requirement(
                        requirement,
                        connection=connection,
                    )
                    if result == "inserted":
                        stats.inserted += 1
                    elif result == "updated":
                        stats.updated += 1
                    else:
                        stats.unchanged += 1

            has_more = bool(page.get("has_more", False))
            if not has_more:
                break

            next_cursor = page.get("next_after_absolute_number")
            if not isinstance(next_cursor, int):
                raise ValueError(
                    "DOORS indicó que hay más páginas pero no devolvió "
                    "next_after_absolute_number."
                )
            if next_cursor in used_cursors:
                raise ValueError(
                    "DOORS devolvió un cursor repetido; se aborta para evitar "
                    "un bucle infinito."
                )
            used_cursors.add(next_cursor)
            cursor = next_cursor

        with repository.transaction() as connection:
            stats.marked_deleted = repository.mark_missing_as_deleted(
                module_path,
                seen_absolute_numbers,
                connection=connection,
            )

        repository.finish_sync_run(
            run_id,
            status="success",
            requirements_seen=stats.requirements_seen,
            inserted=stats.inserted,
            updated=stats.updated,
            unchanged=stats.unchanged,
            marked_deleted=stats.marked_deleted,
            full_sync_completed=True,
        )
        return stats

    except Exception as exc:
        repository.finish_sync_run(
            run_id,
            status="failed",
            requirements_seen=stats.requirements_seen,
            inserted=stats.inserted,
            updated=stats.updated,
            unchanged=stats.unchanged,
            marked_deleted=0,
            error=f"{type(exc).__name__}: {exc}",
            full_sync_completed=False,
        )
        raise
