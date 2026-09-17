from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from embeddings import build_requirement_embedding_text
from models import RequirementRecord
from repository import RequirementsRepository


MODULE = "/Demo/System Requirements"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repository = RequirementsRepository(Path(tmp) / "test.db")
        repository.initialise()

        first = RequirementRecord(
            module_path=MODULE,
            absolute_number=1,
            identifier="SYS-REQ-1",
            unique_identifier="REQ_MENSAJES",
            outline_number="1",
            heading="Message reception",
            text="The system shall receive messages from the network.",
            attributes={"Status": "Approved"},
        )
        second = RequirementRecord(
            module_path=MODULE,
            absolute_number=2,
            identifier="SYS-REQ-2",
            unique_identifier="REQ_STORAGE",
            outline_number="2",
            heading="Persistent storage",
            text="The system shall store configuration persistently.",
            attributes={"Status": "Approved"},
        )

        assert repository.upsert_requirement(first) == "inserted"
        assert repository.upsert_requirement(second) == "inserted"

        req1 = repository.find_by_unique_identifier("REQ_MENSAJES", module_path=MODULE)[0]
        req2 = repository.find_by_unique_identifier("REQ_STORAGE", module_path=MODULE)[0]

        assert "UniqueIdentifier: REQ_MENSAJES" in build_requirement_embedding_text(req1)

        repository.set_embedding(
            req1["id"],
            [1.0, 0.0, 0.0],
            model="test-model",
            content_hash_value=req1["content_hash"],
        )
        repository.set_embedding(
            req2["id"],
            [0.0, 1.0, 0.0],
            model="test-model",
            content_hash_value=req2["content_hash"],
        )

        results = repository.search_by_embedding(
            [0.95, 0.05, 0.0],
            model="test-model",
            module_path=MODULE,
            limit=2,
        )
        assert len(results) == 2
        assert results[0]["unique_identifier"] == "REQ_MENSAJES"
        assert results[0]["similarity"] > results[1]["similarity"]

        status = repository.embedding_status(module_path=MODULE, model="test-model")
        assert status["total_active"] == 2
        assert status["embedded_with_requested_model"] == 2
        assert status["pending_for_requested_model"] == 0

        # Un cambio de contenido invalida el embedding previo.
        changed = RequirementRecord(
            module_path=MODULE,
            absolute_number=1,
            identifier="SYS-REQ-1",
            unique_identifier="REQ_MENSAJES",
            outline_number="1",
            heading="Message reception",
            text="The system shall receive and validate messages from the network.",
            attributes={"Status": "Approved"},
        )
        assert repository.upsert_requirement(changed) == "updated"
        changed_row = repository.get_requirement(MODULE, 1)
        assert changed_row is not None
        assert changed_row["embedding_available"] is False

        print("PRUEBA OK")


if __name__ == "__main__":
    main()
