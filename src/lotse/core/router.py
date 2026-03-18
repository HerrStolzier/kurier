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

    def find_route(self, classification: Classification) -> tuple[str, RouteConfig] | None:
        """Find the best matching route for a classification."""
        for name, route in self.routes.items():
            if (
                classification.category in route.categories
                and classification.confidence >= route.confidence_threshold
            ):
                return name, route
        return None

    def execute(
        self, source_path: Path, classification: Classification
    ) -> RouteResult:
        """Route a file based on its classification."""
        match = self.find_route(classification)

        if match is None:
            return self._route_to_review(source_path, classification)

        route_name, route_config = match
        return self._execute_route(source_path, route_name, route_config, classification)

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

    def _route_to_review(
        self, source_path: Path, classification: Classification
    ) -> RouteResult:
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

        return RouteResult(
            route_name="__review__",
            destination=str(dest_path),
            success=True,
            message=f"No matching route — moved to review: {dest_path}",
        )
