"""Tests for the web dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arkiv.core.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from arkiv.core.config import ArkivConfig
from arkiv.db.store import Store
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


def _csrf_headers(client: TestClient) -> dict[str, str]:
    client.get("/dashboard/")
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


def test_root_redirects_to_dashboard(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/dashboard/"


def test_dashboard_loads(client: TestClient) -> None:
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert "Kurier" in resp.text
    assert "htmx" in resp.text
    assert "styles.css" in resp.text
    assert "kurier-csrf-token" in resp.text
    assert "Problem melden" in resp.text
    assert 'hx-trigger="load, every 5s"' in resp.text


def test_dashboard_static_assets_are_served(client: TestClient) -> None:
    css = client.get("/dashboard/static/styles.css")
    js = client.get("/dashboard/static/htmx.min.js")

    assert css.status_code == 200
    assert "tailwindcss" in css.text
    assert js.status_code == 200
    assert "htmx" in js.text


def test_stats_partial(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/stats")
    assert resp.status_code == 200
    assert "Dokumente" in resp.text
    assert "0" in resp.text  # Empty database


def test_stats_partial_shows_open_webhooks(client: TestClient) -> None:
    from arkiv.inlets.api import _get_context

    _get_context().engine.store.enqueue_webhook(
        item_id=None,
        route_name="n8n",
        url="http://localhost:5678/webhook/kurier",
        payload={"payload_version": 1},
        last_error="down",
    )

    resp = client.get("/dashboard/partials/stats")

    assert resp.status_code == 200
    assert "1 Webhook offen" in resp.text
    assert "kurier webhooks retry" in resp.text


def test_recent_partial_empty(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/recent")
    assert resp.status_code == 200
    assert "Noch keine Dokumente verarbeitet" in resp.text


def test_search_partial_empty_query(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.text == ""


def test_search_partial_no_results(client: TestClient) -> None:
    resp = client.get("/dashboard/partials/search", params={"q": "nonexistent"})
    assert resp.status_code == 200
    assert "Keine passenden Dokumente" in resp.text
    assert "anderen Begriff" in resp.text


def test_search_partial_no_results_records_beta_event(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/dashboard/partials/search", params={"q": "nonexistent"})
    assert resp.status_code == 200

    store = Store(tmp_path / "test.db")
    events = store.recent_beta_events()
    assert events[0]["event_type"] == "search_no_results"
    assert events[0]["context"]["query"] == "nonexistent"


def test_beta_problem_partial_records_manual_feedback(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/dashboard/partials/beta/problem",
        data={"message": "Ich finde den Status nicht.", "page": "dashboard"},
        headers=_csrf_headers(client),
    )

    assert resp.status_code == 200
    assert "lokal notiert" in resp.text

    store = Store(tmp_path / "test.db")
    events = store.recent_beta_events()
    assert events[0]["event_type"] == "manual_feedback"
    assert events[0]["message"] == "Ich finde den Status nicht."


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
            "suggested_filename": "Testrechnung April 2026",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        resp = client.post(
            "/dashboard/partials/upload",
            files={"file": ("test.txt", b"Invoice content", "text/plain")},
            headers=_csrf_headers(client),
        )

    assert resp.status_code == 200
    assert "rechnung" in resp.text
    assert "Erledigt" in resp.text
    assert "Erkannt als" in resp.text
    assert "Wenn das falsch ist" in resp.text


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
            "suggested_filename": "Python Tutorial",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        client.post(
            "/dashboard/partials/upload",
            files={"file": ("tutorial.md", b"# Python Tutorial", "text/markdown")},
            headers=_csrf_headers(client),
        )

    resp = client.get("/dashboard/partials/search", params={"q": "Python"})
    assert resp.status_code == 200
    assert "Python tutorial" in resp.text
    assert "Python Tutorial" in resp.text


def test_search_partial_shows_memory_assist(client: TestClient) -> None:
    classifier_response = MagicMock()
    classifier_response.choices = [MagicMock()]
    classifier_response.choices[0].message.content = json.dumps(
        {
            "category": "rechnung",
            "confidence": 0.94,
            "summary": "Telekom Rechnung März",
            "tags": ["telekom"],
            "language": "de",
            "suggested_filename": "Rechnung Telekom März 2026",
        }
    )
    assist_response = MagicMock()
    assist_response.choices = [MagicMock()]
    assist_response.choices[0].message.content = json.dumps(
        {
            "rewrites": ["Telekom Rechnung März 2026", "Mobilfunk Rechnung Telekom"],
            "filters": {"category": ["rechnung"], "organizations": ["Telekom"]},
            "notes": "Das klingt nach einer Telekom-Rechnung.",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=classifier_response):
        client.post(
            "/dashboard/partials/upload",
            files={"file": ("scan.txt", b"Telekom Rechnung Maerz", "text/plain")},
            headers=_csrf_headers(client),
        )

    with patch("arkiv.core.search_assistant.completion", return_value=assist_response):
        resp = client.get(
            "/dashboard/partials/search",
            params={"q": "Internetanbieter Frühling", "memory": "true"},
        )

    assert resp.status_code == 200
    assert "KI-Hinweis" in resp.text
    assert "Telekom Rechnung März 2026" in resp.text
    assert "Passt wegen" in resp.text
    assert "Suchvariante" in resp.text or "Kategorie" in resp.text


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
            "suggested_filename": "Kurze Notiz",
        }
    )

    with patch("arkiv.core.classifier.completion", return_value=mock_response):
        client.post(
            "/dashboard/partials/upload",
            files={"file": ("note.txt", b"Remember this", "text/plain")},
            headers=_csrf_headers(client),
        )

    resp = client.get("/dashboard/partials/recent")
    assert resp.status_code == 200
    assert "Zuletzt erledigt" in resp.text
    assert "Erledigt" in resp.text
    assert "Quelle:" in resp.text
    assert "Ablage:" in resp.text
    assert "Wenn das falsch ist" in resp.text
    assert "A quick note" in resp.text
    assert "notiz" in resp.text
    assert "Kurze Notiz" in resp.text


def test_review_correct_confirms_item_and_removes_it_from_queue(
    client: TestClient, tmp_path: Path
) -> None:
    store = Store(tmp_path / "test.db")
    item_id = store.record_item(
        original_path="/tmp/brief.txt",
        destination="/archive/brief.txt",
        category="notiz",
        confidence=0.41,
        summary="Unsicher eingeordneter Brief",
        tags=["brief"],
        language="de",
        route_name="archiv",
    )

    before = client.get("/dashboard/partials/review")
    assert before.status_code == 200
    assert "Unsicher eingeordneter Brief" in before.text

    resp = client.post(
        f"/dashboard/partials/review/{item_id}/correct",
        data={"category": "brief"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert "Erledigt" in resp.text
    assert "brief" in resp.text

    after = client.get("/dashboard/partials/review")
    assert after.status_code == 200
    assert "Unsicher eingeordneter Brief" not in after.text

    updated = store.recent(limit=1)[0]
    assert updated["category"] == "brief"
    assert updated["confidence"] == 1.0
