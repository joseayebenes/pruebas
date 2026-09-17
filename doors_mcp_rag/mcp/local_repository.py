from __future__ import annotations

import math
import sqlite3
import struct
from pathlib import Path
from typing import Literal, Sequence
from urllib.parse import quote


Direction = Literal["incoming", "outgoing", "both"]


class LocalDatabaseError(RuntimeError):
    pass


class LocalRequirementsRepository:
    """Repositorio estrictamente de solo lectura para el MCP.

    La conexion usa SQLite `mode=ro`, de forma que una tool MCP no puede
    modificar accidentalmente requisitos, embeddings o trazabilidad.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        if not self.database_path.is_file():
            raise LocalDatabaseError(
                f"No existe la base SQLite: {self.database_path}"
            )
        self.validate_schema()

    def connect(self) -> sqlite3.Connection:
        encoded = quote(self.database_path.as_posix(), safe="/:")
        connection = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _table_exists(self, connection: sqlite3.Connection, table: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _columns(self, connection: sqlite3.Connection, table: str) -> set[str]:
        if not self._table_exists(connection, table):
            return set()
        return {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def validate_schema(self) -> None:
        with self.connect() as connection:
            required_tables = {"requirements", "requirement_attributes"}
            missing = [
                table
                for table in required_tables
                if not self._table_exists(connection, table)
            ]
            if missing:
                raise LocalDatabaseError(
                    "La base no tiene el esquema esperado. Faltan: "
                    + ", ".join(sorted(missing))
                )

            columns = self._columns(connection, "requirements")
            required_columns = {
                "id",
                "module_path",
                "absolute_number",
                "identifier",
                "unique_identifier",
                "heading",
                "text",
                "is_deleted",
                "content_hash",
                "embedding",
                "embedding_model",
                "embedding_dimensions",
                "embedding_content_hash",
            }
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                raise LocalDatabaseError(
                    "La tabla requirements necesita migracion. Faltan columnas: "
                    + ", ".join(missing_columns)
                )

    @staticmethod
    def _unpack_embedding(blob: bytes, dimensions: int) -> list[float]:
        if dimensions <= 0:
            raise LocalDatabaseError("embedding_dimensions invalido.")
        expected = dimensions * 4
        if len(blob) != expected:
            raise LocalDatabaseError(
                f"Embedding BLOB invalido: {len(blob)} bytes, esperados {expected}."
            )
        return list(struct.unpack(f"<{dimensions}f", blob))

    @staticmethod
    def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            raise ValueError("Los embeddings deben tener la misma dimension.")
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

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
                str(attr["name"]): str(attr["value_text"])
                for attr in attributes
            }
        return result

    def list_modules(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT module_path,
                       COUNT(*) AS total,
                       SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN is_deleted = 0 AND embedding IS NOT NULL
                                AND embedding_content_hash = content_hash
                           THEN 1 ELSE 0 END) AS embedded
                FROM requirements
                GROUP BY module_path
                ORDER BY module_path
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def status(self) -> dict:
        with self.connect() as connection:
            requirements = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN is_deleted = 0 AND embedding IS NOT NULL
                                AND embedding_content_hash = content_hash
                           THEN 1 ELSE 0 END) AS embedded
                FROM requirements
                """
            ).fetchone()
            links = 0
            if self._table_exists(connection, "links"):
                links = int(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0])
            models = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT embedding_model
                    FROM requirements
                    WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL
                    ORDER BY embedding_model
                    """
                ).fetchall()
            ]
            return {
                "database_path": str(self.database_path),
                "requirements_total": int(requirements["total"] or 0),
                "requirements_active": int(requirements["active"] or 0),
                "requirements_embedded": int(requirements["embedded"] or 0),
                "traceability_links": links,
                "embedding_models": models,
                "modules": self.list_modules(),
                "read_only": True,
            }

    def get_by_database_id(self, requirement_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM requirements WHERE id = ?",
                (requirement_id,),
            ).fetchone()
            if row is None:
                return None
            return self._requirement_dict(connection, row)

    def _find_exact(
        self,
        column: str,
        value: object,
        *,
        module_path: str | None = None,
        limit: int = 20,
        include_deleted: bool = False,
    ) -> list[dict]:
        allowed = {"identifier", "unique_identifier", "absolute_number"}
        if column not in allowed:
            raise ValueError(f"Columna de busqueda no permitida: {column}")
        clauses = [f"{column} = ?"]
        params: list[object] = [value]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        if not include_deleted:
            clauses.append("is_deleted = 0")
        params.append(limit)
        sql = (
            "SELECT * FROM requirements WHERE "
            + " AND ".join(clauses)
            + " ORDER BY module_path, absolute_number LIMIT ?"
        )
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [self._requirement_dict(connection, row) for row in rows]

    def find_by_unique_identifier(
        self, value: str, *, module_path: str | None = None, limit: int = 20
    ) -> list[dict]:
        return self._find_exact(
            "unique_identifier", value, module_path=module_path, limit=limit
        )

    def find_by_identifier(
        self, value: str, *, module_path: str | None = None, limit: int = 20
    ) -> list[dict]:
        return self._find_exact("identifier", value, module_path=module_path, limit=limit)

    def find_by_absolute_number(
        self, value: int, *, module_path: str | None = None, limit: int = 20
    ) -> list[dict]:
        return self._find_exact(
            "absolute_number", value, module_path=module_path, limit=limit
        )

    def search_text(
        self,
        query: str,
        *,
        module_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        value = query.strip()
        if not value:
            raise ValueError("query no puede estar vacia.")
        clauses = [
            "is_deleted = 0",
            "(identifier LIKE ? OR unique_identifier LIKE ? OR heading LIKE ? OR text LIKE ?)",
        ]
        like = f"%{value}%"
        params: list[object] = [like, like, like, like]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requirements WHERE "
                + " AND ".join(clauses)
                + " ORDER BY module_path, absolute_number LIMIT ?",
                params,
            ).fetchall()
            return [self._requirement_dict(connection, row) for row in rows]

    def search_by_embedding(
        self,
        query_vector: Sequence[float],
        *,
        model: str,
        module_path: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[dict]:
        vector = [float(value) for value in query_vector]
        if not vector:
            raise ValueError("El embedding de consulta esta vacio.")

        clauses = [
            "is_deleted = 0",
            "embedding IS NOT NULL",
            "embedding_model = ?",
            "embedding_dimensions = ?",
            "embedding_content_hash = content_hash",
        ]
        params: list[object] = [model, len(vector)]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM requirements WHERE " + " AND ".join(clauses),
                params,
            ).fetchall()
            scored: list[tuple[float, sqlite3.Row]] = []
            for row in rows:
                stored = self._unpack_embedding(
                    bytes(row["embedding"]), int(row["embedding_dimensions"])
                )
                score = self._cosine_similarity(vector, stored)
                if min_score is None or score >= min_score:
                    scored.append((score, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            results: list[dict] = []
            for score, row in scored[:limit]:
                item = self._requirement_dict(connection, row)
                item["similarity"] = score
                results.append(item)
            return results

    def get_relations(
        self,
        module_path: str,
        absolute_number: int,
        *,
        direction: Direction = "both",
        limit: int = 200,
    ) -> list[dict]:
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction debe ser incoming, outgoing o both.")

        with self.connect() as connection:
            if not self._table_exists(connection, "links"):
                return []
            link_columns = self._columns(connection, "links")
            synced_select = "l.synced_at" if "synced_at" in link_columns else "NULL AS synced_at"

            clauses: list[str] = []
            params: list[object] = []
            if direction in ("outgoing", "both"):
                clauses.append("(l.source_module_path = ? AND l.source_absolute_number = ?)")
                params.extend([module_path, absolute_number])
            if direction in ("incoming", "both"):
                clauses.append("(l.target_module_path = ? AND l.target_absolute_number = ?)")
                params.extend([module_path, absolute_number])
            params.append(limit)

            rows = connection.execute(
                f"""
                SELECT l.id AS link_id, l.link_module_path, {synced_select},
                       l.source_module_path, l.source_absolute_number,
                       l.target_module_path, l.target_absolute_number,
                       sr.id AS source_database_id,
                       sr.identifier AS source_identifier,
                       sr.unique_identifier AS source_unique_identifier,
                       sr.heading AS source_heading,
                       tr.id AS target_database_id,
                       tr.identifier AS target_identifier,
                       tr.unique_identifier AS target_unique_identifier,
                       tr.heading AS target_heading
                FROM links l
                LEFT JOIN requirements sr
                  ON sr.module_path = l.source_module_path
                 AND sr.absolute_number = l.source_absolute_number
                LEFT JOIN requirements tr
                  ON tr.module_path = l.target_module_path
                 AND tr.absolute_number = l.target_absolute_number
                WHERE {' OR '.join(clauses)}
                ORDER BY l.source_module_path, l.source_absolute_number,
                         l.target_module_path, l.target_absolute_number
                LIMIT ?
                """,
                params,
            ).fetchall()

        results: list[dict] = []
        for row in rows:
            source = {
                "module_path": row["source_module_path"],
                "absolute_number": int(row["source_absolute_number"]),
                "database_id": row["source_database_id"],
                "identifier": row["source_identifier"],
                "unique_identifier": row["source_unique_identifier"],
                "heading": row["source_heading"],
                "available_locally": row["source_database_id"] is not None,
            }
            target = {
                "module_path": row["target_module_path"],
                "absolute_number": int(row["target_absolute_number"]),
                "database_id": row["target_database_id"],
                "identifier": row["target_identifier"],
                "unique_identifier": row["target_unique_identifier"],
                "heading": row["target_heading"],
                "available_locally": row["target_database_id"] is not None,
            }
            is_outgoing = (
                source["module_path"] == module_path
                and source["absolute_number"] == absolute_number
            )
            relation_direction = "outgoing" if is_outgoing else "incoming"
            related = target if is_outgoing else source
            results.append(
                {
                    "link_id": int(row["link_id"]),
                    "direction": relation_direction,
                    "link_module_path": row["link_module_path"],
                    "synced_at": row["synced_at"],
                    "source": source,
                    "target": target,
                    "related_requirement": related,
                }
            )
        return results
