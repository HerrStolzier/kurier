"""Tests for the web dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arkiv.core.config import ArkivConfig
from arkiv.inlets.api import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    config = ArkivConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
    )
    app = create_app(config)
    return TestClient(app)


def test_root_redirects_to_dashboard(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/dashboard/"


def test_dashboard_loads(client: TestClient) -> None:
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert "Kurier" in resp.text
    assert "htmx" in resp.text
    assert "tailwindcss" in resp.text


def test_stats_partial(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/stats")
    assert resp.status_code == 200
    assert "Total Items" in resp.text
    assert "0" in resp.text  # Empty database


def test_recent_partial_empty(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/recent")
    assert resp.status_code == 200
    assert "No items processed yet" in resp.text


def test_search_partial_empty_query(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.text == ""


def test_search_partial_no_results(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/search", params={"q": "nonexistent"})
    assert resp.status_code == 200
    assert "No results" in resp.text


def test_upload_partial(client: TestClient) -> None:
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "rechnung",
            "confidence": 0.9,
            "summary": "Test invoice",
            "tags": ["test"],
            "language": "de",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        resp = client.post(
            "/dashboard/partials/upload",
            files={"file": ("test.txt", b"Invoice content", "text/plain")},
        )

    assert resp.status_code == 200
    assert "rechnung" in resp.text
    assert "Classified" in resp.text


def test_search_after_upload(client: TestClient) -> None:
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "artikel",
            "confidence": 0.85,
            "summary": "Python tutorial",
            "tags": ["python"],
            "language": "en",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        client.post(
            "/dashboard/partials/upload",
            files={"file": ("tutorial.md", b"# Python Tutorial", "text/markdown")},
        )

    resp = client.get("/dashboard/partials/search", params={"q": "Python"})
    assert resp.status_code == 200
    assert "Python tutorial" in resp.text


def test_recent_shows_items_after_upload(client: TestClient) -> None:
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        {
            "category": "notiz",
            "confidence": 0.8,
            "summary": "A quick note",
            "tags": [],
            "language": "en",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        client.post(
            "/dashboard/partials/upload",
            files={"file": ("note.txt", b"Remember this", "text/plain")},
        )

    resp = client.get("/dashboard/partials/recent")
    assert resp.status_code == 200
    assert "A quick note" in resp.text
    assert "notiz" in resp.text
