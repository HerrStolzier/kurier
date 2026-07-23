"""Main processing engine — the heart of Kurier."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from arkiv.core.classifier import Classification, Classifier
from arkiv.core.config import ArkivConfig
from arkiv.core.embeddings import EmbeddingEngine
from arkiv.core.router import Router, RouteResult
from arkiv.core.search_assistant import QueryAssist, QueryAssistant
from arkiv.db.store import Store
from arkiv.plugins.manager import PluginManager

logger = logging.getLogger(__name__)


def _normalize_search_text(value: str) -> str:
    return " ".join(value.casefold().split())


class Engine:
    """Orchestrates the capture → classify → route pipeline."""

    def __init__(self, config: ArkivConfig) -> None:
        self.config = config
        self.classifier = Classifier(config.llm, arkiv_config=config)
        self.store = Store(config.database.path)
        self.router = Router(config.routes, config.review_dir, store=self.store)
        self.plugin_manager = PluginManager()
        self._embedder: EmbeddingEngine | None = None
        self._query_assistant: QueryAssistant | None = None

    @property
    def embedder(self) -> EmbeddingEngine:
        """Lazy-load embedding engine (avoids slow model load if not needed)."""
        if self._embedder is None:
            self._embedder = EmbeddingEngine(self.config.embeddings)
        return self._embedder

    @property
    def query_assistant(self) -> QueryAssistant:
        """Lazy-load query assistant to keep normal search lightweight."""
        if self._query_assistant is None:
            self._query_assistant = QueryAssistant(self.config.llm, arkiv_config=self.config)
        return self._query_assistant

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

        # Skip empty files — LLM will hallucinate on empty input
        if not content.strip():
            logger.warning("Skipping empty file: %s", file_path.name)
            return RouteResult(
                route_name="__error__",
                destination="",
                success=False,
                message=f"Empty file: {file_path.name}",
            )

        # Step 2: Let plugins pre-process (pluggy returns list of hook results)
        hook_results = self.plugin_manager.hook.pre_classify(content=content, path=str(file_path))
        if hook_results:
            # Use the last plugin's transformed content
            content = hook_results[-1]

        # Step 3: Classify
        classification = self.classifier.classify(content)
        logger.info(
            "Classified %s → %s (%.2f)",
            file_path.name,
            classification.category,
            classification.confidence,
        )

        # Step 4: Let plugins post-process classification
        self.plugin_manager.hook.post_classify(classification=classification, path=str(file_path))

        # Step 5: Store with status='pending' to get item_id before routing
        store_content = self.config.database.store_content
        item_id = self.store.record_item(
            original_path=str(file_path),
            destination="",
            category=classification.category,
            confidence=classification.confidence,
            summary=classification.summary,
            tags=classification.tags,
            language=classification.language,
            route_name="__pending__",
            suggested_filename=classification.suggested_filename,
            content_text=content[:2000] if store_content else "",
            status="pending",
        )

        # Step 6: Try to route
        try:
            result = self.router.execute(file_path, classification, item_id=item_id)
            # Status folgt dem Ergebnis: ein RouteResult mit success=False
            # (Webhook-only-Fehlschlag, Folder-Route ohne Pfad) ist nicht "routed".
            self.store.update_status(item_id, "routed" if result.success else "failed")
            self.store.update_routing_metadata(item_id, result.destination, result.route_name)
            if not result.success:
                # Schliesst das Race mit einem parallel laufenden
                # `kurier webhooks retry`: Wurde die Outbox-Zeile schon
                # zugestellt, bevor der failed-Status geschrieben war,
                # setzt reconcile das Item jetzt wieder auf routed.
                self.store.reconcile_item_after_webhook_delivery(item_id)

            if result.success:
                try:
                    self.plugin_manager.hook.on_routed(
                        path=str(file_path),
                        destination=result.destination,
                        route_name=result.route_name,
                    )
                except Exception as exc:
                    logger.warning("on_routed plugin hook failed (non-fatal): %s", exc)
        except Exception as exc:
            logger.warning("Routing failed for %s: %s", file_path.name, exc)
            self.store.update_status(item_id, "failed")
            result = RouteResult(
                route_name="__failed__",
                destination="",
                success=False,
                message=f"Routing failed: {exc}",
            )

        # Step 7: Embed (best-effort, don't fail on this)
        try:
            embedding = self._generate_embedding(content, classification)
            if embedding and self.store.vec_enabled:
                self.store._conn.execute(
                    "INSERT OR REPLACE INTO items_vec(rowid, embedding) VALUES (?, ?)",
                    (item_id, embedding),
                )
                self.store._conn.commit()
        except Exception as exc:
            logger.warning("Embedding failed for %s (non-fatal): %s", file_path.name, exc)

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
            suggested_filename=classification.suggested_filename,
            content_text=text[:2000] if self.config.database.store_content else "",
            embedding=embedding,
        )

        return RouteResult(
            route_name="__text__",
            destination="stored",
            success=True,
            message=(
                f"Text classified: {classification.category} ({classification.confidence:.2f})"
            ),
        )

    def search(
        self,
        query: str,
        limit: int = 20,
        mode: str = "auto",
        memory: bool = False,
    ) -> list[dict[str, Any]]:
        """Search stored items. Supports keyword, semantic, hybrid, and memory-assist."""
        results, _ = self.search_with_assist(query, limit=limit, mode=mode, memory=memory)
        return results

    def search_with_assist(
        self,
        query: str,
        limit: int = 20,
        mode: str = "auto",
        memory: bool = False,
    ) -> tuple[list[dict[str, Any]], QueryAssist | None]:
        """Search stored items and optionally return query-assist metadata."""
        assist = self.query_assistant.assist(query) if memory else None
        queries = assist.queries(query) if assist else [query]

        if len(queries) == 1:
            results = self._search_single_query(queries[0], limit=limit, mode=mode)
            return self._annotate_search_results(results, query, assist), assist

        results = self._search_multi_query(queries, limit=limit, mode=mode, assist=assist)
        return self._annotate_search_results(results, query, assist), assist

    def _search_single_query(
        self,
        query: str,
        limit: int,
        mode: str,
    ) -> list[dict[str, Any]]:
        """Run one query through the existing search pipeline."""
        query_embedding = None
        if mode in ("auto", "vec") and self.store.vec_enabled:
            try:
                query_embedding = self.embedder.embed_query(query)
            except Exception as e:
                logger.warning("Embedding query failed, falling back to FTS: %s", e)

        return self.store.search(query, limit=limit, query_embedding=query_embedding, mode=mode)

    def _search_multi_query(
        self,
        queries: list[str],
        limit: int,
        mode: str,
        assist: QueryAssist | None = None,
    ) -> list[dict[str, Any]]:
        """Run multiple rewritten queries and merge them with a simple RRF pass."""
        fused_scores: dict[int, float] = {}
        fused_items: dict[int, dict[str, Any]] = {}
        matched_queries: dict[int, list[str]] = {}
        matched_filters: dict[int, list[str]] = {}
        k = 60

        for query_index, query in enumerate(queries):
            per_query_limit = max(limit, 10)
            try:
                results = self._search_single_query(query, limit=per_query_limit, mode=mode)
            except Exception as exc:
                logger.warning("Search query variant failed for %r: %s", query, exc)
                continue
            query_weight = 1.2 if query_index == 0 else 1.0
            for rank_pos, item in enumerate(results, 1):
                item_id = item["id"]
                fused_scores[item_id] = fused_scores.get(item_id, 0.0) + (
                    query_weight / (k + rank_pos)
                )
                fused_items[item_id] = item
                matched_queries.setdefault(item_id, []).append(query)

        if assist:
            for item_id, item in fused_items.items():
                filter_hits = self._match_assist_filters(item, assist)
                if filter_hits:
                    fused_scores[item_id] = fused_scores.get(item_id, 0.0) + (
                        0.015 * len(filter_hits)
                    )
                    matched_filters[item_id] = filter_hits

        top_ids = sorted(
            fused_scores,
            key=lambda item_id: fused_scores[item_id],
            reverse=True,
        )[:limit]
        merged_results: list[dict[str, Any]] = []
        for item_id in top_ids:
            item = dict(fused_items[item_id])
            item["memory_score"] = fused_scores[item_id]
            item["matched_queries"] = matched_queries.get(item_id, [])
            item["matched_filters"] = matched_filters.get(item_id, [])
            merged_results.append(item)
        return merged_results

    def _match_assist_filters(
        self,
        item: dict[str, Any],
        assist: QueryAssist,
    ) -> list[str]:
        """Return human-readable filter matches for an item."""
        hits: list[str] = []

        normalized_category = _normalize_search_text(item.get("category", ""))
        for category in assist.filters.get("category", []):
            if normalized_category == _normalize_search_text(category):
                hits.append(f"Kategorie: {category}")

        haystack_parts = [
            item.get("display_title", ""),
            item.get("destination_name", ""),
            item.get("summary", ""),
            item.get("tags", ""),
            item.get("original_path", ""),
        ]
        haystack = _normalize_search_text(" ".join(str(part) for part in haystack_parts if part))

        for key, label in (
            ("organizations", "Organisation"),
            ("topics", "Thema"),
            ("date_hints", "Zeit"),
            ("location_hints", "Ort"),
            ("locations", "Ort"),
        ):
            for value in assist.filters.get(key, []):
                normalized_value = _normalize_search_text(value)
                if normalized_value and normalized_value in haystack:
                    hits.append(f"{label}: {value}")

        deduped: list[str] = []
        seen: set[str] = set()
        for hit in hits:
            key = hit.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hit)
        return deduped

    def _annotate_search_results(
        self,
        results: list[dict[str, Any]],
        original_query: str,
        assist: QueryAssist | None,
    ) -> list[dict[str, Any]]:
        """Attach a short, deterministic explanation to each result."""
        annotated: list[dict[str, Any]] = []
        for item in results:
            annotated_item = dict(item)
            annotated_item["match_reason"] = self._build_match_reason(
                annotated_item,
                original_query,
                assist,
            )
            annotated.append(annotated_item)
        return annotated

    def _build_match_reason(
        self,
        item: dict[str, Any],
        original_query: str,
        assist: QueryAssist | None,
    ) -> str:
        """Build a short explanation for why this item matched."""
        reasons: list[str] = []
        matched_filters = item.get("matched_filters") or []
        if matched_filters:
            reasons.append(", ".join(matched_filters[:2]))

        matched_queries = item.get("matched_queries") or []
        rewritten_queries = [
            query
            for query in matched_queries
            if _normalize_search_text(query) != _normalize_search_text(original_query)
        ]
        if rewritten_queries:
            reasons.append(f"Suchvariante: {rewritten_queries[0]}")

        if not reasons:
            title = _normalize_search_text(item.get("display_title", ""))
            summary = _normalize_search_text(item.get("summary", ""))
            query_terms = [
                term for term in _normalize_search_text(original_query).split() if len(term) >= 4
            ]
            title_hits = [term for term in query_terms if term in title]
            summary_hits = [term for term in query_terms if term in summary]
            if title_hits:
                reasons.append("Titel passt zur Anfrage")
            elif summary_hits:
                reasons.append("Zusammenfassung passt zur Anfrage")
            elif assist and assist.filters.get("category"):
                reasons.append(f"Kategorie passt: {assist.filters['category'][0]}")
            else:
                reasons.append("Treffer aus dem Suchindex")

        return "Passt wegen " + "; ".join(reasons[:2]) + "."

    def stats(self) -> dict[str, Any]:
        """Get processing statistics."""
        return self.store.stats()

    def _generate_embedding(self, content: str, classification: Classification) -> bytes | None:
        """Generate an embedding combining content + classification metadata."""
        if not self.store.vec_enabled:
            return None

        # Combine content with classification for richer embedding
        embed_text = (
            f"{classification.suggested_filename} "
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
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".csv",
            ".tsv",
            ".log",
        }
        if file_path.suffix.lower() in text_extensions:
            return file_path.read_text(errors="replace")[:8000]

        # Try OCR for PDFs and images
        from arkiv.core.ocr import extract_text, is_ocr_candidate

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
