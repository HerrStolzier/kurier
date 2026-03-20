"""Tests for webhook routing and the webhook plugin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lotse.core.classifier import Classification
from lotse.core.config import RouteConfig
from lotse.core.router import Router

lotse_webhook = pytest.importorskip("lotse_webhook", reason="lotse_webhook plugin not installed")


@pytest.fixture
def router_with_webhook(tmp_path: Path) -> Router:
    routes = {
        "archiv": RouteConfig(
            type="folder",
            path=str(tmp_path / "archiv"),
            categories=["rechnung"],
            confidence_threshold=0.7,
        ),
        "slack": RouteConfig(
            type="webhook",
            url="https://hooks.slack.com/services/T123/B456/xxx",
            categories=["rechnung"],
            confidence_threshold=0.5,
        ),
    }
    return Router(routes, tmp_path / "review")


def test_find_routes_returns_multiple(router_with_webhook: Router) -> None:
    """Both folder and webhook routes should match."""
    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Invoice",
        tags=[],
        language="de",
    )
    matches = router_with_webhook.find_routes(classification)
    assert len(matches) == 2
    types = {r.type for _, r in matches}
    assert types == {"folder", "webhook"}


def test_wildcard_categories(tmp_path: Path) -> None:
    """Empty categories list should match any category."""
    routes = {
        "catchall": RouteConfig(
            type="webhook",
            url="https://example.com/hook",
            categories=[],  # wildcard
            confidence_threshold=0.3,
        ),
    }
    router = Router(routes, tmp_path / "review")

    classification = Classification(
        category="anything",
        confidence=0.5,
        summary="Test",
        tags=[],
        language="en",
    )
    matches = router.find_routes(classification)
    assert len(matches) == 1
    assert matches[0][0] == "catchall"


def test_webhook_route_sends_and_keeps_file(router_with_webhook: Router, tmp_path: Path) -> None:
    """Webhook fires but file is moved by folder route, not webhook."""
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Telekom invoice",
        tags=["telekom"],
        language="de",
    )

    with patch("lotse_webhook.send_webhook", return_value=True) as mock:
        result = router_with_webhook.execute(source, classification)

    assert result.success
    assert result.route_name == "archiv"  # Primary route is folder
    mock.assert_called_once()  # Webhook was also fired


def test_webhook_only_route(tmp_path: Path) -> None:
    """When only webhook routes match, file goes to review."""
    routes = {
        "notify": RouteConfig(
            type="webhook",
            url="https://example.com/hook",
            categories=["artikel"],
            confidence_threshold=0.5,
        ),
    }
    router = Router(routes, tmp_path / "review")

    source = tmp_path / "article.md"
    source.write_text("test")

    classification = Classification(
        category="artikel",
        confidence=0.8,
        summary="Article",
        tags=[],
        language="en",
    )

    with patch("lotse_webhook.send_webhook", return_value=True):
        result = router.execute(source, classification)

    # Webhook-only = file stays, webhook fires, result is from webhook
    assert result.success
    assert result.route_name == "notify"


def test_webhook_failure_does_not_block_folder_route(
    router_with_webhook: Router, tmp_path: Path
) -> None:
    """Failed webhook should not prevent folder routing."""
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Invoice",
        tags=[],
        language="de",
    )

    with patch("lotse_webhook.send_webhook", return_value=False):
        result = router_with_webhook.execute(source, classification)

    assert result.success
    assert result.route_name == "archiv"  # Folder route succeeded
