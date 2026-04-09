"""Tests for the SQLite store."""

from pathlib import Path

import pytest

from arkiv.db.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db_path = tmp_path / "test.db"
    return Store(db_path)


def test_record_and_retrieve(store: Store) -> None:
    item_id = store.record_item(
        original_path="/tmp/test.pdf",
        destination="/archive/test.pdf",
        category="rechnung",
        confidence=0.95,
        summary="Telekom Rechnung März 2026",
        tags=["telekom", "rechnung", "telefon"],
        language="de",
        route_name="archiv",
        suggested_filename="Rechnung Telekom März 2026",
    )
    assert item_id > 0

    recent = store.recent(limit=1)
    assert len(recent) == 1
    assert recent[0]["category"] == "rechnung"
    assert recent[0]["confidence"] == 0.95
    assert recent[0]["suggested_filename"] == "Rechnung Telekom März 2026"
    assert recent[0]["destination_name"] == "test.pdf"
    assert recent[0]["display_title"] == "Rechnung Telekom März 2026"


def test_fts_search(store: Store) -> None:
    store.record_item(
        original_path="/tmp/telekom.pdf",
        destination="/archive/telekom.pdf",
        category="rechnung",
        confidence=0.9,
        summary="Telekom Rechnung März 2026",
        tags=["telekom"],
        language="de",
        route_name="archiv",
    )
    store.record_item(
        original_path="/tmp/article.md",
        destination="/articles/article.md",
        category="artikel",
        confidence=0.85,
        summary="Python async patterns",
        tags=["python", "async"],
        language="en",
        route_name="artikel",
    )

    results = store.search("Telekom")
    assert len(results) == 1
    assert results[0]["category"] == "rechnung"

    results = store.search("Python")
    assert len(results) == 1
    assert results[0]["category"] == "artikel"


def test_fts_search_finds_suggested_filename(store: Store) -> None:
    store.record_item(
        original_path="/tmp/scan-001.pdf",
        destination="/archive/2026-telekom.pdf",
        category="rechnung",
        confidence=0.92,
        summary="Monatsrechnung Mobilfunk",
        tags=["telefon"],
        language="de",
        route_name="archiv",
        suggested_filename="Rechnung Telekom März 2026",
    )

    results = store.search("Telekom")
    assert len(results) == 1
    assert results[0]["display_title"] == "Rechnung Telekom März 2026"


def test_stats(store: Store) -> None:
    for i in range(3):
        store.record_item(
            original_path=f"/tmp/file{i}.pdf",
            destination=f"/archive/file{i}.pdf",
            category="rechnung",
            confidence=0.9,
            summary=f"Invoice {i}",
            tags=[],
            language="de",
            route_name="archiv",
        )
    store.record_item(
        original_path="/tmp/article.md",
        destination="/articles/article.md",
        category="artikel",
        confidence=0.8,
        summary="An article",
        tags=[],
        language="en",
        route_name="artikel",
    )

    s = store.stats()
    assert s["total_items"] == 4
    assert s["categories"]["rechnung"] == 3
    assert s["categories"]["artikel"] == 1


def test_empty_search(store: Store) -> None:
    results = store.search("nonexistent")
    assert results == []


def test_update_category_marks_item_confirmed(store: Store) -> None:
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

    assert [item["id"] for item in store.low_confidence()] == [item_id]

    store.update_category(item_id, "brief")

    recent = store.recent(limit=1)
    assert recent[0]["category"] == "brief"
    assert recent[0]["confidence"] == 1.0
    assert store.low_confidence() == []
