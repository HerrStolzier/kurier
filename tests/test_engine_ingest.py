"""Tests for ingest_file status persistence (routed vs. failed)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from arkiv.core.classifier import Classification
from arkiv.core.config import ArkivConfig
from arkiv.core.engine import Engine


def _make_engine(tmp_path: Path, routes: dict) -> Engine:
    config = ArkivConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
        routes=routes,
    )
    return Engine(config)


def _classification() -> Classification:
    return Classification(
        category="rechnung",
        confidence=0.9,
        summary="Testrechnung",
        tags=["test"],
        language="de",
    )


def _ingest(engine: Engine, source: Path):
    with (
        patch.object(engine.classifier, "classify", return_value=_classification()),
        patch.object(engine, "_generate_embedding", return_value=None),
    ):
        return engine.ingest_file(source)


def test_ingest_success_persists_routed_status(tmp_path: Path) -> None:
    routes = {
        "archiv": {
            "type": "folder",
            "path": str(tmp_path / "archiv"),
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
            "rename": False,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert result.success
    item = engine.store.get_recent(limit=1)[0]
    assert item["status"] == "routed"
    assert item["route_name"] == "archiv"
    assert item["destination"].endswith("invoice.txt")


def test_ingest_unsuccessful_route_persists_failed_status(tmp_path: Path) -> None:
    # Folder-Route ohne Pfad: execute() liefert success=False ohne Exception
    routes = {
        "kaputt": {
            "type": "folder",
            "path": None,
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert not result.success
    item = engine.store.get_recent(limit=1)[0]
    assert item["status"] == "failed"
    assert item["route_name"] == "kaputt"
