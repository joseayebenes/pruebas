from __future__ import annotations

import argparse
import os
from pathlib import Path

from doors_client import DoorsClient
from embeddings import (
    EmbeddingConfig,
    OpenAICompatibleEmbeddingProvider,
    generate_embeddings,
)
from repository import RequirementsRepository
from sync_service import sync_module
from traceability import DoorsTraceabilitySource, TraceabilityRepository, sync_module_traceability


DEFAULT_DB = Path(__file__).with_name("doors_requirements.db")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza un módulo IBM DOORS Classic hacia SQLite."
    )
    parser.add_argument(
        "--module",
        default=os.environ.get("DOORS_MODULE_PATH", ""),
        help="Ruta interna completa del módulo DOORS.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Ruta de la base SQLite.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="Objetos leídos por llamada DXL (default: 50).",
    )
    parser.add_argument(
        "--attributes",
        default="",
        help=(
            "Lista separada por comas de atributos extra. Object Heading, "
            "Object Text y REM_UniqueIdentifier se añaden automáticamente."
        ),
    )
    parser.add_argument(
        "--modified-attribute",
        default="",
        help="Atributo opcional usado como fecha/versión de modificación.",
    )
    parser.add_argument(
        "--max-attribute-chars",
        type=int,
        default=20_000,
        help="Máximo de caracteres leídos por atributo y objeto.",
    )
    parser.add_argument(
        "--no-login-pause",
        action="store_true",
        help="No esperar ENTER después de crear DOORS.Application.",
    )
    parser.add_argument(
        "--sync-links",
        action="store_true",
        help="Tras descargar requisitos, extrae y guarda trazabilidad DOORS.",
    )
    parser.add_argument(
        "--links-direction",
        choices=("incoming", "outgoing", "both"),
        default="both",
        help="Dirección de relaciones a extraer con --sync-links.",
    )
    parser.add_argument(
        "--allow-incomplete-incoming",
        action="store_true",
        help=(
            "Con --sync-links permite guardar enlaces aunque algún módulo origen "
            "no pueda cargarse. Por defecto se conserva la copia anterior."
        ),
    )
    parser.add_argument(
        "--calculate-embeddings",
        action="store_true",
        help=(
            "Tras sincronizar DOORS, calcula los embeddings pendientes usando "
            "DOORS_EMBEDDING_BASE_URL y DOORS_EMBEDDING_MODEL."
        ),
    )
    parser.add_argument(
        "--embedding-force",
        action="store_true",
        help="Recalcula todos los embeddings del módulo.",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=None,
        help="Sobrescribe DOORS_EMBEDDING_BATCH_SIZE para esta ejecución.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    module_path = args.module.strip()
    if not module_path:
        raise SystemExit("Falta --module y DOORS_MODULE_PATH no está definida.")

    extra_attributes = [
        item.strip()
        for item in args.attributes.split(",")
        if item.strip()
    ]

    repository = RequirementsRepository(args.db)
    client = DoorsClient()

    try:
        print("1. Creando sesión Automation de DOORS...")
        client.start_session()
        print("   Sesión Automation creada.")

        if not args.no_login_pause:
            print()
            print("Inicia sesión en la ventana de DOORS que se ha abierto.")
            input("Cuando DOORS esté listo, pulsa ENTER para continuar... ")

        print()
        print(f"2. Comprobando módulo: {module_path}")
        status = client.status(module_path)
        print("   Módulo accesible:", status["module"]["full_name"])

        print()
        print("3. Sincronizando requisitos...")
        stats = sync_module(
            client,
            repository,
            module_path,
            attributes=extra_attributes or None,
            page_size=args.page_size,
            max_attribute_chars=args.max_attribute_chars,
            modified_attribute=args.modified_attribute.strip() or None,
        )

        print()
        print("Sincronización completada")
        print("-------------------------")
        print(f"Páginas:       {stats.pages}")
        print(f"Vistos:        {stats.requirements_seen}")
        print(f"Insertados:    {stats.inserted}")
        print(f"Actualizados:  {stats.updated}")
        print(f"Sin cambios:   {stats.unchanged}")
        print(f"Eliminados:    {stats.marked_deleted}")
        print(f"Base SQLite:   {Path(args.db).resolve()}")

        if args.sync_links:
            print()
            print(f"4. Sincronizando trazabilidad ({args.links_direction})...")
            trace_stats = sync_module_traceability(
                DoorsTraceabilitySource(client),
                repository,
                module_path,
                direction=args.links_direction,
                load_incoming_sources=args.links_direction in ("incoming", "both"),
                strict_incoming=not args.allow_incomplete_incoming,
            )
            trace_repository = TraceabilityRepository(repository)
            print("Trazabilidad:", trace_stats.as_dict())
            print(
                "Links del módulo en DB:",
                trace_repository.count_links(module_path=module_path),
            )

    finally:
        client.close()

    if args.calculate_embeddings:
        print()
        step = "5" if args.sync_links else "4"
        print(f"{step}. Calculando embeddings pendientes...")
        config = (
            EmbeddingConfig.from_env()
            .with_batch_size(args.embedding_batch_size)
            .validate()
        )
        provider = OpenAICompatibleEmbeddingProvider(config)
        embedding_stats = generate_embeddings(
            repository,
            provider,
            module_path=module_path,
            batch_size=config.batch_size,
            max_input_chars=config.max_input_chars,
            force=args.embedding_force,
        )
        print("Embeddings:", embedding_stats.as_dict())
        print(
            "Estado:",
            repository.embedding_status(module_path=module_path, model=config.model),
        )


if __name__ == "__main__":
    main()
