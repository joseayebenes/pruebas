from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from doors_client import (
    DoorsClient,
    DoorsError,
    _dxl_bool,
    _dxl_helpers,
    _dxl_preamble,
    _module_context_dxl,
)
from repository import RequirementsRepository, utc_now_iso


Direction = Literal["incoming", "outgoing", "both"]


@dataclass(frozen=True, slots=True)
class TraceLink:
    source_module_path: str
    source_absolute_number: int
    target_module_path: str
    target_absolute_number: int
    link_module_path: str = ""

    @classmethod
    def from_doors(cls, item: dict) -> "TraceLink":
        source = item.get("source") or {}
        target = item.get("target") or {}
        return cls(
            source_module_path=str(source.get("module_path", "")),
            source_absolute_number=int(source.get("absolute_number", 0)),
            target_module_path=str(target.get("module_path", "")),
            target_absolute_number=int(target.get("absolute_number", 0)),
            link_module_path=str(item.get("link_module_path", "")),
        )


@dataclass(slots=True)
class TraceabilitySyncStats:
    module_path: str
    direction: Direction
    requirements_seen: int = 0
    link_pages: int = 0
    links_seen: int = 0
    links_stored: int = 0
    incoming_source_module_load_failures: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class DoorsTraceabilitySource:
    """Extrae enlaces estándar internos de DOORS mediante DXL.

    Los enlaces se normalizan siempre como source -> target, aunque se consulten
    desde el extremo entrante. Los enlaces externos OSLC no se incluyen.
    """

    def __init__(self, client: DoorsClient):
        self.client = client

    def get_requirement_links(
        self,
        module_path: str,
        absolute_number: int,
        *,
        direction: Direction = "both",
        offset: int = 0,
        limit: int = 500,
        load_incoming_sources: bool = True,
    ) -> dict:
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction debe ser incoming, outgoing o both.")
        if absolute_number < 1:
            raise ValueError("absolute_number debe ser >= 1.")
        if offset < 0:
            raise ValueError("offset debe ser >= 0.")
        if limit < 1 or limit > 5000:
            raise ValueError("limit debe estar entre 1 y 5000.")

        include_incoming = direction in ("incoming", "both")
        include_outgoing = direction in ("outgoing", "both")

        dxl = (
            _dxl_preamble()
            + _dxl_helpers()
            + "\n"
            + r'''
void appendLinkJson(Buffer output, Link linkValue, string directionValue) {
    ModName_ sourceModuleRef = source(linkValue)
    ModName_ targetModuleRef = target(linkValue)
    Module linkModule = module(linkValue)

    output += "{\"direction\":"
    appendJsonString(output, directionValue)
    output += ",\"link_module_path\":"
    if (null linkModule) appendJsonString(output, "")
    else appendJsonString(output, fullName(linkModule))
    output += ",\"source\":{\"module_path\":"
    appendJsonString(output, fullName(sourceModuleRef))
    output += ",\"absolute_number\":"
    output += (sourceAbsNo(linkValue) "")
    output += "},\"target\":{\"module_path\":"
    appendJsonString(output, fullName(targetModuleRef))
    output += ",\"absolute_number\":"
    output += (targetAbsNo(linkValue) "")
    output += "}}"
}
'''
            + f"int requestedAbsoluteNumber = {int(absolute_number)}\n"
            + f"int requestedOffset = {int(offset)}\n"
            + f"int requestedLimit = {int(limit)}\n"
            + f"bool requestedIncoming = {_dxl_bool(include_incoming)}\n"
            + f"bool requestedOutgoing = {_dxl_bool(include_outgoing)}\n"
            + f"bool requestedLoadIncomingSources = {_dxl_bool(load_incoming_sources)}\n"
            + _module_context_dxl(module_path)
            + r'''
Object obj = object(requestedAbsoluteNumber, currentModule)
if (null obj) {
    Buffer notFound = create
    notFound += "{\"ok\":false,\"error\":\"No existe el objeto con Absolute Number "
    notFound += (requestedAbsoluteNumber "")
    notFound += " en el módulo seleccionado.\"}"
    oleSetResult(stringOf(notFound))
    delete notFound
    halt
}

int sourceModuleLoadFailures = 0
if (requestedIncoming && requestedLoadIncomingSources) {
    ModName_ sourceModuleRef
    for sourceModuleRef in obj <- "*" do {
        string sourcePath = fullName(sourceModuleRef)
        noError
        Module loadedSourceModule = read(sourcePath, false, true)
        string sourceLoadError = lastError
        if (!null sourceLoadError || null loadedSourceModule) {
            sourceModuleLoadFailures++
        }
    }
    current = currentModule
}

Buffer output = create
output += "{\"ok\":true,\"absolute_number\":"
output += (requestedAbsoluteNumber "")
output += ",\"incoming_source_module_load_failures\":"
output += (sourceModuleLoadFailures "")
output += ",\"external_links_included\":false"
output += ",\"offset\":"
output += (requestedOffset "")
output += ",\"limit\":"
output += (requestedLimit "")
output += ",\"links\":["

int eligibleIndex = 0
int returned = 0
bool hasMore = false
bool firstLink = true

if (requestedOutgoing) {
    Link outgoingLink
    for outgoingLink in obj -> "*" do {
        if (eligibleIndex >= requestedOffset) {
            if (returned < requestedLimit) {
                if (!firstLink) output += ","
                firstLink = false
                appendLinkJson(output, outgoingLink, "outgoing")
                returned++
            } else {
                hasMore = true
                break
            }
        }
        eligibleIndex++
    }
}

if (requestedIncoming && !hasMore) {
    Link incomingLink
    for incomingLink in obj <- "*" do {
        if (eligibleIndex >= requestedOffset) {
            if (returned < requestedLimit) {
                if (!firstLink) output += ","
                firstLink = false
                appendLinkJson(output, incomingLink, "incoming")
                returned++
            } else {
                hasMore = true
                break
            }
        }
        eligibleIndex++
    }
}

output += "],\"returned\":"
output += (returned "")
output += ",\"has_more\":"
appendJsonBool(output, hasMore)
output += ",\"next_offset\":"
if (hasMore) output += ((requestedOffset + returned) "")
else output += "null"
output += "}"
oleSetResult(stringOf(output))
delete output
'''
        )
        return self.client._run_json(dxl)


class TraceabilityRepository:
    """Persistencia y consulta del grafo de trazabilidad en la misma SQLite."""

    def __init__(self, requirements_repository: RequirementsRepository):
        self.requirements = requirements_repository

    def initialise(self) -> None:
        self.requirements.initialise()
        with self.requirements.connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(links)").fetchall()
            }
            if "synced_at" not in columns:
                conn.execute("ALTER TABLE links ADD COLUMN synced_at TEXT")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traceability_sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_path TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    requirements_seen INTEGER NOT NULL DEFAULT 0,
                    links_seen INTEGER NOT NULL DEFAULT 0,
                    links_stored INTEGER NOT NULL DEFAULT 0,
                    incoming_source_module_load_failures INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traceability_sync_module "
                "ON traceability_sync_runs(module_path, id)"
            )
            conn.commit()

    def start_sync_run(self, module_path: str, direction: Direction) -> int:
        self.initialise()
        with self.requirements.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO traceability_sync_runs(
                    module_path, direction, started_at, status
                ) VALUES (?, ?, ?, 'running')
                """,
                (module_path, direction, utc_now_iso()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        stats: TraceabilitySyncStats,
        error: str | None = None,
    ) -> None:
        with self.requirements.connect() as conn:
            conn.execute(
                """
                UPDATE traceability_sync_runs
                SET finished_at = ?, status = ?, requirements_seen = ?,
                    links_seen = ?, links_stored = ?,
                    incoming_source_module_load_failures = ?, error = ?
                WHERE id = ?
                """,
                (
                    utc_now_iso(),
                    status,
                    stats.requirements_seen,
                    stats.links_seen,
                    stats.links_stored,
                    stats.incoming_source_module_load_failures,
                    error,
                    run_id,
                ),
            )
            conn.commit()

    def replace_links_for_module(
        self,
        module_path: str,
        links: set[TraceLink],
        *,
        direction: Direction,
    ) -> int:
        self.initialise()
        with self.requirements.transaction() as conn:
            if direction == "outgoing":
                conn.execute(
                    "DELETE FROM links WHERE source_module_path = ?",
                    (module_path,),
                )
            elif direction == "incoming":
                conn.execute(
                    "DELETE FROM links WHERE target_module_path = ?",
                    (module_path,),
                )
            else:
                conn.execute(
                    "DELETE FROM links WHERE source_module_path = ? OR target_module_path = ?",
                    (module_path, module_path),
                )

            now = utc_now_iso()
            conn.executemany(
                """
                INSERT OR IGNORE INTO links(
                    source_module_path,
                    source_absolute_number,
                    target_module_path,
                    target_absolute_number,
                    link_module_path,
                    synced_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        link.source_module_path,
                        link.source_absolute_number,
                        link.target_module_path,
                        link.target_absolute_number,
                        link.link_module_path,
                        now,
                    )
                    for link in sorted(
                        links,
                        key=lambda value: (
                            value.source_module_path,
                            value.source_absolute_number,
                            value.target_module_path,
                            value.target_absolute_number,
                            value.link_module_path,
                        ),
                    )
                ],
            )
        return len(links)

    def count_links(self, *, module_path: str | None = None) -> int:
        self.initialise()
        with self.requirements.connect() as conn:
            if module_path:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM links
                    WHERE source_module_path = ? OR target_module_path = ?
                    """,
                    (module_path, module_path),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS count FROM links").fetchone()
            return int(row["count"])

    def get_relations(
        self,
        module_path: str,
        absolute_number: int,
        *,
        direction: Direction = "both",
        limit: int = 200,
    ) -> list[dict]:
        self.initialise()
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction debe ser incoming, outgoing o both.")
        if limit < 1 or limit > 5000:
            raise ValueError("limit debe estar entre 1 y 5000.")

        clauses: list[str] = []
        params: list[object] = []
        if direction in ("outgoing", "both"):
            clauses.append("(l.source_module_path = ? AND l.source_absolute_number = ?)")
            params.extend([module_path, absolute_number])
        if direction in ("incoming", "both"):
            clauses.append("(l.target_module_path = ? AND l.target_absolute_number = ?)")
            params.extend([module_path, absolute_number])
        params.append(limit)

        where = " OR ".join(clauses)
        sql = f"""
            SELECT
                l.id AS link_id,
                l.link_module_path,
                l.synced_at,
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
            WHERE {where}
            ORDER BY l.link_module_path, l.source_module_path,
                     l.source_absolute_number, l.target_module_path,
                     l.target_absolute_number
            LIMIT ?
        """

        with self.requirements.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

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
            if is_outgoing and is_incoming:
                relation_direction = "both"
                related = target
            elif is_outgoing:
                relation_direction = "outgoing"
                related = target
            else:
                relation_direction = "incoming"
                related = source

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

    def recent_sync_runs(self, module_path: str, limit: int = 10) -> list[dict]:
        self.initialise()
        with self.requirements.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM traceability_sync_runs
                WHERE module_path = ?
                ORDER BY id DESC LIMIT ?
                """,
                (module_path, limit),
            ).fetchall()
            return [dict(row) for row in rows]


def sync_module_traceability(
    source: DoorsTraceabilitySource,
    requirements_repository: RequirementsRepository,
    module_path: str,
    *,
    direction: Direction = "both",
    load_incoming_sources: bool = True,
    strict_incoming: bool = True,
    requirement_page_size: int = 200,
    link_page_size: int = 500,
) -> TraceabilitySyncStats:
    """Sincroniza relaciones para los requisitos activos ya descargados.

    La sustitución en SQLite solo ocurre al final. Si la extracción falla, se
    conservan los enlaces que ya existían. En modo `both`/`incoming`, si DOORS
    no puede cargar un módulo origen y strict_incoming=True, se aborta para no
    reemplazar una vista completa por otra incompleta.
    """
    if direction not in ("incoming", "outgoing", "both"):
        raise ValueError("direction debe ser incoming, outgoing o both.")

    trace_repository = TraceabilityRepository(requirements_repository)
    run_id = trace_repository.start_sync_run(module_path, direction)
    stats = TraceabilitySyncStats(module_path=module_path, direction=direction)
    collected: set[TraceLink] = set()

    try:
        offset = 0
        while True:
            requirements = requirements_repository.list_requirements(
                module_path,
                limit=requirement_page_size,
                offset=offset,
                include_deleted=False,
            )
            if not requirements:
                break

            for requirement in requirements:
                absolute_number = int(requirement["absolute_number"])
                stats.requirements_seen += 1
                link_offset = 0

                while True:
                    page = source.get_requirement_links(
                        module_path,
                        absolute_number,
                        direction=direction,
                        offset=link_offset,
                        limit=link_page_size,
                        load_incoming_sources=load_incoming_sources,
                    )
                    stats.link_pages += 1
                    failures = int(
                        page.get("incoming_source_module_load_failures", 0) or 0
                    )
                    stats.incoming_source_module_load_failures += failures
                    if failures and strict_incoming and direction in ("incoming", "both"):
                        raise DoorsError(
                            "DOORS no pudo cargar uno o más módulos origen al extraer "
                            f"enlaces entrantes del objeto {absolute_number}. "
                            "Se conserva la trazabilidad local anterior."
                        )

                    raw_links = page.get("links", [])
                    if not isinstance(raw_links, list):
                        raise DoorsError("DOORS devolvió 'links' con formato no válido.")
                    for raw in raw_links:
                        if not isinstance(raw, dict):
                            raise DoorsError("DOORS devolvió un enlace no válido.")
                        link = TraceLink.from_doors(raw)
                        if (
                            link.source_module_path
                            and link.target_module_path
                            and link.source_absolute_number > 0
                            and link.target_absolute_number > 0
                        ):
                            collected.add(link)
                            stats.links_seen += 1

                    if not bool(page.get("has_more", False)):
                        break
                    next_offset = page.get("next_offset")
                    if not isinstance(next_offset, int) or next_offset <= link_offset:
                        raise DoorsError("Paginación de enlaces inválida devuelta por DOORS.")
                    link_offset = next_offset

            offset += len(requirements)
            if len(requirements) < requirement_page_size:
                break

        stats.links_stored = trace_repository.replace_links_for_module(
            module_path,
            collected,
            direction=direction,
        )
        trace_repository.finish_sync_run(run_id, status="success", stats=stats)
        return stats
    except Exception as exc:
        trace_repository.finish_sync_run(
            run_id,
            status="failed",
            stats=stats,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
