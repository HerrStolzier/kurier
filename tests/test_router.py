"""Tests for the routing engine."""

import builtins
from pathlib import Path
from unittest.mock import patch

import pytest

from arkiv.core.classifier import Classification
from arkiv.core.config import ArkivConfig, RouteConfig
from arkiv.core.router import Router, _build_filename, _unique_destination_path
from arkiv.db.store import Store


@pytest.fixture
def router(tmp_path: Path) -> Router:
    routes = {
        "archiv": RouteConfig(
            type="folder",
            path=str(tmp_path / "archiv"),
            categories=["rechnung", "vertrag"],
            confidence_threshold=0.7,
        ),
        "artikel": RouteConfig(
            type="folder",
            path=str(tmp_path / "artikel"),
            categories=["artikel", "tutorial"],
            confidence_threshold=0.5,
        ),
    }
    review_dir = tmp_path / "review"
    return Router(routes, review_dir)


@pytest.fixture
def router_no_rename(tmp_path: Path) -> Router:
    routes = {
        "archiv": RouteConfig(
            type="folder",
            path=str(tmp_path / "archiv"),
            categories=["rechnung"],
            confidence_threshold=0.7,
            rename=False,
        ),
    }
    return Router(routes, tmp_path / "review")


def test_route_to_matching_folder(router: Router, tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test invoice",
        tags=["test"],
        language="de",
        suggested_filename="Rechnung Telekom März",
    )

    result = router.execute(source, classification)
    assert result.success
    assert result.route_name == "archiv"
    assert not source.exists()  # File was moved


def test_route_to_review_on_low_confidence(router: Router, tmp_path: Path) -> None:
    source = tmp_path / "unclear.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.3,  # Below threshold
        summary="Unclear document",
        tags=[],
        language="de",
    )

    result = router.execute(source, classification)
    assert result.success
    assert result.route_name == "__review__"


def test_route_to_review_on_unknown_category(router: Router, tmp_path: Path) -> None:
    source = tmp_path / "mystery.pdf"
    source.write_text("test content")

    classification = Classification(
        category="unknown_type",
        confidence=0.95,
        summary="Unknown type",
        tags=[],
        language="en",
    )

    result = router.execute(source, classification)
    assert result.success
    assert result.route_name == "__review__"


def test_handles_name_collision(router: Router, tmp_path: Path) -> None:
    archiv_dir = tmp_path / "archiv"
    archiv_dir.mkdir(parents=True)
    (archiv_dir / "invoice.pdf").write_text("existing")

    source = tmp_path / "invoice.pdf"
    source.write_text("new content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Another invoice",
        tags=[],
        language="de",
    )

    result = router.execute(source, classification)
    assert result.success
    assert "_1.pdf" in result.destination


def test_unique_destination_path_increments_existing_name(tmp_path: Path) -> None:
    existing = tmp_path / "invoice.pdf"
    existing.write_text("existing")
    (tmp_path / "invoice_1.pdf").write_text("also existing")

    result = _unique_destination_path(existing)

    assert result == tmp_path / "invoice_2.pdf"


def test_review_route_uses_same_collision_handling(router: Router, tmp_path: Path) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "mystery.pdf").write_text("existing")
    (review_dir / "mystery_1.pdf").write_text("also existing")

    source = tmp_path / "mystery.pdf"
    source.write_text("new content")

    classification = Classification(
        category="unknown_type",
        confidence=0.95,
        summary="Unknown type",
        tags=[],
        language="en",
    )

    result = router.execute(source, classification)

    assert result.success
    assert result.route_name == "__review__"
    assert result.destination.endswith("mystery_2.pdf")


def test_missing_webhook_plugin_uses_versioned_payload_in_outbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = {
        "notify": RouteConfig(
            type="webhook",
            url="https://example.com/hook",
            categories=["artikel"],
            confidence_threshold=0.5,
        ),
    }
    store = Store(tmp_path / "kurier.db")
    router = Router(routes, tmp_path / "review", store=store)
    source = tmp_path / "article.md"
    source.write_text("test")
    item_id = store.record_item(
        original_path=str(source),
        destination="",
        category="artikel",
        confidence=0.8,
        summary="Article",
        tags=["python"],
        language="en",
        route_name="",
        status="pending",
    )
    classification = Classification(
        category="artikel",
        confidence=0.8,
        summary="Article",
        tags=["python"],
        language="en",
    )
    original_import = builtins.__import__

    def fail_webhook_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "arkiv_webhook":
            raise ImportError("arkiv-webhook missing for test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_webhook_import)

    result = router.execute(source, classification, item_id=item_id)

    [pending] = store.list_webhook_outbox(statuses=("pending",))
    assert not result.success
    assert result.message == "arkiv-webhook plugin not installed"
    assert pending["item_id"] == item_id
    assert pending["route_name"] == "notify"
    assert pending["payload"]["payload_version"] == 1
    assert pending["payload"]["original_path"] == str(source)
    assert pending["payload"]["category"] == "artikel"
    assert pending["payload"]["tags"] == ["python"]
    assert pending["last_error"] == "arkiv-webhook plugin not installed"


# --- Smart Rename Tests ---


@patch("arkiv.core.router.date")
def test_rename_builds_correct_filename(mock_date: object, router: Router, tmp_path: Path) -> None:
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]
    mock_date.side_effect = lambda *args, **kw: real_date(*args, **kw)  # type: ignore[attr-defined]

    source = tmp_path / "Screenshot 2026-03-23 um 14.23.45.png"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Telekom Monatsabrechnung",
        tags=["telekom"],
        language="de",
        suggested_filename="Rechnung Telekom März",
    )

    result = router.execute(source, classification)
    assert result.success
    assert "23.03.2026 Rechnung Telekom März.png" in result.destination


def test_rename_disabled_keeps_original_name(router_no_rename: Router, tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
        suggested_filename="Rechnung Telekom",
    )

    result = router_no_rename.execute(source, classification)
    assert result.success
    assert "invoice.pdf" in result.destination


def test_rename_without_suggested_filename_keeps_original(router: Router, tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
        suggested_filename="",
    )

    result = router.execute(source, classification)
    assert result.success
    assert "invoice.pdf" in result.destination


@patch("arkiv.core.router.date")
def test_build_filename_strips_unsafe_chars(mock_date: object) -> None:
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]

    c = Classification(
        category="rechnung",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        suggested_filename='Rechnung: Telekom / März "2026"',
    )
    result = _build_filename(c, ".pdf")
    assert "23.03.2026" in result
    assert "Rechnung" in result
    assert "Telekom" in result
    assert ":" not in result
    assert '"' not in result
    assert "/" not in result


@patch("arkiv.core.router.date")
def test_build_filename_preserves_umlauts(mock_date: object) -> None:
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]

    c = Classification(
        category="vertrag",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        suggested_filename="Mietvertrag Hauptstraße Würzburg",
    )
    result = _build_filename(c, ".pdf")
    assert "ä" in result or "ü" in result or "ß" in result
    assert "23.03.2026" in result


@patch("arkiv.core.router.date")
def test_build_filename_removes_extension_from_llm(mock_date: object) -> None:
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]

    c = Classification(
        category="rechnung",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        suggested_filename="Rechnung Telekom.pdf",
    )
    result = _build_filename(c, ".pdf")
    assert result == "23.03.2026 Rechnung Telekom.pdf"
    assert ".pdf.pdf" not in result


@patch("arkiv.core.router.date")
def test_build_filename_fallback_to_category(mock_date: object) -> None:
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]

    c = Classification(
        category="rechnung",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        suggested_filename="   ",  # Only whitespace
    )
    result = _build_filename(c, ".pdf")
    assert result == "23.03.2026 Rechnung.pdf"


@patch("arkiv.core.router.date")
def test_build_filename_keeps_dates_intact(mock_date: object) -> None:
    """Dots in dates like 20.03.2026 must NOT be treated as file extensions."""
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]

    c = Classification(
        category="notiz",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        suggested_filename="Teammeeting 20.03.2026",
    )
    result = _build_filename(c, ".txt")
    assert result == "23.03.2026 Teammeeting 20.03.2026.txt"
    assert "20.03.2026" in result


@patch("arkiv.core.router.date")
def test_build_filename_replaces_underscores(mock_date: object) -> None:
    """LLM sometimes uses underscores despite instructions — fix them."""
    from datetime import date as real_date

    mock_date.today.return_value = real_date(2026, 3, 23)  # type: ignore[attr-defined]

    c = Classification(
        category="rechnung",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        suggested_filename="telekom_rechnung_märz",
    )
    result = _build_filename(c, ".txt")
    assert "_" not in result
    assert "telekom rechnung märz" in result


def test_two_matching_folder_routes_first_moves_rest_skipped(tmp_path: Path) -> None:
    routes = {
        "archiv": RouteConfig(
            type="folder",
            path=str(tmp_path / "archiv"),
            categories=["rechnung"],
            confidence_threshold=0.7,
            rename=False,
        ),
        "alles": RouteConfig(
            type="folder",
            path=str(tmp_path / "alles"),
            categories=[],  # wildcard
            confidence_threshold=0.0,
            rename=False,
        ),
    }
    router = Router(routes, tmp_path / "review")
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
    )

    result = router.execute(source, classification)

    assert result.success
    assert result.route_name == "archiv"
    assert (tmp_path / "archiv" / "invoice.pdf").exists()
    assert not source.exists()
    assert not (tmp_path / "alles" / "invoice.pdf").exists()


def test_failed_folder_route_falls_back_to_next_folder_route(tmp_path: Path) -> None:
    routes = {
        "kaputt": RouteConfig(
            type="folder",
            path=None,  # Konfigurationsfehler: kein Pfad
            categories=["rechnung"],
            confidence_threshold=0.7,
        ),
        "archiv": RouteConfig(
            type="folder",
            path=str(tmp_path / "archiv"),
            categories=["rechnung"],
            confidence_threshold=0.7,
            rename=False,
        ),
    }
    router = Router(routes, tmp_path / "review")
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
    )

    result = router.execute(source, classification)

    assert result.success
    assert result.route_name == "archiv"
    assert (tmp_path / "archiv" / "invoice.pdf").exists()


def test_all_folder_routes_failing_returns_failure(tmp_path: Path) -> None:
    routes = {
        "kaputt": RouteConfig(
            type="folder",
            path=None,
            categories=["rechnung"],
            confidence_threshold=0.7,
        ),
    }
    router = Router(routes, tmp_path / "review")
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
    )

    result = router.execute(source, classification)

    assert not result.success
    assert source.exists()  # Datei wurde nicht bewegt


def test_webhook_only_failure_wins_regardless_of_order(tmp_path: Path) -> None:
    routes = {
        "hook_ok": RouteConfig(
            type="webhook",
            url="http://example.invalid/ok",
            categories=["rechnung"],
            confidence_threshold=0.7,
        ),
        "hook_kaputt": RouteConfig(
            type="webhook",
            url="http://example.invalid/fail",
            categories=["rechnung"],
            confidence_threshold=0.7,
        ),
    }
    router = Router(routes, tmp_path / "review")
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
    )

    with patch("arkiv_webhook.send_webhook", side_effect=[True, False]):
        result = router.execute(source, classification)

    assert not result.success
    assert result.route_name == "hook_kaputt"
    assert source.exists()  # Webhooks bewegen keine Dateien


def test_unknown_geht_nie_an_eine_wildcard_route(tmp_path: Path) -> None:
    """Eine Auffangroute mit Schwelle 0.0 wuerde 'unknown' sonst einsammeln und
    die Datei still ablegen, statt sie in die Pruefliste zu geben."""
    config = ArkivConfig(
        database={"path": tmp_path / "t.db"},
        review_dir=tmp_path / "review",
        routes={
            "auffang": {
                "type": "folder",
                "path": str(tmp_path / "auffang"),
                "categories": [],
                "confidence_threshold": 0.0,
            }
        },
    )
    router = Router(config.routes, review_dir=config.review_dir)

    unbekannt = Classification(
        category="unknown", confidence=0.0, summary="", tags=[], language="de"
    )

    assert router.find_routes(unbekannt) == []


def test_eigene_kategorie_unknown_wird_normal_geroutet(tmp_path: Path) -> None:
    """'unknown' darf als eigene Kategorie konfiguriert sein. Nur der interne
    Fehlerwert (Sicherheit 0.0) gehoert in die Pruefliste."""
    config = ArkivConfig(
        database={"path": tmp_path / "t.db"},
        review_dir=tmp_path / "review",
        categories={"unknown": "alles Unsortierte"},
        routes={
            "unsortiert": {
                "type": "folder",
                "path": str(tmp_path / "unsortiert"),
                "categories": ["unknown"],
                "confidence_threshold": 0.5,
            }
        },
    )
    router = Router(config.routes, review_dir=config.review_dir)

    sicher = Classification(category="unknown", confidence=0.9, summary="", tags=[], language="de")
    fehlerwert = Classification(
        category="unknown", confidence=0.0, summary="", tags=[], language="de"
    )

    assert [name for name, _ in router.find_routes(sicher)] == ["unsortiert"]
    assert router.find_routes(fehlerwert) == []
