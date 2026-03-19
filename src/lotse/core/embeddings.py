"""Embedding engine for semantic search using FastEmbed."""

from __future__ import annotations

import logging
import struct
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from fastembed import TextEmbedding as TextEmbeddingType
    from lotse.core.config import EmbeddingConfig

logger = logging.getLogger(__name__)

# BAAI/bge-small-en-v1.5 produces 384-dimensional vectors
EMBEDDING_DIM = 384


class EmbeddingEngine:
    """Generates text embeddings using FastEmbed (ONNX-based, local inference)."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model: Optional[TextEmbeddingType] = None

    @property
    def model(self) -> TextEmbeddingType:
        """Lazy-load the embedding model on first use."""
        if self._model is None:
            from fastembed import TextEmbedding

            logger.info("Loading embedding model: %s", self.config.model)
            if self.config.cache_dir:
                self._model = TextEmbedding(
                    model_name=self.config.model,
                    cache_dir=str(self.config.cache_dir),
                )
            else:
                self._model = TextEmbedding(model_name=self.config.model)
        return self._model

    def embed_text(self, text: str) -> bytes:
        """Embed a single text and return as bytes for SQLite storage."""
        embeddings = list(self.model.embed([text]))
        return _float_list_to_bytes(embeddings[0].tolist())

    def embed_query(self, query: str) -> bytes:
        """Embed a search query. Returns bytes for sqlite-vec MATCH."""
        return self.embed_text(query)

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        """Embed multiple texts at once."""
        embeddings = list(self.model.embed(texts))
        return [_float_list_to_bytes(e.tolist()) for e in embeddings]


def _float_list_to_bytes(floats: list[float]) -> bytes:
    """Pack a list of floats into a bytes blob (little-endian float32)."""
    return struct.pack(f"<{len(floats)}f", *floats)


def _bytes_to_float_list(data: bytes) -> list[float]:
    """Unpack a bytes blob into a list of floats."""
    count = len(data) // 4
    return list(struct.unpack(f"<{count}f", data))
