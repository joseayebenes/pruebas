from __future__ import annotations

import math
import os
from numbers import Real
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv
from daisei import Daisei, ConnectionConfig


# La configuracion de Daisei vive fuera del MCP. Se busca primero un .env en
# la raiz de doors_mcp_rag, independientemente del directorio desde el que VS
# Code arranque el servidor MCP. ConnectionConfig.from_env() encapsula proxy,
# certificados, API key, base_url, timeout, redirects, etc.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ENV = PROJECT_ROOT / ".env"
if PROJECT_ENV.exists():
    load_dotenv(PROJECT_ENV)
else:
    load_dotenv()

DEFAULT_EMBEDDING_MODEL = os.environ.get(
    "DAISEI_EMBEDDING_MODEL",
    "text-embedding-gte-multilingual-base",
).strip()


class DaiseiEmbeddingError(RuntimeError):
    pass


def _numeric_vector(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if not all(isinstance(item, Real) and not isinstance(item, bool) for item in value):
        return None
    vector = [float(item) for item in value]
    if not all(math.isfinite(item) for item in vector):
        raise DaiseiEmbeddingError("Daisei devolvio un embedding con valores no finitos.")
    return vector


def _extract_embedding(response: Any) -> list[float]:
    """Acepta las formas de respuesta mas comunes sin acoplar el MCP al SDK."""
    direct = _numeric_vector(response)
    if direct is not None:
        return direct

    if isinstance(response, dict):
        direct = _numeric_vector(response.get("embedding"))
        if direct is not None:
            return direct

        data = response.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                direct = _numeric_vector(first.get("embedding"))
                if direct is not None:
                    return direct
            direct = _numeric_vector(getattr(first, "embedding", None))
            if direct is not None:
                return direct

    direct = _numeric_vector(getattr(response, "embedding", None))
    if direct is not None:
        return direct

    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        direct = _numeric_vector(getattr(data[0], "embedding", None))
        if direct is not None:
            return direct

    raise DaiseiEmbeddingError(
        "No se pudo extraer el vector de la respuesta de Daisei. "
        f"Tipo recibido: {type(response).__name__}."
    )


class DaiseiEmbeddingProvider:
    """Adaptador minimo de Daisei para embeddings.

    No usa chat, chat_stream, analyze_image ni list_models. El unico metodo de
    inferencia utilizado es create_embedding(text, model).
    """

    def __init__(self, model: str | None = None):
        self.model = (model or DEFAULT_EMBEDDING_MODEL).strip()
        if not self.model:
            raise ValueError("El modelo de embeddings de Daisei no puede estar vacio.")
        self._client: Daisei | None = None

    def connect(self) -> None:
        if self._client is not None:
            return
        config = ConnectionConfig.from_env()
        client = Daisei(config)
        client.connect()
        self._client = client

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()

    def embed_one(self, text: str) -> list[float]:
        value = text.strip()
        if not value:
            raise ValueError("No se puede calcular el embedding de un texto vacio.")
        self.connect()
        assert self._client is not None
        response = self._client.create_embedding(value, self.model)
        return _extract_embedding(response)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # Daisei expone create_embedding para un texto. Mantenemos esta interfaz
        # por lotes para el generador offline de embeddings de requisitos.
        vectors = [self.embed_one(text) for text in texts]
        if vectors and any(len(vector) != len(vectors[0]) for vector in vectors):
            raise DaiseiEmbeddingError(
                "Daisei devolvio embeddings con dimensiones distintas."
            )
        return vectors

    def __enter__(self) -> "DaiseiEmbeddingProvider":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
