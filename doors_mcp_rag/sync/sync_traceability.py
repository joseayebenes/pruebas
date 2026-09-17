from __future__ import annotations

import argparse
import os
from pathlib import Path

from doors_client import DoorsClient
from repository import RequirementsRepository
from traceability import DoorsTraceabilitySource, TraceabilityRepository, sync_module_traceability


DEFAULT_DB = Path(__file__).with_name("doors_requirements.db")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza relaciones de trazabilidad de IBM DOORS hacia SQLite."
    )
    parser.add_argument(
        "--module",
        default=os.environ.get("DOORS_MODULE_PATH", ""),
        help="Ruta interna completa del módulo DOORS.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Ruta de la base SQLite ya sincronizada con requisitos.",
    )
    parser.add_argument(
        "--direction",
        choices=("incoming", "outgoing", "both"),
        default="both",
        help="Dirección de enlaces a sincronizar.",
    )
    parser.add_argument(
        "--outgoing-only",
        action="store_true",
        help="Atajo equivalente a --direction outgoing; evita cargar módulos origen.",
    )
    parser.add_argument(
        "--allow-incomplete-incoming",
        action="store_true",
        help=(
            "Permite guardar el resultado aunque DOORS no pueda cargar algún "
            "módulo origen. Por defecto se aborta y se conserva la copia anterior."
        ),
    )
    parser.add_argument(
        "--no-login-pause",
        action="store_true",
        help="No esperar ENTER después de crear DOORS.Application.",
    )
    parser.add_argument(
        "--requirement-page-size",
        type=int,
        default=200,
        help="Número de requisitos locales recorridos por página.",
    )
    parser.add_argument(
        "--link-page-size",
        type=int,
        default=500,
        help="Número máximo de enlaces devueltos por llamada DXL y requisito.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    module_path = args.module.strip()
    if not module_path:
        raise SystemExit("Falta --module y DOORS_MODULE_PATH no está definida.")

    direction = "outgoing" if args.outgoing_only else args.direction
    repository = RequirementsRepository(args.db)
    repository.initialise()
    if repository.count_requirements(module_path) == 0:
        raise SystemExit(
            "No hay requisitos activos del módulo en SQLite. Ejecuta primero sync_doors.py."
        )

    client = DoorsClient()
    try:
        print("1. Creando sesión Automation de DOORS...")
        client.start_session()
        if not args.no_login_pause:
            print("Inicia sesión en la ventana Automation de DOORS.")
            input("Cuando DOORS esté listo, pulsa ENTER para continuar... ")

        print(f"2. Comprobando módulo: {module_path}")
        status = client.status(module_path)
        print("   Módulo accesible:", status["module"]["full_name"])

        print(f"3. Sincronizando trazabilidad ({direction})...")
        source = DoorsTraceabilitySource(client)
        stats = sync_module_traceability(
            source,
            repository,
            module_path,
            direction=direction,
            load_incoming_sources=direction in ("incoming", "both"),
            strict_incoming=not args.allow_incomplete_incoming,
            requirement_page_size=args.requirement_page_size,
            link_page_size=args.link_page_size,
        )

        trace_repository = TraceabilityRepository(repository)
        print()
        print("Trazabilidad sincronizada")
        print("------------------------")
        print(f"Requisitos recorridos: {stats.requirements_seen}")
        print(f"Páginas DXL de links:  {stats.link_pages}")
        print(f"Enlaces observados:    {stats.links_seen}")
        print(f"Enlaces almacenados:   {stats.links_stored}")
        print(
            "Fallos carga origen:  ",
            stats.incoming_source_module_load_failures,
        )
        print(f"Links del módulo en DB: {trace_repository.count_links(module_path=module_path)}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
