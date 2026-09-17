from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Protocol, Sequence

from repository import RequirementsRepository


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Configuración de un endpoint OpenAI-compatible de embeddings."""

    base_url: str
    model: str
    api_key: str
    batch_size: int = 32
    timeout_seconds: float = 60.0
    max_input_chars: int = 30_000

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        return cls(
            base_url=os.environ.get("DOORS_EMBEDDING_BASE_URL", "").strip(),
            model=os.environ.get("DOORS_EMBEDDING_MODEL", "").strip(),
            api_key=os.environ.get("DOORS_EMBEDDING_API_KEY", "").strip(),
            batch_size=int(os.environ.get("DOORS_EMBEDDING_BATCH_SIZE", "32")),
            timeout_seconds=float(
                os.environ.get("DOORS_EMBEDDING_TIMEOUT_SECONDS", "60")
            ),
            max_input_chars=int(
                os.environ.get("DOORS_EMBEDDING_MAX_INPUT_CHARS", "30000")
            ),
        )

    def validate(self) -> "EmbeddingConfig":
        if not self.base_url:
            raise ValueError(
                "Falta DOORS_EMBEDDING_BASE_URL. Configura la URL base de tu "
                "servidor OpenAI-compatible, por ejemplo http://host:puerto/v1."
            )
        if not self.model:
            raise ValueError(
                "Falta DOORS_EMBEDDING_MODEL. Configura el nombre del modelo de embeddings."
            )
        if self.batch_size < 1:
            raise ValueError("DOORS_EMBEDDING_BATCH_SIZE debe ser mayor que cero.")
        if self.timeout_seconds <= 0:
            raise ValueError("DOORS_EMBEDDING_TIMEOUT_SECONDS debe ser mayor que cero.")
        if self.max_input_chars < 100:
            raise ValueError("DOORS_EMBEDDING_MAX_INPUT_CHARS debe ser al menos 100.")
        return self

    def with_batch_size(self, batch_size: int | None) -> "EmbeddingConfig":
        if batch_size is None:
            return self
        return replace(self, batch_size=batch_size).validate()

    def public_dict(self) -> dict:
        return {
            "base_url": self.base_url or None,
            "model": self.model or None,
            "api_key_configured": bool(self.api_key),
            "batch_size": self.batch_size,
            "timeout_seconds": self.timeout_seconds,
            "max_input_chars": self.max_input_chars,
        }


class EmbeddingProvider(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class OpenAICompatibleEmbeddingProvider:
    """Proveedor usando el SDK de OpenAI contra un `base_url` configurable."""

    def __init__(self, config: EmbeddingConfig):
        self.config = config.validate()
        self.model = self.config.model
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Falta el paquete 'openai'. Ejecuta: python -m pip install -r requirements.txt"
            ) from exc

        # El SDK exige api_key. Muchos servidores locales OpenAI-compatible no
        # validan Authorization, por lo que usamos un valor inocuo si no se define.
        self._client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key or "not-required",
            timeout=self.config.timeout_seconds,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self.config.model,
            input=list(texts),
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [[float(value) for value in item.embedding] for item in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError(
                "El endpoint de embeddings devolvió un número inesperado de vectores."
            )
        if vectors and any(len(vector) != len(vectors[0]) for vector in vectors):
            raise RuntimeError("El endpoint devolvió embeddings con dimensiones distintas.")
        return vectors


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
    """Construye el texto canónico que representa semánticamente un requisito."""
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
    """
    Calcula solo embeddings ausentes/obsoletos, salvo `force=True`.

    Los batches completados se guardan inmediatamente. Si el endpoint falla a
    mitad del proceso, una ejecución posterior continuará con los pendientes.
    """
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
            raise RuntimeError("El proveedor devolvió un número incorrecto de embeddings.")

        if vectors:
            dimensions = len(vectors[0])
            if stats.dimensions is None:
                stats.dimensions = dimensions
            elif dimensions != stats.dimensions:
                raise RuntimeError("La dimensión del embedding cambió durante la ejecución.")

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


def embed_query(
    provider: EmbeddingProvider,
    query: str,
) -> list[float]:
    text = query.strip()
    if not text:
        raise ValueError("La consulta semántica no puede estar vacía.")
    vectors = provider.embed([text])
    if len(vectors) != 1:
        raise RuntimeError("El proveedor no devolvió exactamente un embedding de consulta.")
    return vectors[0]
