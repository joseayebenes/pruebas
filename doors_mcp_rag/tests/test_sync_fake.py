from __future__ import annotations

import tempfile
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from repository import RequirementsRepository
from sync_service import sync_module


MODULE = "/Demo/System Requirements"
ATTRIBUTES = ["Object Heading", "Object Text", "Status", "REM_UniqueIdentifier"]


def item(
    number: int,
    heading: str,
    text: str,
    status: str,
    unique_identifier: str | None = None,
) -> dict:
    return {
        "absolute_number": number,
        "identifier": f"SYS-REQ-{number}",
        "outline_number": str(number),
        "is_deleted": False,
        "attributes": {
            "Object Heading": heading,
            "Object Text": text,
            "Status": status,
            "REM_UniqueIdentifier": unique_identifier or "",
        },
    }


class FakeDoorsSource:
    def __init__(self, requirements: list[dict]):
        self.requirements = requirements

    def validate_attributes(self, module_path: str, attributes: list[str]) -> None:
        if module_path != MODULE:
            raise ValueError("Módulo falso desconocido")
        available = set(ATTRIBUTES)
        missing = [name for name in attributes if name not in available]
        if missing:
            raise ValueError(f"Atributos inexistentes: {missing}")

    def list_requirements(
        self,
        module_path: str,
        *,
        after_absolute_number: int | None,
        limit: int,
        attributes: list[str],
        max_attribute_chars: int = 20_000,
    ) -> dict:
        eligible = list(self.requirements)
        start_index = 0

        if after_absolute_number is not None:
            for index, requirement in enumerate(eligible):
                if int(requirement["absolute_number"]) == after_absolute_number:
                    start_index = index + 1
                    break
            else:
                raise ValueError("Cursor inexistente en FakeDoorsSource")

        page = eligible[start_index : start_index + limit]
        has_more = start_index + len(page) < len(eligible)
        next_cursor = None
        if has_more and page:
            next_cursor = int(page[-1]["absolute_number"])

        return {
            "ok": True,
            "requirements": page,
            "returned": len(page),
            "has_more": has_more,
            "next_after_absolute_number": next_cursor,
        }


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repository = RequirementsRepository(Path(tmp) / "test.db")

        first = sync_module(
            FakeDoorsSource(
                [
                    item(1, "Ethernet", "The system shall provide Ethernet.", "Approved", "REQ_ETHERNET"),
                    item(2, "Timeout", "A timeout shall be detected.", "Draft", "REQ_TIMEOUT"),
                    item(3, "Recovery", "The system shall recover the link.", "Approved"),
                ]
            ),
            repository,
            MODULE,
            attributes=ATTRIBUTES,
            page_size=2,
        )

        assert first.inserted == 3
        assert first.updated == 0
        assert first.unchanged == 0
        assert first.marked_deleted == 0

        req1 = repository.get_requirement(MODULE, 1)
        assert req1 is not None
        assert req1["unique_identifier"] == "REQ_ETHERNET"

        req3 = repository.get_requirement(MODULE, 3)
        assert req3 is not None
        assert req3["unique_identifier"] is None

        second = sync_module(
            FakeDoorsSource(
                [
                    item(1, "Ethernet", "The system shall provide Ethernet.", "Approved", "REQ_ETHERNET"),
                    item(2, "Timeout", "A timeout shall be detected in 5 seconds.", "Approved", "REQ_TIMEOUT"),
                    item(4, "Degraded mode", "The unit shall enter degraded mode.", "Draft", "REQ_DEGRADED"),
                ]
            ),
            repository,
            MODULE,
            attributes=ATTRIBUTES,
            page_size=2,
        )

        assert second.inserted == 1
        assert second.updated == 1
        assert second.unchanged == 1
        assert second.marked_deleted == 1

        deleted = repository.get_requirement(MODULE, 3)
        assert deleted is not None
        assert deleted["is_deleted"] is True

        third = sync_module(
            FakeDoorsSource(
                [
                    item(1, "Ethernet", "The system shall provide Ethernet.", "Approved", "REQ_ETHERNET"),
                    item(2, "Timeout", "A timeout shall be detected in 5 seconds.", "Approved", "REQ_TIMEOUT"),
                    item(3, "Recovery", "The system shall recover the link.", "Approved"),
                    item(4, "Degraded mode", "The unit shall enter degraded mode.", "Draft", "REQ_DEGRADED"),
                ]
            ),
            repository,
            MODULE,
            attributes=ATTRIBUTES,
            page_size=2,
        )

        restored = repository.get_requirement(MODULE, 3)
        assert restored is not None
        assert restored["is_deleted"] is False
        assert repository.count_requirements(MODULE) == 4

        matches = repository.find_by_unique_identifier("REQ_TIMEOUT", module_path=MODULE)
        assert len(matches) == 1
        assert matches[0]["absolute_number"] == 2

        print("PRUEBA OK")
        print("Primera sync:", first.as_dict())
        print("Segunda sync:", second.as_dict())
        print("Tercera sync:", third.as_dict())


if __name__ == "__main__":
    main()
