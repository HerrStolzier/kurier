"""Route matching and execution engine."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from arkiv.core.classifier import Classification
from arkiv.core.config import RouteConfig

if TYPE_CHECKING:
    from arkiv.db.store import Store

logger = logging.getLogger(__name__)

# Characters not allowed in filenames (Windows + macOS + Linux)
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 200
_FILE_EXTENSIONS = {
    "pdf",
    "txt",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "csv",
    "json",
    "xml",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "bmp",
    "webp",
    "tiff",
    "svg",
    "py",
    "js",
    "ts",
    "html",
    "css",
    "md",
    "yaml",
    "yml",
    "toml",
    "zip",
    "tar",
    "gz",
    "rar",
    "eml",
    "mbox",
}


def _build_filename(classification: Classification, extension: str) -> str:
    """Build a human-readable filename from classification data.

    Format: DD.MM.YYYY Suggested Filename.ext
    Example: 23.03.2026 Rechnung Telekom März.pdf
    """
    today = date.today().strftime("%d.%m.%Y")
    name = classification.suggested_filename.strip()

    # Remove unsafe characters
    name = _UNSAFE_CHARS.sub("", name)

    # Underscores → spaces (LLMs sometimes ignore the prompt instruction)
    name = name.replace("_", " ")

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()

    # Only strip actual file extensions, not dates like "20.03" or words like "Nr.123"
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        if ext.lower() in _FILE_EXTENSIONS:
            name = stem.rstrip()

    if not name:
        name = classification.category.capitalize() if classification.category else "Dokument"

    full = f"{today} {name}{extension}"

    # Truncate if too long (keep date + extension intact)
    if len(full) > _MAX_FILENAME_LEN:
        available = _MAX_FILENAME_LEN - len(today) - len(extension) - 2
        name = name[:available].rstrip()
        full = f"{today} {name}{extension}"

    return full


def _unique_destination_path(dest_path: Path) -> Path:
    """Return a non-existing path by appending _1, _2, ... when needed."""
    if not dest_path.exists():
        return dest_path

    stem = dest_path.stem
    suffix = dest_path.suffix
    counter = 1
    while dest_path.exists():
        dest_path = dest_path.with_name(f"{stem}_{counter}{suffix}")
        counter += 1
    return dest_path


@dataclass
class RouteResult:
    """Result of routing an item."""

    route_name: str
    destination: str
    success: bool
    message: str


class Router:
    """Matches classifications to routes and executes them."""

    def __init__(
        self,
        routes: dict[str, RouteConfig],
        review_dir: Path,
        store: Store | None = None,
    ) -> None:
        self.routes = routes
        self.review_dir = review_dir
        self.store = store

    def find_routes(self, classification: Classification) -> list[tuple[str, RouteConfig]]:
        """Find ALL matching routes for a classification (supports fan-out)."""
        matches = []
        for name, route in self.routes.items():
            cat_match = (
                classification.category in route.categories
                or not route.categories  # empty = wildcard
            )
            if cat_match and classification.confidence >= route.confidence_threshold:
                matches.append((name, route))
        return matches

    def find_route(self, classification: Classification) -> tuple[str, RouteConfig] | None:
        """Find the best matching route for a classification."""
        matches = self.find_routes(classification)
        return matches[0] if matches else None

    def execute(
        self,
        source_path: Path,
        classification: Classification,
        item_id: int | None = None,
    ) -> RouteResult:
        """Route a file based on its classification.

        Primary route (first match with type=folder) moves the file.
        Additional webhook routes fire in parallel without moving the file.
        """
        matches = self.find_routes(classification)

        if not matches:
            return self._route_to_review(source_path, classification)

        # Separate folder routes (file-moving) from webhook routes (fire-and-forget)
        primary_result = None  # first SUCCESSFUL folder move
        first_folder_failure = None
        first_webhook_failure = None
        first_result = None
        for route_name, route_config in matches:
            # Nach einem erfolgreichen Move existiert source_path nicht mehr —
            # weitere Folder-Routes würden auf dem toten Pfad crashen.
            if route_config.type == "folder" and primary_result is not None:
                logger.info(
                    "Skipping folder route %s: file already moved by %s",
                    route_name,
                    primary_result.route_name,
                )
                continue

            result = self._execute_route(
                source_path,
                route_name,
                route_config,
                classification,
                item_id=item_id,
            )
            if first_result is None:
                first_result = result
            if route_config.type == "folder":
                if result.success:
                    primary_result = result
                else:
                    # Fehlgeschlagene Folder-Route (z. B. ohne path) hat die
                    # Datei nicht bewegt — die nächste darf als Fallback ziehen.
                    logger.warning("Folder route %s failed: %s", route_name, result.message)
                    if first_folder_failure is None:
                        first_folder_failure = result
            elif route_config.type == "webhook" and not result.success:
                logger.warning("Webhook %s failed: %s", route_name, result.message)
                if first_webhook_failure is None:
                    first_webhook_failure = result

        # Ohne Folder-Move zaehlt ein Webhook-Fehlschlag als Gesamt-Fehlschlag
        # (unabhaengig von der Routen-Reihenfolge) — der Status wird nach
        # erfolgreichem Retry via reconcile wieder auf routed gesetzt.
        return (
            primary_result
            or first_folder_failure
            or first_webhook_failure
            or first_result
            or self._route_to_review(source_path, classification)
        )

    def _execute_route(
        self,
        source_path: Path,
        route_name: str,
        route_config: RouteConfig,
        classification: Classification,
        item_id: int | None = None,
    ) -> RouteResult:
        """Execute a specific route."""
        if route_config.type == "folder":
            return self._route_to_folder(source_path, route_name, route_config, classification)
        elif route_config.type == "webhook":
            return self._route_to_webhook(
                source_path,
                route_name,
                route_config,
                classification,
                item_id=item_id,
            )
        else:
            logger.warning("Unknown route type: %s", route_config.type)
            return RouteResult(
                route_name=route_name,
                destination="unknown",
                success=False,
                message=f"Unknown route type: {route_config.type}",
            )

    def _route_to_folder(
        self,
        source_path: Path,
        route_name: str,
        route_config: RouteConfig,
        classification: Classification,
        item_id: int | None = None,
    ) -> RouteResult:
        """Route a file to a folder destination."""
        if not route_config.path:
            return RouteResult(
                route_name=route_name,
                destination="",
                success=False,
                message="No path configured for folder route",
            )

        dest_dir = Path(route_config.path).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)

        if route_config.rename and classification.suggested_filename:
            new_name = _build_filename(classification, source_path.suffix)
            dest_path = dest_dir / new_name
        else:
            dest_path = dest_dir / source_path.name

        dest_path = _unique_destination_path(dest_path)

        shutil.move(str(source_path), str(dest_path))
        logger.info("Routed %s → %s (%s)", source_path.name, dest_path, route_name)

        return RouteResult(
            route_name=route_name,
            destination=str(dest_path),
            success=True,
            message=f"Routed to {route_name}: {dest_path}",
        )

    def _route_to_webhook(
        self,
        source_path: Path,
        route_name: str,
        route_config: RouteConfig,
        classification: Classification,
        item_id: int | None = None,
    ) -> RouteResult:
        """Route item data to a webhook URL (does NOT move the file)."""
        if not route_config.url:
            return RouteResult(
                route_name=route_name,
                destination="",
                success=False,
                message="No URL configured for webhook route",
            )

        item_data = self._build_webhook_payload(source_path, route_name, classification)

        try:
            from arkiv_webhook import send_webhook

            success = send_webhook(route_config.url, item_data)
            if success:
                return RouteResult(
                    route_name=route_name,
                    destination=route_config.url,
                    success=True,
                    message=f"Webhook delivered: {route_name}",
                )
            self._enqueue_failed_webhook(
                item_id=item_id,
                route_name=route_name,
                url=route_config.url,
                payload=item_data,
                error=f"Webhook delivery failed: {route_name}",
            )
            return RouteResult(
                route_name=route_name,
                destination=route_config.url,
                success=False,
                message=f"Webhook delivery failed: {route_name}",
            )

        except ImportError:
            logger.warning(
                "Webhook route '%s' configured but arkiv-webhook not installed. "
                "Install with: pip install arkiv-webhook",
                route_name,
            )
            self._enqueue_failed_webhook(
                item_id=item_id,
                route_name=route_name,
                url=route_config.url,
                payload=item_data,
                error="arkiv-webhook plugin not installed",
            )
            return RouteResult(
                route_name=route_name,
                destination=route_config.url,
                success=False,
                message="arkiv-webhook plugin not installed",
            )

    def _build_webhook_payload(
        self,
        source_path: Path,
        route_name: str,
        classification: Classification,
    ) -> dict[str, object]:
        """Build the versioned payload stored in outbox and sent to webhook plugins."""
        return {
            "payload_version": 1,
            "original_path": str(source_path),
            "category": classification.category,
            "confidence": classification.confidence,
            "summary": classification.summary,
            "tags": classification.tags,
            "language": classification.language,
            "route_name": route_name,
        }

    def _enqueue_failed_webhook(
        self,
        *,
        item_id: int | None,
        route_name: str,
        url: str,
        payload: dict[str, object],
        error: str,
    ) -> None:
        """Remember a failed webhook delivery when a Store is available."""
        if self.store is None:
            return
        self.store.enqueue_webhook(
            item_id=item_id,
            route_name=route_name,
            url=url,
            payload=payload,
            last_error=error,
        )

    def _route_to_review(self, source_path: Path, classification: Classification) -> RouteResult:
        """Route to review directory when no route matches."""
        self.review_dir.mkdir(parents=True, exist_ok=True)
        dest_path = _unique_destination_path(self.review_dir / source_path.name)

        shutil.move(str(source_path), str(dest_path))
        logger.info(
            "No route matched for %s (category=%s, confidence=%.2f) → review",
            source_path.name,
            classification.category,
            classification.confidence,
        )

        reason = self._explain_no_match(classification)
        return RouteResult(
            route_name="__review__",
            destination=str(dest_path),
            success=True,
            message=f"Moved to review: {reason}",
        )

    def _explain_no_match(self, classification: Classification) -> str:
        """Explain why no route matched — helps users understand review items."""
        category = classification.category
        confidence = classification.confidence

        # Check if any route has this category
        routes_with_category = [
            (name, r)
            for name, r in self.routes.items()
            if category in r.categories or not r.categories
        ]

        if not routes_with_category:
            configured = sorted({cat for r in self.routes.values() for cat in r.categories})
            return (
                f"no route configured for category '{category}'. "
                f"Configured: {', '.join(configured)}"
            )

        # Category exists but confidence too low
        thresholds = [(name, r.confidence_threshold) for name, r in routes_with_category]
        best_name, best_threshold = min(thresholds, key=lambda x: x[1])
        return (
            f"confidence too low for '{category}' "
            f"({confidence:.0%} < {best_threshold:.0%} required by '{best_name}')"
        )
