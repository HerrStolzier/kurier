"""Tests for the REST API inlet."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arkiv.core.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from arkiv.core.config import ArkivConfig
from arkiv.inlets.api import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Create a test client with a temp database."""
    config = ArkivConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
    )
    app = create_app(config)
    return TestClient(app)


def _csrf_headers(client: TestClient) -> dict[str, str]:
    client.get("/health")
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


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
            "suggested_filename": "Testnotiz Python Async",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        resp = client.post(
            "/ingest/text",
            data={"text": "This is a test note about Python async patterns"},
            headers=_csrf_headers(client),
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["route_name"] == "__text__"


def test_ingest_text_empty_rejected(client: TestClient) -> None:
    resp = client.post("/ingest/text", data={"text": "   "}, headers=_csrf_headers(client))
    assert resp.status_code == 422


def test_ingest_text_returns_safe_error_detail(client: TestClient) -> None:
    with patch("arkiv.inlets.api.ingest_text_workflow", side_effect=RuntimeError("/secret/path")):
        resp = client.post(
            "/ingest/text",
            data={"text": "trigger error"},
            headers=_csrf_headers(client),
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Text could not be processed"
    assert "/secret/path" not in resp.text


def test_ingest_text_rejects_missing_csrf_token(client: TestClient) -> None:
    resp = client.post("/ingest/text", data={"text": "browser form"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF token required"


def test_api_key_client_can_post_without_csrf(tmp_path: Path) -> None:
    config = ArkivConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
    )
    api_key_client = TestClient(create_app(config, api_key="secret"))
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "notiz",
            "confidence": 0.85,
            "summary": "A test note",
            "tags": ["test"],
            "language": "en",
            "suggested_filename": "API Key Note",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        resp = api_key_client.post(
            "/ingest/text",
            data={"text": "API key clients stay ergonomic"},
            headers={"x-api-key": "secret"},
        )

    assert resp.status_code == 200


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
            "suggested_filename": "Python Tutorial",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        resp = client.post(
            "/ingest/file",
            files={"file": ("tutorial.md", b"# Python Tutorial\nHello world", "text/markdown")},
            headers=_csrf_headers(client),
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
            "suggested_filename": "Rechnung Telekom März 2026",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        client.post(
            "/ingest/text",
            data={"text": "Telekom Rechnung für März 2026"},
            headers=_csrf_headers(client),
        )

    resp = client.get("/search", params={"q": "Telekom", "mode": "fts"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["results"][0]["category"] == "rechnung"
    assert data["results"][0]["display_title"] == "Rechnung Telekom März 2026"


def test_memory_search_uses_query_assist_rewrites(client: TestClient) -> None:
    classifier_response = MagicMock()
    classifier_response.choices = [MagicMock()]
    classifier_response.choices[0].message.content = json.dumps(
        {
            "category": "rechnung",
            "confidence": 0.94,
            "summary": "Mobilfunkrechnung März 2026",
            "tags": ["rechnung"],
            "language": "de",
            "suggested_filename": "Rechnung Telekom März 2026",
        }
    )
    search_assist_response = MagicMock()
    search_assist_response.choices = [MagicMock()]
    search_assist_response.choices[0].message.content = json.dumps(
        {
            "rewrites": ["Telekom Rechnung März 2026", "Mobilfunk Rechnung Telekom"],
            "filters": {"category": ["rechnung"], "organizations": ["Telekom"]},
            "notes": "Die Anfrage klingt nach einer Telekom-Rechnung.",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=classifier_response):
        client.post(
            "/ingest/text",
            data={"text": "Telekom Rechnung für März 2026"},
            headers=_csrf_headers(client),
        )

    with patch("arkiv.core.search_assistant.completion", return_value=search_assist_response):
        resp = client.get(
            "/search",
            params={"q": "Internetanbieter Frühling", "mode": "fts", "memory": "true"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["memory"] is True
    assert data["count"] >= 1
    assert data["assist"]["rewrites"][0] == "Telekom Rechnung März 2026"
    assert data["results"][0]["display_title"] == "Rechnung Telekom März 2026"
    assert data["results"][0]["match_reason"].startswith("Passt wegen")


def test_recent_items(client: TestClient) -> None:
    resp = client.get("/recent")
    assert resp.status_code == 200
    assert resp.json() == []


def test_recent_items_return_minimized_titles(client: TestClient) -> None:
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "brief",
            "confidence": 0.88,
            "summary": "Schreiben der Krankenkasse",
            "tags": ["krankenkasse"],
            "language": "de",
            "suggested_filename": "Beitragsschreiben Krankenkasse",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        client.post(
            "/ingest/text",
            data={"text": "Schreiben der Krankenkasse wegen neuem Beitrag"},
            headers=_csrf_headers(client),
        )

    resp = client.get("/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["display_title"] == "Beitragsschreiben Krankenkasse"
    assert "original_path" not in data[0]
