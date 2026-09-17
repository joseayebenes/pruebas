from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal, Sequence

from models import RequirementRecord


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS modules (
    module_path TEXT PRIMARY KEY,
    last_sync_at TEXT,
    last_full_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_path TEXT NOT NULL,
    absolute_number INTEGER NOT NULL,
    identifier TEXT NOT NULL DEFAULT '',
    unique_identifier TEXT,
    outline_number TEXT NOT NULL DEFAULT '',
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,
    source_last_modified TEXT,
    content_hash TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    embedding BLOB,
    embedding_model TEXT,
    embedding_dimensions INTEGER,
    embedding_content_hash TEXT,
    embedding_updated_at TEXT,

    UNIQUE(module_path, absolute_number),
    FOREIGN KEY(module_path)
        REFERENCES modules(module_path)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_requirements_identifier
    ON requirements(identifier);

CREATE INDEX IF NOT EXISTS idx_requirements_module_deleted
    ON requirements(module_path, is_deleted);

CREATE TABLE IF NOT EXISTS requirement_attributes (
    requirement_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    value_text TEXT NOT NULL DEFAULT '',

    PRIMARY KEY(requirement_id, name),
    FOREIGN KEY(requirement_id)
        REFERENCES requirements(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_requirement_attributes_name_value
    ON requirement_attributes(name, value_text);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_module_path TEXT NOT NULL,
    source_absolute_number INTEGER NOT NULL,
    target_module_path TEXT NOT NULL,
    target_absolute_number INTEGER NOT NULL,
    link_module_path TEXT NOT NULL DEFAULT '',

    UNIQUE(
        source_module_path,
        source_absolute_number,
        target_module_path,
        target_absolute_number,
        link_module_path
    )
);

CREATE INDEX IF NOT EXISTS idx_links_source
    ON links(source_module_path, source_absolute_number);

CREATE INDEX IF NOT EXISTS idx_links_target
    ON links(target_module_path, target_absolute_number);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    requirements_seen INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    marked_deleted INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


UpsertResult = Literal["inserted", "updated", "unchanged"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_requirement_payload(requirement: RequirementRecord) -> dict:
    """Datos que determinan si el requisito ha cambiado."""
    return {
        "identifier": requirement.identifier,
        "unique_identifier": requirement.unique_identifier,
        "outline_number": requirement.outline_number,
        "heading": requirement.heading,
        "text": requirement.text,
        "attributes": dict(sorted(requirement.attributes.items())),
        "is_deleted": requirement.is_deleted,
        "source_last_modified": requirement.source_last_modified,
    }


def content_hash(requirement: RequirementRecord) -> str:
    payload = json.dumps(
        canonical_requirement_payload(requirement),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pack_embedding(vector: Sequence[float]) -> bytes:
    values = [float(value) for value in vector]
    if not values:
        raise ValueError("El embedding no puede estar vacío.")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("El embedding contiene valores no finitos.")
    return struct.pack(f"<{len(values)}f", *values)


def _unpack_embedding(blob: bytes, dimensions: int) -> list[float]:
    if dimensions <= 0:
        raise ValueError("embedding_dimensions debe ser mayor que cero.")
    expected = dimensions * 4
    if len(blob) != expected:
        raise ValueError(
            f"Embedding BLOB inválido: {len(blob)} bytes; se esperaban {expected}."
        )
    return list(struct.unpack(f"<{dimensions}f", blob))


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Los embeddings deben tener la misma dimensión y no estar vacíos.")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class RequirementsRepository:
    """Acceso a la copia local SQLite de DOORS."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialise(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._migrate_schema(connection)
            connection.commit()

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        """Añade columnas nuevas sin obligar a recrear una base existente."""
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(requirements)").fetchall()
        }
        migrations = {
            "unique_identifier": "unique_identifier TEXT",
            "embedding": "embedding BLOB",
            "embedding_model": "embedding_model TEXT",
            "embedding_dimensions": "embedding_dimensions INTEGER",
            "embedding_content_hash": "embedding_content_hash TEXT",
            "embedding_updated_at": "embedding_updated_at TEXT",
        }
        for column_name, definition in migrations.items():
            if column_name not in columns:
                connection.execute(
                    f"ALTER TABLE requirements ADD COLUMN {definition}"
                )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_requirements_unique_identifier
            ON requirements(unique_identifier)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_requirements_embedding_model
            ON requirements(embedding_model)
            """
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_module(
        self,
        module_path: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        conn = connection or self.connect()
        try:
            conn.execute(
                """
                INSERT INTO modules(module_path)
                VALUES (?)
                ON CONFLICT(module_path) DO NOTHING
                """,
                (module_path,),
            )
            if owns_connection:
                conn.commit()
        finally:
            if owns_connection:
                conn.close()

    def upsert_requirement(
        self,
        requirement: RequirementRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> UpsertResult:
        owns_connection = connection is None
        conn = connection or self.connect()

        try:
            self.ensure_module(requirement.module_path, connection=conn)
            new_hash = content_hash(requirement)
            current = conn.execute(
                """
                SELECT id, content_hash
                FROM requirements
                WHERE module_path = ?
                  AND absolute_number = ?
                """,
                (requirement.module_path, requirement.absolute_number),
            ).fetchone()
            now = utc_now_iso()

            if current is None:
                cursor = conn.execute(
                    """
                    INSERT INTO requirements(
                        module_path,
                        absolute_number,
                        identifier,
                        unique_identifier,
                        outline_number,
                        heading,
                        text,
                        is_deleted,
                        source_last_modified,
                        content_hash,
                        synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        requirement.module_path,
                        requirement.absolute_number,
                        requirement.identifier,
                        requirement.unique_identifier,
                        requirement.outline_number,
                        requirement.heading,
                        requirement.text,
                        int(requirement.is_deleted),
                        requirement.source_last_modified,
                        new_hash,
                        now,
                    ),
                )
                requirement_id = int(cursor.lastrowid)
                self._replace_attributes(conn, requirement_id, requirement.attributes)
                result: UpsertResult = "inserted"

            elif current["content_hash"] != new_hash:
                requirement_id = int(current["id"])
                conn.execute(
                    """
                    UPDATE requirements
                    SET identifier = ?,
                        unique_identifier = ?,
                        outline_number = ?,
                        heading = ?,
                        text = ?,
                        is_deleted = ?,
                        source_last_modified = ?,
                        content_hash = ?,
                        synced_at = ?,
                        embedding = NULL,
                        embedding_model = NULL,
                        embedding_dimensions = NULL,
                        embedding_content_hash = NULL,
                        embedding_updated_at = NULL
                    WHERE id = ?
                    """,
                    (
                        requirement.identifier,
                        requirement.unique_identifier,
                        requirement.outline_number,
                        requirement.heading,
                        requirement.text,
                        int(requirement.is_deleted),
                        requirement.source_last_modified,
                        new_hash,
                        now,
                        requirement_id,
                    ),
                )
                self._replace_attributes(conn, requirement_id, requirement.attributes)
                result = "updated"

            else:
                conn.execute(
                    """
                    UPDATE requirements
                    SET synced_at = ?, is_deleted = ?, unique_identifier = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        int(requirement.is_deleted),
                        requirement.unique_identifier,
                        int(current["id"]),
                    ),
                )
                result = "unchanged"

            if owns_connection:
                conn.commit()
            return result
        finally:
            if owns_connection:
                conn.close()

    @staticmethod
    def _replace_attributes(
        connection: sqlite3.Connection,
        requirement_id: int,
        attributes,
    ) -> None:
        connection.execute(
            "DELETE FROM requirement_attributes WHERE requirement_id = ?",
            (requirement_id,),
        )
        connection.executemany(
            """
            INSERT INTO requirement_attributes(requirement_id, name, value_text)
            VALUES (?, ?, ?)
            """,
            [
                (requirement_id, str(name), str(value))
                for name, value in attributes.items()
            ],
        )

    def mark_missing_as_deleted(
        self,
        module_path: str,
        seen_absolute_numbers: set[int],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        owns_connection = connection is None
        conn = connection or self.connect()
        try:
            existing = conn.execute(
                """
                SELECT id, absolute_number
                FROM requirements
                WHERE module_path = ?
                  AND is_deleted = 0
                """,
                (module_path,),
            ).fetchall()
            missing_ids = [
                int(row["id"])
                for row in existing
                if int(row["absolute_number"]) not in seen_absolute_numbers
            ]
            if missing_ids:
                now = utc_now_iso()
                conn.executemany(
                    """
                    UPDATE requirements
                    SET is_deleted = 1, synced_at = ?
                    WHERE id = ?
                    """,
                    [(now, requirement_id) for requirement_id in missing_ids],
                )
            if owns_connection:
                conn.commit()
            return len(missing_ids)
        finally:
            if owns_connection:
                conn.close()

    def start_sync_run(self, module_path: str) -> int:
        now = utc_now_iso()
        with self.connect() as conn:
            self.ensure_module(module_path, connection=conn)
            cursor = conn.execute(
                """
                INSERT INTO sync_runs(module_path, started_at, status)
                VALUES (?, ?, 'running')
                """,
                (module_path, now),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        requirements_seen: int = 0,
        inserted: int = 0,
        updated: int = 0,
        unchanged: int = 0,
        marked_deleted: int = 0,
        error: str | None = None,
        full_sync_completed: bool = False,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT module_path FROM sync_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"No existe sync_run con id={run_id}")

            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?,
                    status = ?,
                    requirements_seen = ?,
                    inserted = ?,
                    updated = ?,
                    unchanged = ?,
                    marked_deleted = ?,
                    error = ?
                WHERE id = ?
                """,
                (
                    now,
                    status,
                    requirements_seen,
                    inserted,
                    updated,
                    unchanged,
                    marked_deleted,
                    error,
                    run_id,
                ),
            )

            if status == "success":
                if full_sync_completed:
                    conn.execute(
                        """
                        UPDATE modules
                        SET last_sync_at = ?, last_full_sync_at = ?
                        WHERE module_path = ?
                        """,
                        (now, now, row["module_path"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE modules
                        SET last_sync_at = ?
                        WHERE module_path = ?
                        """,
                        (now, row["module_path"]),
                    )
            conn.commit()

    def recent_sync_runs(self, module_path: str, limit: int = 10) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sync_runs
                WHERE module_path = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (module_path, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _requirement_dict(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        include_attributes: bool = True,
    ) -> dict:
        result = dict(row)
        embedding_blob = result.pop("embedding", None)
        result["embedding_available"] = embedding_blob is not None
        result["is_deleted"] = bool(result.get("is_deleted", False))

        if include_attributes:
            attributes = connection.execute(
                """
                SELECT name, value_text
                FROM requirement_attributes
                WHERE requirement_id = ?
                ORDER BY name
                """,
                (int(result["id"]),),
            ).fetchall()
            result["attributes"] = {
                attr["name"]: attr["value_text"] for attr in attributes
            }
        return result

    def get_requirement(self, module_path: str, absolute_number: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM requirements
                WHERE module_path = ? AND absolute_number = ?
                """,
                (module_path, absolute_number),
            ).fetchone()
            if row is None:
                return None
            return self._requirement_dict(conn, row)

    def get_requirement_by_database_id(self, requirement_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM requirements WHERE id = ?",
                (requirement_id,),
            ).fetchone()
            if row is None:
                return None
            return self._requirement_dict(conn, row)

    def find_by_unique_identifier(
        self,
        unique_identifier: str,
        *,
        module_path: str | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        clauses = ["unique_identifier = ?"]
        params: list[object] = [unique_identifier]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        if not include_deleted:
            clauses.append("is_deleted = 0")
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM requirements
                WHERE {' AND '.join(clauses)}
                ORDER BY module_path, absolute_number
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._requirement_dict(conn, row) for row in rows]

    def find_by_identifier(
        self,
        identifier: str,
        *,
        module_path: str | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        clauses = ["identifier = ?"]
        params: list[object] = [identifier]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        if not include_deleted:
            clauses.append("is_deleted = 0")
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM requirements
                WHERE {' AND '.join(clauses)}
                ORDER BY module_path, absolute_number
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._requirement_dict(conn, row) for row in rows]

    def list_requirements(
        self,
        module_path: str,
        *,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[dict]:
        where_deleted = "" if include_deleted else "AND is_deleted = 0"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, module_path, absolute_number, identifier,
                       unique_identifier, outline_number, heading, text,
                       is_deleted, source_last_modified, synced_at,
                       embedding_model, embedding_dimensions,
                       embedding_updated_at,
                       CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END
                           AS embedding_available
                FROM requirements
                WHERE module_path = ?
                  {where_deleted}
                ORDER BY absolute_number
                LIMIT ? OFFSET ?
                """,
                (module_path, limit, offset),
            ).fetchall()
            return [
                {
                    **dict(row),
                    "is_deleted": bool(row["is_deleted"]),
                    "embedding_available": bool(row["embedding_available"]),
                }
                for row in rows
            ]

    def count_requirements(
        self,
        module_path: str,
        *,
        include_deleted: bool = False,
    ) -> int:
        where_deleted = "" if include_deleted else "AND is_deleted = 0"
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM requirements
                WHERE module_path = ?
                  {where_deleted}
                """,
                (module_path,),
            ).fetchone()
            return int(row["count"])

    def embedding_candidates(
        self,
        *,
        model: str,
        module_path: str | None = None,
        force: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        clauses = ["is_deleted = 0"]
        params: list[object] = []
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        if not force:
            clauses.append(
                """
                (
                    embedding IS NULL
                    OR embedding_model IS NULL
                    OR embedding_model <> ?
                    OR embedding_content_hash IS NULL
                    OR embedding_content_hash <> content_hash
                )
                """
            )
            params.append(model)
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ?"
            params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM requirements
                WHERE {' AND '.join(clauses)}
                ORDER BY module_path, absolute_number
                {limit_sql}
                """,
                params,
            ).fetchall()
            return [self._requirement_dict(conn, row) for row in rows]

    def set_embedding(
        self,
        requirement_id: int,
        vector: Sequence[float],
        *,
        model: str,
        content_hash_value: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        conn = connection or self.connect()
        try:
            packed = _pack_embedding(vector)
            conn.execute(
                """
                UPDATE requirements
                SET embedding = ?,
                    embedding_model = ?,
                    embedding_dimensions = ?,
                    embedding_content_hash = ?,
                    embedding_updated_at = ?
                WHERE id = ?
                """,
                (
                    packed,
                    model,
                    len(vector),
                    content_hash_value,
                    utc_now_iso(),
                    requirement_id,
                ),
            )
            if owns_connection:
                conn.commit()
        finally:
            if owns_connection:
                conn.close()

    def embedding_status(
        self,
        *,
        module_path: str | None = None,
        model: str | None = None,
    ) -> dict:
        clauses = ["is_deleted = 0"]
        params: list[object] = []
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        where = " AND ".join(clauses)

        with self.connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM requirements WHERE {where}", params
                ).fetchone()[0]
            )
            embedded = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM requirements
                    WHERE {where}
                      AND embedding IS NOT NULL
                      AND embedding_content_hash = content_hash
                    """,
                    params,
                ).fetchone()[0]
            )
            current_model = None
            if model:
                current_model = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*) FROM requirements
                        WHERE {where}
                          AND embedding IS NOT NULL
                          AND embedding_content_hash = content_hash
                          AND embedding_model = ?
                        """,
                        [*params, model],
                    ).fetchone()[0]
                )
            return {
                "module_path": module_path,
                "model": model,
                "total_active": total,
                "embedded_current_content": embedded,
                "embedded_with_requested_model": current_model,
                "pending_for_requested_model": (
                    None if current_model is None else total - current_model
                ),
            }

    def search_by_embedding(
        self,
        query_embedding: Sequence[float],
        *,
        model: str | None = None,
        module_path: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[dict]:
        if not query_embedding:
            raise ValueError("El embedding de consulta no puede estar vacío.")
        clauses = [
            "is_deleted = 0",
            "embedding IS NOT NULL",
            "embedding_dimensions IS NOT NULL",
            "embedding_content_hash = content_hash",
        ]
        params: list[object] = []
        if model:
            clauses.append("embedding_model = ?")
            params.append(model)
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)

        scored: list[tuple[float, sqlite3.Row]] = []
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM requirements
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
            for row in rows:
                dimensions = int(row["embedding_dimensions"])
                if dimensions != len(query_embedding):
                    continue
                vector = _unpack_embedding(bytes(row["embedding"]), dimensions)
                score = _cosine_similarity(query_embedding, vector)
                if min_score is not None and score < min_score:
                    continue
                scored.append((score, row))

            scored.sort(key=lambda item: item[0], reverse=True)
            results: list[dict] = []
            for score, row in scored[:limit]:
                item = self._requirement_dict(conn, row)
                item["similarity"] = score
                results.append(item)
            return results
