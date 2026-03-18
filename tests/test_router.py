"""Tests for the routing engine."""

from pathlib import Path

import pytest

from lotse.core.classifier import Classification
from lotse.core.config import RouteConfig
from lotse.core.router import Router


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


def test_route_to_matching_folder(router: Router, tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_text("test content")

    classification = Classification(
        category="rechnung",
        confidence=0.9,
        summary="Test invoice",
        tags=["test"],
        language="de",
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
    assert "invoice_1.pdf" in result.destination
