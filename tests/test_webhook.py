"""Tests for webhook routing and the webhook plugin."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Queue
from unittest.mock import patch

import pytest

from arkiv.core.classifier import Classification
from arkiv.core.config import RouteConfig
from arkiv.core.router import Router

arkiv_webhook = pytest.importorskip("arkiv_webhook", reason="arkiv_webhook plugin not installed")


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

    with patch("arkiv_webhook.send_webhook", return_value=True) as mock:
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

    with patch("arkiv_webhook.send_webhook", return_value=True):
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

    with patch("arkiv_webhook.send_webhook", return_value=False):
        result = router_with_webhook.execute(source, classification)

    assert result.success
    assert result.route_name == "archiv"  # Folder route succeeded


def test_webhook_live_delivery_to_local_endpoint(tmp_path: Path) -> None:
    """Webhook plugin should POST a real payload to a live local endpoint."""

    received: Queue[dict[str, object]] = Queue()

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP handler API requires this name.
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            received.put(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": json.loads(body),
                }
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        webhook_url = f"http://127.0.0.1:{server.server_port}/hook"
        routes = {
            "archiv": RouteConfig(
                type="folder",
                path=str(tmp_path / "archiv"),
                categories=["rechnung"],
                confidence_threshold=0.7,
            ),
            "notify": RouteConfig(
                type="webhook",
                url=webhook_url,
                categories=["rechnung"],
                confidence_threshold=0.5,
            ),
        }
        router = Router(routes, tmp_path / "review")

        source = tmp_path / "invoice.pdf"
        source.write_text("test content")

        classification = Classification(
            category="rechnung",
            confidence=0.91,
            summary="Telekom Rechnung April 2026",
            tags=["telekom", "rechnung"],
            language="de",
        )

        result = router.execute(source, classification)

        assert result.success
        assert result.route_name == "archiv"

        delivered = received.get(timeout=2)
        payload = delivered["body"]

        assert delivered["path"] == "/hook"
        assert delivered["headers"]["Content-Type"] == "application/json"
        assert payload["event"] == "item_routed"
        assert payload["item"]["category"] == "rechnung"
        assert payload["item"]["route_name"] == "notify"
        assert payload["item"]["summary"] == "Telekom Rechnung April 2026"
        assert payload["item"]["language"] == "de"
        assert payload["item"]["tags"] == ["telekom", "rechnung"]
        assert "timestamp" in payload
        assert (tmp_path / "archiv").exists()
        assert not source.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
