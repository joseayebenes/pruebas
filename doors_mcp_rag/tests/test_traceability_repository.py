from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from models import RequirementRecord
from repository import RequirementsRepository
from traceability import TraceLink, TraceabilityRepository


MODULE_A = "/Demo/System Requirements"
MODULE_B = "/Demo/Software Requirements"


def requirement(module: str, number: int, uid: str, heading: str) -> RequirementRecord:
    return RequirementRecord(
        module_path=module,
        absolute_number=number,
        identifier=f"{module.rsplit('/', 1)[-1]}-{number}",
        unique_identifier=uid,
        outline_number=str(number),
        heading=heading,
        text=f"Text for {uid}",
        attributes={
            "Object Heading": heading,
            "Object Text": f"Text for {uid}",
            "REM_UniqueIdentifier": uid,
        },
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repository = RequirementsRepository(Path(tmp) / "requirements.db")
        repository.initialise()

        repository.upsert_requirement(requirement(MODULE_A, 1, "REQ_A1", "Requirement A1"))
        repository.upsert_requirement(requirement(MODULE_A, 2, "REQ_A2", "Requirement A2"))
        repository.upsert_requirement(requirement(MODULE_B, 10, "REQ_B10", "Requirement B10"))

        trace = TraceabilityRepository(repository)
        trace.initialise()

        stored = trace.replace_links_for_module(
            MODULE_A,
            {
                TraceLink(MODULE_A, 1, MODULE_A, 2, "/Links/Satisfies"),
                TraceLink(MODULE_B, 10, MODULE_A, 1, "/Links/Derives"),
            },
            direction="both",
        )
        assert stored == 2
        assert trace.count_links(module_path=MODULE_A) == 2

        relations = trace.get_relations(MODULE_A, 1, direction="both")
        assert len(relations) == 2
        assert {item["direction"] for item in relations} == {"incoming", "outgoing"}
        related_uids = {
            item["related_requirement"]["unique_identifier"] for item in relations
        }
        assert related_uids == {"REQ_A2", "REQ_B10"}

        incoming = trace.get_relations(MODULE_A, 1, direction="incoming")
        assert len(incoming) == 1
        assert incoming[0]["source"]["unique_identifier"] == "REQ_B10"

        outgoing = trace.get_relations(MODULE_A, 1, direction="outgoing")
        assert len(outgoing) == 1
        assert outgoing[0]["target"]["unique_identifier"] == "REQ_A2"

        # Refrescar solo outgoing no debe borrar un enlace entrante externo.
        trace.replace_links_for_module(
            MODULE_A,
            {TraceLink(MODULE_A, 2, MODULE_B, 10, "/Links/Implements")},
            direction="outgoing",
        )
        preserved = trace.get_relations(MODULE_A, 1, direction="incoming")
        assert len(preserved) == 1
        assert preserved[0]["source"]["unique_identifier"] == "REQ_B10"

        print("PRUEBA OK")
        print("Links totales:", trace.count_links())


if __name__ == "__main__":
    main()
