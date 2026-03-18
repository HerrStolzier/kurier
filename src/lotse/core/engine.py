"""Main processing engine — the heart of Lotse."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from lotse.core.classifier import Classifier
from lotse.core.config import LotseConfig
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
        content = self.plugin_manager.hook.pre_classify(content=content, path=str(file_path))
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

        # Step 6: Store in database
        self.store.record_item(
            original_path=str(file_path),
            destination=result.destination,
            category=classification.category,
            confidence=classification.confidence,
            summary=classification.summary,
            tags=classification.tags,
            language=classification.language,
            route_name=result.route_name,
        )

        return result

    def ingest_text(self, text: str, name: str = "text_input") -> RouteResult:
        """Process raw text through the pipeline."""
        classification = self.classifier.classify(text)

        self.store.record_item(
            original_path=f"text://{name}",
            destination="",
            category=classification.category,
            confidence=classification.confidence,
            summary=classification.summary,
            tags=classification.tags,
            language=classification.language,
            route_name="__text__",
        )

        return RouteResult(
            route_name="__text__",
            destination="stored",
            success=True,
            message=f"Text classified: {classification.category} ({classification.confidence:.2f})",
        )

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search stored items using full-text search."""
        return self.store.search(query, limit=limit)

    def stats(self) -> dict:
        """Get processing statistics."""
        return self.store.stats()

    def _extract_content(self, file_path: Path) -> str:
        """Extract text content from a file."""
        mime_type, _ = mimetypes.guess_type(str(file_path))

        # Plain text files
        if mime_type and mime_type.startswith("text/"):
            return file_path.read_text(errors="replace")[:8000]

        # Common text-based formats without proper MIME
        text_extensions = {".md", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".log"}
        if file_path.suffix.lower() in text_extensions:
            return file_path.read_text(errors="replace")[:8000]

        # For binary files, provide metadata as context
        stat = file_path.stat()
        return (
            f"File: {file_path.name}\n"
            f"Type: {mime_type or 'unknown'}\n"
            f"Size: {stat.st_size} bytes\n"
            f"Extension: {file_path.suffix}\n"
        )
