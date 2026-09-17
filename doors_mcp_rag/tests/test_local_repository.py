from __future__ import annotations

import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_DIR = PROJECT_ROOT / "sync"
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from local_repository import LocalRequirementsRepository


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE requirements (
                id INTEGER PRIMARY KEY,
                module_path TEXT NOT NULL,
                absolute_number INTEGER NOT NULL,
                identifier TEXT NOT NULL,
                unique_identifier TEXT,
                outline_number TEXT NOT NULL,
                heading TEXT NOT NULL,
                text TEXT NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                embedding BLOB,
                embedding_model TEXT,
                embedding_dimensions INTEGER,
                embedding_content_hash TEXT,
                embedding_updated_at TEXT
            );

            CREATE TABLE requirement_attributes (
                requirement_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                value_text TEXT NOT NULL
            );

            CREATE TABLE links (
                id INTEGER PRIMARY KEY,
                source_module_path TEXT NOT NULL,
                source_absolute_number INTEGER NOT NULL,
                target_module_path TEXT NOT NULL,
                target_absolute_number INTEGER NOT NULL,
                link_module_path TEXT NOT NULL DEFAULT '',
                synced_at TEXT
            );
            """
        )
        vector_1 = struct.pack("<3f", 1.0, 0.0, 0.0)
        vector_2 = struct.pack("<3f", 0.0, 1.0, 0.0)
        conn.executemany(
            """
            INSERT INTO requirements(
                id, module_path, absolute_number, identifier, unique_identifier,
                outline_number, heading, text, is_deleted, content_hash,
                embedding, embedding_model, embedding_dimensions,
                embedding_content_hash, embedding_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'test-model', 3, ?, 'now')
            """,
            [
                (
                    1, "/System", 10, "SYS-10", "REQ_MENSAJES", "1.1",
                    "Mensajes", "El sistema enviara mensajes.", "hash-1",
                    vector_1, "hash-1",
                ),
                (
                    2, "/Software", 20, "SWR-20", "SWR_MENSAJES", "2.1",
                    "Procesamiento", "El software procesara mensajes.", "hash-2",
                    vector_2, "hash-2",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO requirement_attributes VALUES (1, 'Status', 'Approved')"
        )
        conn.execute(
            """
            INSERT INTO links(
                id, source_module_path, source_absolute_number,
                target_module_path, target_absolute_number,
                link_module_path, synced_at
            ) VALUES (1, '/System', 10, '/Software', 20, '/Links/Satisfies', 'now')
            """
        )
        conn.commit()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "requirements.db"
        create_database(db)

        repo = LocalRequirementsRepository(db)
        status = repo.status()
        assert status["read_only"] is True
        assert status["active_requirements"] == 2
        assert status["links"] == 1
        assert status["embedding_models"] == ["test-model"]

        by_uid = repo.find_by_unique_identifier("REQ_MENSAJES")
        assert len(by_uid) == 1
        assert by_uid[0]["absolute_number"] == 10
        assert by_uid[0]["attributes"]["Status"] == "Approved"

        by_id = repo.get_by_database_id(2)
        assert by_id is not None
        assert by_id["unique_identifier"] == "SWR_MENSAJES"

        text = repo.search_text("Approved")
        assert len(text) == 1
        assert text[0]["id"] == 1

        semantic = repo.search_by_embedding(
            [1.0, 0.0, 0.0], model="test-model", limit=2
        )
        assert semantic[0]["id"] == 1
        assert semantic[0]["similarity"] > semantic[1]["similarity"]

        relations = repo.get_relations("/System", 10, direction="outgoing")
        assert len(relations) == 1
        assert relations[0]["related_requirement"]["unique_identifier"] == "SWR_MENSAJES"

        try:
            with repo.connect() as conn:
                conn.execute("DELETE FROM requirements")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("La conexion local deberia rechazar escrituras.")

        print("PRUEBA OK: repositorio MCP estrictamente read-only")


if __name__ == "__main__":
    main()
