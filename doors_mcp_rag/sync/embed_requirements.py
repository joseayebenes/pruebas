from __future__ import annotations

import argparse
from pathlib import Path

from embeddings import (
    EmbeddingConfig,
    OpenAICompatibleEmbeddingProvider,
    generate_embeddings,
)
from repository import RequirementsRepository


DEFAULT_DB = Path(__file__).with_name("doors_requirements.db")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calcula embeddings de requisitos ya descargados en SQLite."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Ruta de la base SQLite.")
    parser.add_argument(
        "--module",
        default="",
        help="Limita el cálculo a un módulo. Si se omite, procesa todos.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Sobrescribe DOORS_EMBEDDING_BATCH_SIZE.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Máximo de requisitos a procesar en esta ejecución.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recalcula también embeddings actuales del mismo modelo/contenido.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit debe ser mayor que cero.")

    config = EmbeddingConfig.from_env().with_batch_size(args.batch_size).validate()
    repository = RequirementsRepository(args.db)
    repository.initialise()
    provider = OpenAICompatibleEmbeddingProvider(config)

    module_path = args.module.strip() or None
    print("Configuración de embeddings:")
    for key, value in config.public_dict().items():
        print(f"  {key}: {value}")
    print()

    before = repository.embedding_status(module_path=module_path, model=config.model)
    print("Estado inicial:", before)

    stats = generate_embeddings(
        repository,
        provider,
        module_path=module_path,
        batch_size=config.batch_size,
        max_input_chars=config.max_input_chars,
        force=args.force,
        limit=args.limit,
    )

    print("Resultado:", stats.as_dict())
    print(
        "Estado final:",
        repository.embedding_status(module_path=module_path, model=config.model),
    )


if __name__ == "__main__":
    main()
