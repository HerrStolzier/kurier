"""Route matching and execution engine."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from lotse.core.classifier import Classification
from lotse.core.config import RouteConfig

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """Result of routing an item."""

    route_name: str
    destination: str
    success: bool
    message: str


class Router:
    """Matches classifications to routes and executes them."""

    def __init__(self, routes: dict[str, RouteConfig], review_dir: Path) -> None:
        self.routes = routes
        self.review_dir = review_dir

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

    def execute(self, source_path: Path, classification: Classification) -> RouteResult:
        """Route a file based on its classification.

        Primary route (first match with type=folder) moves the file.
        Additional webhook routes fire in parallel without moving the file.
        """
        matches = self.find_routes(classification)

        if not matches:
            return self._route_to_review(source_path, classification)

        # Separate folder routes (file-moving) from webhook routes (fire-and-forget)
        primary_result = None
        for route_name, route_config in matches:
            result = self._execute_route(source_path, route_name, route_config, classification)
            if primary_result is None and route_config.type == "folder":
                primary_result = result
            elif route_config.type == "webhook" and not result.success:
                logger.warning("Webhook %s failed: %s", route_name, result.message)

        # If no folder route matched, use the first result (could be webhook-only)
        if primary_result is None:
            primary_result = self._execute_route(
                source_path, matches[0][0], matches[0][1], classification
            )

        return primary_result

    def _execute_route(
        self,
        source_path: Path,
        route_name: str,
        route_config: RouteConfig,
        classification: Classification,
    ) -> RouteResult:
        """Execute a specific route."""
        if route_config.type == "folder":
            return self._route_to_folder(source_path, route_name, route_config)
        elif route_config.type == "webhook":
            return self._route_to_webhook(source_path, route_name, route_config, classification)
        else:
            logger.warning("Unknown route type: %s", route_config.type)
            return RouteResult(
                route_name=route_name,
                destination="unknown",
                success=False,
                message=f"Unknown route type: {route_config.type}",
            )

    def _route_to_folder(
        self, source_path: Path, route_name: str, route_config: RouteConfig
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
        dest_path = dest_dir / source_path.name

        # Handle name collision
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

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
    ) -> RouteResult:
        """Route item data to a webhook URL (does NOT move the file)."""
        if not route_config.url:
            return RouteResult(
                route_name=route_name,
                destination="",
                success=False,
                message="No URL configured for webhook route",
            )

        try:
            from lotse_webhook import send_webhook

            item_data = {
                "original_path": str(source_path),
                "category": classification.category,
                "confidence": classification.confidence,
                "summary": classification.summary,
                "tags": classification.tags,
                "language": classification.language,
                "route_name": route_name,
            }

            success = send_webhook(route_config.url, item_data)
            if success:
                return RouteResult(
                    route_name=route_name,
                    destination=route_config.url,
                    success=True,
                    message=f"Webhook delivered: {route_name}",
                )
            else:
                return RouteResult(
                    route_name=route_name,
                    destination=route_config.url,
                    success=False,
                    message=f"Webhook delivery failed: {route_name}",
                )

        except ImportError:
            logger.warning(
                "Webhook route '%s' configured but lotse-webhook not installed. "
                "Install with: pip install lotse-webhook",
                route_name,
            )
            return RouteResult(
                route_name=route_name,
                destination=route_config.url,
                success=False,
                message="lotse-webhook plugin not installed",
            )

    def _route_to_review(self, source_path: Path, classification: Classification) -> RouteResult:
        """Route to review directory when no route matches."""
        self.review_dir.mkdir(parents=True, exist_ok=True)
        dest_path = self.review_dir / source_path.name

        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = self.review_dir / f"{stem}_{counter}{suffix}"
                counter += 1

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
