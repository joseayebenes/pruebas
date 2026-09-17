from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(slots=True)
class RequirementRecord:
    """Representación neutral de un requisito extraído de DOORS."""

    module_path: str
    absolute_number: int
    identifier: str
    outline_number: str
    heading: str
    text: str
    unique_identifier: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    is_deleted: bool = False
    source_last_modified: str | None = None
