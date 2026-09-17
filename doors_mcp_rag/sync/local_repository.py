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


def _unpack_embedding(blob: bytes, dimensions: int) -> list[float]:
    if dimensions <= 0:
        raise LocalDatabaseError("embedding_dimensions debe ser mayor que cero.")
    expected = dimensions * 4
    if len(blob) != expected:
        raise LocalDatabaseError(
            f"Embedding BLOB invalido: {len(blob)} bytes; se esperaban {expected}."
        )
    return list(struct.unpack(f"<{dimensions}f", blob))


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Los embeddings deben tener la misma dimension y no estar vacios.")
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _like_pattern(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class LocalRequirementsRepository:
    """Repositorio de consulta estrictamente read-only para el MCP.

    Esta clase NO importa DoorsClient, no ejecuta DXL y nunca abre la base con
    permisos de escritura. El fichero SQLite debe haber sido generado antes por
    el proceso de sincronizacion/ingesta.
    """

    REQUIRED_REQUIREMENT_COLUMNS = {
        "id",
        "module_path",
        "absolute_number",
        "identifier",
        "unique_identifier",
        "outline_number",
        "heading",
        "text",
        "is_deleted",
        "content_hash",
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_content_hash",
    }

    def __init__(self, database_path: str | Path):
        path = Path(database_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"No existe la base SQLite: {path}")
        if not path.is_file():
            raise LocalDatabaseError(f"La ruta no es un fichero SQLite: {path}")
        self.database_path = path.resolve()
        self._validate_schema()

    def connect(self) -> sqlite3.Connection:
        # mode=ro hace que SQLite rechace cualquier escritura aunque una futura
        # modificacion del MCP introduzca por error un UPDATE/INSERT/DELETE.
        sqlite_path = quote(self.database_path.as_posix(), safe="/:")
        connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
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

    def _validate_schema(self) -> None:
        with self.connect() as connection:
            if not self._table_exists(connection, "requirements"):
                raise LocalDatabaseError(
                    "La base no contiene la tabla requirements. Ejecuta antes la ingesta."
                )
            columns = self._columns(connection, "requirements")
            missing = sorted(self.REQUIRED_REQUIREMENT_COLUMNS - columns)
            if missing:
                raise LocalDatabaseError(
                    "La base usa un esquema antiguo; faltan columnas: " + ", ".join(missing)
                )

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
            attributes: dict[str, str] = {}
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='requirement_attributes'"
            ).fetchone()
            if table_exists is not None:
                rows = connection.execute(
                    """
                    SELECT name, value_text
                    FROM requirement_attributes
                    WHERE requirement_id = ?
                    ORDER BY name
                    """,
                    (int(result["id"]),),
                ).fetchall()
                attributes = {str(item["name"]): str(item["value_text"]) for item in rows}
            result["attributes"] = attributes
        return result

    def status(self) -> dict:
        with self.connect() as connection:
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM requirements WHERE is_deleted = 0"
                ).fetchone()[0]
            )
            deleted = int(
                connection.execute(
                    "SELECT COUNT(*) FROM requirements WHERE is_deleted <> 0"
                ).fetchone()[0]
            )
            embedded = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM requirements
                    WHERE is_deleted = 0
                      AND embedding IS NOT NULL
                      AND embedding_content_hash = content_hash
                    """
                ).fetchone()[0]
            )
            models = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT embedding_model
                    FROM requirements
                    WHERE is_deleted = 0
                      AND embedding IS NOT NULL
                      AND embedding_content_hash = content_hash
                      AND embedding_model IS NOT NULL
                    ORDER BY embedding_model
                    """
                ).fetchall()
            ]
            modules = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT module_path) FROM requirements"
                ).fetchone()[0]
            )
            links = 0
            if self._table_exists(connection, "links"):
                links = int(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0])

        return {
            "database_path": str(self.database_path),
            "read_only": True,
            "source": "sqlite_only",
            "modules": modules,
            "active_requirements": active,
            "deleted_requirements": deleted,
            "current_embeddings": embedded,
            "embedding_models": models,
            "links": links,
        }

    def list_modules(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    module_path,
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) AS active,
                    SUM(
                        CASE WHEN is_deleted = 0
                                  AND embedding IS NOT NULL
                                  AND embedding_content_hash = content_hash
                             THEN 1 ELSE 0 END
                    ) AS embedded
                FROM requirements
                GROUP BY module_path
                ORDER BY module_path
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_by_database_id(self, requirement_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM requirements WHERE id = ?",
                (requirement_id,),
            ).fetchone()
            return None if row is None else self._requirement_dict(connection, row)

    def _find_exact(
        self,
        column: str,
        value: object,
        *,
        module_path: str | None,
        limit: int,
    ) -> list[dict]:
        allowed = {"unique_identifier", "identifier", "absolute_number"}
        if column not in allowed:
            raise ValueError(f"Columna de busqueda no permitida: {column}")
        clauses = [f"{column} = ?", "is_deleted = 0"]
        params: list[object] = [value]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM requirements
                WHERE {' AND '.join(clauses)}
                ORDER BY module_path, absolute_number
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._requirement_dict(connection, row) for row in rows]

    def find_by_unique_identifier(
        self,
        unique_identifier: str,
        *,
        module_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return self._find_exact(
            "unique_identifier", unique_identifier, module_path=module_path, limit=limit
        )

    def find_by_identifier(
        self,
        identifier: str,
        *,
        module_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return self._find_exact("identifier", identifier, module_path=module_path, limit=limit)

    def find_by_absolute_number(
        self,
        absolute_number: int,
        *,
        module_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return self._find_exact(
            "absolute_number", absolute_number, module_path=module_path, limit=limit
        )

    def search_text(
        self,
        query: str,
        *,
        module_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        text = query.strip()
        if not text:
            raise ValueError("query no puede estar vacio.")
        pattern = _like_pattern(text)
        clauses = [
            "r.is_deleted = 0",
            """
            (
                r.identifier LIKE ? ESCAPE '\\'
                OR COALESCE(r.unique_identifier, '') LIKE ? ESCAPE '\\'
                OR r.outline_number LIKE ? ESCAPE '\\'
                OR r.heading LIKE ? ESCAPE '\\'
                OR r.text LIKE ? ESCAPE '\\'
                OR EXISTS (
                    SELECT 1 FROM requirement_attributes a
                    WHERE a.requirement_id = r.id
                      AND (a.name LIKE ? ESCAPE '\\' OR a.value_text LIKE ? ESCAPE '\\')
                )
            )
            """,
        ]
        params: list[object] = [pattern] * 7
        if module_path:
            clauses.append("r.module_path = ?")
            params.append(module_path)
        params.append(limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*
                FROM requirements r
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE WHEN r.unique_identifier = ? THEN 0
                         WHEN r.identifier = ? THEN 1
                         ELSE 2 END,
                    r.module_path,
                    r.absolute_number
                LIMIT ?
                """,
                [*params[:-1], text, text, params[-1]],
            ).fetchall()
            return [self._requirement_dict(connection, row) for row in rows]

    def search_by_embedding(
        self,
        query_embedding: Sequence[float],
        *,
        model: str,
        module_path: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[dict]:
        if not query_embedding:
            raise ValueError("El embedding de consulta no puede estar vacio.")
        query_vector = [float(value) for value in query_embedding]
        if not all(math.isfinite(value) for value in query_vector):
            raise ValueError("El embedding de consulta contiene valores no finitos.")

        clauses = [
            "is_deleted = 0",
            "embedding IS NOT NULL",
            "embedding_dimensions IS NOT NULL",
            "embedding_content_hash = content_hash",
            "embedding_model = ?",
        ]
        params: list[object] = [model]
        if module_path:
            clauses.append("module_path = ?")
            params.append(module_path)

        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM requirements WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()
            scored: list[tuple[float, sqlite3.Row]] = []
            for row in rows:
                dimensions = int(row["embedding_dimensions"])
                if dimensions != len(query_vector):
                    continue
                vector = _unpack_embedding(bytes(row["embedding"]), dimensions)
                score = _cosine_similarity(query_vector, vector)
                if min_score is not None and score < min_score:
                    continue
                scored.append((score, row))

            scored.sort(key=lambda item: item[0], reverse=True)
            result: list[dict] = []
            for score, row in scored[:limit]:
                item = self._requirement_dict(connection, row)
                item["similarity"] = score
                result.append(item)
            return result

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
            synced_expr = "l.synced_at" if "synced_at" in link_columns else "NULL"

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
                SELECT
                    l.id AS link_id,
                    l.link_module_path,
                    {synced_expr} AS synced_at,
                    l.source_module_path,
                    l.source_absolute_number,
                    l.target_module_path,
                    l.target_absolute_number,
                    sr.id AS source_database_id,
                    sr.identifier AS source_identifier,
                    sr.unique_identifier AS source_unique_identifier,
                    sr.outline_number AS source_outline_number,
                    sr.heading AS source_heading,
                    tr.id AS target_database_id,
                    tr.identifier AS target_identifier,
                    tr.unique_identifier AS target_unique_identifier,
                    tr.outline_number AS target_outline_number,
                    tr.heading AS target_heading
                FROM links l
                LEFT JOIN requirements sr
                  ON sr.module_path = l.source_module_path
                 AND sr.absolute_number = l.source_absolute_number
                LEFT JOIN requirements tr
                  ON tr.module_path = l.target_module_path
                 AND tr.absolute_number = l.target_absolute_number
                WHERE {' OR '.join(clauses)}
                ORDER BY l.link_module_path,
                         l.source_module_path, l.source_absolute_number,
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
                "outline_number": row["source_outline_number"],
                "heading": row["source_heading"],
                "available_locally": row["source_database_id"] is not None,
            }
            target = {
                "module_path": row["target_module_path"],
                "absolute_number": int(row["target_absolute_number"]),
                "database_id": row["target_database_id"],
                "identifier": row["target_identifier"],
                "unique_identifier": row["target_unique_identifier"],
                "outline_number": row["target_outline_number"],
                "heading": row["target_heading"],
                "available_locally": row["target_database_id"] is not None,
            }
            is_outgoing = (
                source["module_path"] == module_path
                and source["absolute_number"] == absolute_number
            )
            is_incoming = (
                target["module_path"] == module_path
                and target["absolute_number"] == absolute_number
            )
            relation_direction = "both" if is_outgoing and is_incoming else (
                "outgoing" if is_outgoing else "incoming"
            )
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
