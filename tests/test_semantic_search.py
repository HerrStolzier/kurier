"""Tests for hybrid semantic + keyword search."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lotse.db.store import Store


def _make_embedding(seed: float = 0.0) -> bytes:
    """Create a fake 384-dim embedding for testing."""
    floats = [(seed + i * 0.001) for i in range(384)]
    return struct.pack(f"<{len(floats)}f", *floats)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db_path = tmp_path / "test.db"
    return Store(db_path)


def test_vec_table_created_when_available(store: Store) -> None:
    """sqlite-vec should be available in test environment."""
    assert store.vec_enabled


def test_record_with_embedding(store: Store) -> None:
    emb = _make_embedding(0.1)
    item_id = store.record_item(
        original_path="/tmp/test.pdf",
        destination="/archive/test.pdf",
        category="rechnung",
        confidence=0.95,
        summary="Telekom Rechnung März 2026",
        tags=["telekom"],
        language="de",
        route_name="archiv",
        content_text="Telekom monthly invoice",
        embedding=emb,
    )
    assert item_id > 0
    assert store.count_embeddings() == 1


def test_vector_search(store: Store) -> None:
    """Items with similar embeddings should be found via vector search."""
    # Insert two items with different embeddings
    store.record_item(
        original_path="/tmp/invoice.pdf",
        destination="/archive/invoice.pdf",
        category="rechnung",
        confidence=0.9,
        summary="Telekom Rechnung",
        tags=["telekom"],
        language="de",
        route_name="archiv",
        content_text="Telekom monthly invoice for March",
        embedding=_make_embedding(0.1),
    )
    store.record_item(
        original_path="/tmp/article.md",
        destination="/articles/article.md",
        category="artikel",
        confidence=0.85,
        summary="Python async patterns",
        tags=["python"],
        language="en",
        route_name="artikel",
        content_text="How to use async/await in Python",
        embedding=_make_embedding(0.9),
    )

    # Search with embedding close to the first item
    results = store.search(
        query="", query_embedding=_make_embedding(0.1), mode="vec"
    )
    assert len(results) == 2
    # First result should be closer to seed=0.1
    assert results[0]["category"] == "rechnung"


def test_hybrid_search(store: Store) -> None:
    """Hybrid search combines FTS and vector results via RRF."""
    store.record_item(
        original_path="/tmp/telekom.pdf",
        destination="/archive/telekom.pdf",
        category="rechnung",
        confidence=0.9,
        summary="Telekom Rechnung März",
        tags=["telekom", "rechnung"],
        language="de",
        route_name="archiv",
        content_text="Telekom Rechnung",
        embedding=_make_embedding(0.1),
    )
    store.record_item(
        original_path="/tmp/vodafone.pdf",
        destination="/archive/vodafone.pdf",
        category="rechnung",
        confidence=0.85,
        summary="Vodafone Rechnung",
        tags=["vodafone", "rechnung"],
        language="de",
        route_name="archiv",
        content_text="Vodafone Rechnung",
        embedding=_make_embedding(0.5),
    )

    # Hybrid: FTS matches "Telekom", vector is close to seed 0.1
    results = store.search(
        query="Telekom",
        query_embedding=_make_embedding(0.1),
        mode="auto",
    )
    assert len(results) >= 1
    # Telekom should rank first (matched by both FTS and vector)
    assert results[0]["summary"] == "Telekom Rechnung März"
    # Check RRF score is present
    assert "rrf_score" in results[0]


def test_fts_fallback_without_embedding(store: Store) -> None:
    """Search without embedding falls back to pure FTS."""
    store.record_item(
        original_path="/tmp/test.pdf",
        destination="/archive/test.pdf",
        category="rechnung",
        confidence=0.9,
        summary="Test Rechnung",
        tags=["test"],
        language="de",
        route_name="archiv",
        content_text="test",
        embedding=_make_embedding(0.1),
    )

    results = store.search(query="Rechnung", mode="fts")
    assert len(results) == 1


def test_stats_include_embeddings(store: Store) -> None:
    store.record_item(
        original_path="/tmp/test.pdf",
        destination="/archive/test.pdf",
        category="rechnung",
        confidence=0.9,
        summary="Test",
        tags=[],
        language="de",
        route_name="archiv",
        content_text="test",
        embedding=_make_embedding(0.0),
    )

    s = store.stats()
    assert s["vec_enabled"] is True
    assert s["embeddings"] == 1
