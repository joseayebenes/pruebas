from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

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
    outline_number TEXT NOT NULL DEFAULT '',
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,
    source_last_modified TEXT,
    content_hash TEXT NOT NULL,
    synced_at TEXT NOT NULL,

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
                        outline_number,
                        heading,
                        text,
                        is_deleted,
                        source_last_modified,
                        content_hash,
                        synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        requirement.module_path,
                        requirement.absolute_number,
                        requirement.identifier,
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
                        outline_number = ?,
                        heading = ?,
                        text = ?,
                        is_deleted = ?,
                        source_last_modified = ?,
                        content_hash = ?,
                        synced_at = ?
                    WHERE id = ?
                    """,
                    (
                        requirement.identifier,
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
                    SET synced_at = ?, is_deleted = ?
                    WHERE id = ?
                    """,
                    (now, int(requirement.is_deleted), int(current["id"])),
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
        """Registra el comienzo de una sincronización completa."""
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
        """Cierra una ejecución de sincronización y actualiza el módulo."""
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

            attributes = conn.execute(
                """
                SELECT name, value_text
                FROM requirement_attributes
                WHERE requirement_id = ?
                ORDER BY name
                """,
                (int(row["id"]),),
            ).fetchall()

            result = dict(row)
            result["is_deleted"] = bool(result["is_deleted"])
            result["attributes"] = {
                attr["name"]: attr["value_text"] for attr in attributes
            }
            return result

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
                       outline_number, heading, text, is_deleted,
                       source_last_modified, synced_at
                FROM requirements
                WHERE module_path = ?
                  {where_deleted}
                ORDER BY absolute_number
                LIMIT ? OFFSET ?
                """,
                (module_path, limit, offset),
            ).fetchall()
            return [
                {**dict(row), "is_deleted": bool(row["is_deleted"])}
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
