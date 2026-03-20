"""Tests for the REST API inlet."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lotse.core.config import LotseConfig
from lotse.inlets.api import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Create a test client with a temp database."""
    config = LotseConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
    )
    app = create_app(config)
    return TestClient(app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_status_empty(client: TestClient) -> None:
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_items"] == 0
    assert data["categories"] == {}


def test_ingest_text(client: TestClient) -> None:
    """Test text ingestion via API (with mocked LLM)."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "notiz",
            "confidence": 0.85,
            "summary": "A test note",
            "tags": ["test"],
            "language": "en",
        }
    )

    with patch("lotse.core.classifier.completion", return_value=mock_response):
        resp = client.post(
            "/ingest/text",
            data={"text": "This is a test note about Python async patterns"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["route_name"] == "__text__"


def test_ingest_text_empty_rejected(client: TestClient) -> None:
    resp = client.post("/ingest/text", data={"text": "   "})
    assert resp.status_code == 422


def test_ingest_file(client: TestClient, tmp_path: Path) -> None:
    """Test file upload via API (with mocked LLM)."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "artikel",
            "confidence": 0.9,
            "summary": "Python tutorial",
            "tags": ["python"],
            "language": "en",
        }
    )

    with patch("lotse.core.classifier.completion", return_value=mock_response):
        resp = client.post(
            "/ingest/file",
            files={"file": ("tutorial.md", b"# Python Tutorial\nHello world", "text/markdown")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


def test_search_empty(client: TestClient) -> None:
    resp = client.get("/search", params={"q": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["results"] == []


def test_search_invalid_mode(client: TestClient) -> None:
    resp = client.get("/search", params={"q": "test", "mode": "invalid"})
    assert resp.status_code == 422


def test_search_after_ingest(client: TestClient) -> None:
    """Ingest then search should find the item."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "rechnung",
            "confidence": 0.95,
            "summary": "Telekom Rechnung März",
            "tags": ["telekom"],
            "language": "de",
        }
    )

    with patch("lotse.core.classifier.completion", return_value=mock_response):
        client.post(
            "/ingest/text",
            data={"text": "Telekom Rechnung für März 2026"},
        )

    resp = client.get("/search", params={"q": "Telekom", "mode": "fts"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["results"][0]["category"] == "rechnung"


def test_recent_items(client: TestClient) -> None:
    resp = client.get("/recent")
    assert resp.status_code == 200
    assert resp.json() == []
