from __future__ import annotations

import argparse
import os
from pathlib import Path

from daisei_embedding import DaiseiEmbeddingProvider
from embeddings import generate_embeddings
from repository import RequirementsRepository


DEFAULT_DB = Path(__file__).with_name("doors_requirements.db")
DEFAULT_BATCH_SIZE = int(os.environ.get("DAISEI_EMBEDDING_BATCH_SIZE", "32"))
DEFAULT_MAX_INPUT_CHARS = int(os.environ.get("DAISEI_EMBEDDING_MAX_INPUT_CHARS", "30000"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calcula con Daisei embeddings de requisitos ya descargados en SQLite."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Ruta de la base SQLite.")
    parser.add_argument(
        "--module",
        default="",
        help="Limita el calculo a un modulo. Si se omite, procesa todos.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximo de requisitos a procesar en esta ejecucion.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recalcula embeddings actuales del mismo modelo/contenido.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit debe ser mayor que cero.")

    repository = RequirementsRepository(args.db)
    repository.initialise()
    module_path = args.module.strip() or None

    with DaiseiEmbeddingProvider() as provider:
        print(f"Modelo Daisei: {provider.model}")
        before = repository.embedding_status(module_path=module_path, model=provider.model)
        print("Estado inicial:", before)

        stats = generate_embeddings(
            repository,
            provider,
            module_path=module_path,
            batch_size=DEFAULT_BATCH_SIZE,
            max_input_chars=DEFAULT_MAX_INPUT_CHARS,
            force=args.force,
            limit=args.limit,
        )

        print("Resultado:", stats.as_dict())
        print(
            "Estado final:",
            repository.embedding_status(module_path=module_path, model=provider.model),
        )


if __name__ == "__main__":
    main()
