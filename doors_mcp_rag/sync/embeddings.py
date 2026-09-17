from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from repository import RequirementsRepository


class EmbeddingProvider(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


@dataclass(slots=True)
class EmbeddingStats:
    candidates: int = 0
    embedded: int = 0
    batches: int = 0
    dimensions: int | None = None

    def as_dict(self) -> dict:
        return {
            "candidates": self.candidates,
            "embedded": self.embedded,
            "batches": self.batches,
            "dimensions": self.dimensions,
        }


def build_requirement_embedding_text(
    requirement: dict,
    *,
    max_chars: int = 30_000,
) -> str:
    """Construye el texto canonico que representa semanticamente un requisito."""
    parts: list[str] = []

    def add(label: str, value: object) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            parts.append(f"{label}: {text}")

    add("Module", requirement.get("module_path"))
    add("UniqueIdentifier", requirement.get("unique_identifier"))
    add("DOORS Identifier", requirement.get("identifier"))
    add("Outline", requirement.get("outline_number"))
    add("Heading", requirement.get("heading"))
    add("Text", requirement.get("text"))

    attributes = requirement.get("attributes") or {}
    excluded = {"Object Heading", "Object Text", "REM_UniqueIdentifier"}
    if isinstance(attributes, dict):
        for name in sorted(attributes):
            if name in excluded:
                continue
            value = str(attributes[name]).strip()
            if value:
                parts.append(f"Attribute {name}: {value}")

    text = "\n".join(parts)
    if not text:
        text = f"Requirement {requirement.get('id', '')}"
    return text[:max_chars]


def generate_embeddings(
    repository: RequirementsRepository,
    provider: EmbeddingProvider,
    *,
    module_path: str | None = None,
    batch_size: int = 32,
    max_input_chars: int = 30_000,
    force: bool = False,
    limit: int | None = None,
) -> EmbeddingStats:
    """Calcula embeddings ausentes u obsoletos y los persiste en SQLite."""
    if batch_size < 1:
        raise ValueError("batch_size debe ser mayor que cero.")

    repository.initialise()
    candidates = repository.embedding_candidates(
        model=provider.model,
        module_path=module_path,
        force=force,
        limit=limit,
    )
    stats = EmbeddingStats(candidates=len(candidates))

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        texts = [
            build_requirement_embedding_text(item, max_chars=max_input_chars)
            for item in batch
        ]
        vectors = provider.embed(texts)
        if len(vectors) != len(batch):
            raise RuntimeError("El proveedor devolvio un numero incorrecto de embeddings.")

        if vectors:
            dimensions = len(vectors[0])
            if stats.dimensions is None:
                stats.dimensions = dimensions
            elif dimensions != stats.dimensions:
                raise RuntimeError("La dimension del embedding cambio durante la ejecucion.")

        with repository.transaction() as connection:
            for requirement, vector in zip(batch, vectors):
                repository.set_embedding(
                    int(requirement["id"]),
                    vector,
                    model=provider.model,
                    content_hash_value=str(requirement["content_hash"]),
                    connection=connection,
                )
                stats.embedded += 1

        stats.batches += 1

    return stats


def embed_query(provider: EmbeddingProvider, query: str) -> list[float]:
    text = query.strip()
    if not text:
        raise ValueError("La consulta semantica no puede estar vacia.")
    vectors = provider.embed([text])
    if len(vectors) != 1:
        raise RuntimeError("El proveedor no devolvio exactamente un embedding de consulta.")
    return vectors[0]
