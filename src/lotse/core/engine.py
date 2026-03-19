"""Main processing engine — the heart of Lotse."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from lotse.core.classifier import Classification, Classifier
from lotse.core.config import LotseConfig
from lotse.core.embeddings import EmbeddingEngine
from lotse.core.router import Router, RouteResult
from lotse.db.store import Store
from lotse.plugins.manager import PluginManager

logger = logging.getLogger(__name__)


class Engine:
    """Orchestrates the capture → classify → route pipeline."""

    def __init__(self, config: LotseConfig) -> None:
        self.config = config
        self.classifier = Classifier(config.llm)
        self.router = Router(config.routes, config.review_dir)
        self.store = Store(config.database.path)
        self.plugin_manager = PluginManager()
        self._embedder: EmbeddingEngine | None = None

    @property
    def embedder(self) -> EmbeddingEngine:
        """Lazy-load embedding engine (avoids slow model load if not needed)."""
        if self._embedder is None:
            self._embedder = EmbeddingEngine(self.config.embeddings)
        return self._embedder

    def ingest_file(self, file_path: Path) -> RouteResult:
        """Process a single file through the pipeline."""
        logger.info("Ingesting: %s", file_path)

        if not file_path.exists():
            return RouteResult(
                route_name="__error__",
                destination="",
                success=False,
                message=f"File not found: {file_path}",
            )

        # Step 1: Extract content
        content = self._extract_content(file_path)

        # Step 2: Let plugins pre-process
        content = self.plugin_manager.hook.pre_classify(
            content=content, path=str(file_path)
        )
        if isinstance(content, list):
            content = content[-1] if content else ""

        # Step 3: Classify
        classification = self.classifier.classify(content)
        logger.info(
            "Classified %s → %s (%.2f)",
            file_path.name,
            classification.category,
            classification.confidence,
        )

        # Step 4: Let plugins post-process classification
        self.plugin_manager.hook.post_classify(
            classification=classification, path=str(file_path)
        )

        # Step 5: Route
        result = self.router.execute(file_path, classification)

        # Step 6: Generate embedding for semantic search
        embedding = self._generate_embedding(content, classification)

        # Step 7: Store in database (respect privacy setting)
        store_content = self.config.database.store_content
        self.store.record_item(
            original_path=str(file_path),
            destination=result.destination,
            category=classification.category,
            confidence=classification.confidence,
            summary=classification.summary,
            tags=classification.tags,
            language=classification.language,
            route_name=result.route_name,
            content_text=content[:2000] if store_content else "",
            embedding=embedding,
        )

        return result

    def ingest_text(self, text: str, name: str = "text_input") -> RouteResult:
        """Process raw text through the pipeline."""
        classification = self.classifier.classify(text)

        embedding = self._generate_embedding(text, classification)

        self.store.record_item(
            original_path=f"text://{name}",
            destination="",
            category=classification.category,
            confidence=classification.confidence,
            summary=classification.summary,
            tags=classification.tags,
            language=classification.language,
            route_name="__text__",
            content_text=text[:2000] if self.config.database.store_content else "",
            embedding=embedding,
        )

        return RouteResult(
            route_name="__text__",
            destination="stored",
            success=True,
            message=(
                f"Text classified: {classification.category}"
                f" ({classification.confidence:.2f})"
            ),
        )

    def search(
        self, query: str, limit: int = 20, mode: str = "auto"
    ) -> list[dict[str, Any]]:
        """Search stored items. Supports keyword, semantic, and hybrid search."""
        query_embedding = None
        if mode in ("auto", "vec") and self.store.vec_enabled:
            try:
                query_embedding = self.embedder.embed_query(query)
            except Exception as e:
                logger.warning("Embedding query failed, falling back to FTS: %s", e)

        return self.store.search(
            query, limit=limit, query_embedding=query_embedding, mode=mode
        )

    def stats(self) -> dict[str, Any]:
        """Get processing statistics."""
        return self.store.stats()

    def _generate_embedding(self, content: str, classification: Classification) -> bytes | None:
        """Generate an embedding combining content + classification metadata."""
        if not self.store.vec_enabled:
            return None

        # Combine content with classification for richer embedding
        embed_text = (
            f"{classification.summary} "
            f"{classification.category} "
            f"{' '.join(classification.tags)} "
            f"{content[:1500]}"
        )

        try:
            return self.embedder.embed_text(embed_text)
        except Exception as e:
            logger.warning("Embedding generation failed: %s", e)
            return None

    def _extract_content(self, file_path: Path) -> str:
        """Extract text content from a file."""
        mime_type, _ = mimetypes.guess_type(str(file_path))

        # Plain text files
        if mime_type and mime_type.startswith("text/"):
            return file_path.read_text(errors="replace")[:8000]

        # Common text-based formats without proper MIME
        text_extensions = {
            ".md", ".json", ".yaml", ".yml", ".toml",
            ".csv", ".tsv", ".log",
        }
        if file_path.suffix.lower() in text_extensions:
            return file_path.read_text(errors="replace")[:8000]

        # Try OCR for PDFs and images
        from lotse.core.ocr import extract_text, is_ocr_candidate

        if is_ocr_candidate(file_path):
            text = extract_text(file_path)
            if text:
                logger.info("OCR extracted %d chars from %s", len(text), file_path.name)
                return text[:8000]

        # For binary files, provide metadata as context
        stat = file_path.stat()
        return (
            f"File: {file_path.name}\n"
            f"Type: {mime_type or 'unknown'}\n"
            f"Size: {stat.st_size} bytes\n"
            f"Extension: {file_path.suffix}\n"
        )
