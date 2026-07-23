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
    assert s["webhooks_open"] == 0


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


def test_beta_events_are_recorded_and_summarized(store: Store) -> None:
    event_id = store.record_beta_event(
        "search_no_results",
        "Suche ohne Treffer",
        severity="warn",
        context={"query": "Telekom März"},
    )

    events = store.recent_beta_events()
    assert events[0]["id"] == event_id
    assert events[0]["event_type"] == "search_no_results"
    assert events[0]["severity"] == "warn"
    assert events[0]["context"]["query"] == "Telekom März"

    summary = store.beta_event_summary(days=7)
    assert summary["total"] == 1
    assert summary["by_type"][0]["event_type"] == "search_no_results"
    assert summary["by_type"][0]["count"] == 1


def test_stats_counts_open_webhooks(store: Store) -> None:
    store.enqueue_webhook(
        item_id=None,
        route_name="n8n",
        url="http://localhost:5678/webhook/kurier",
        payload={"payload_version": 1},
        last_error="down",
    )

    stats = store.stats()

    assert stats["webhooks"]["pending"] == 1
    assert stats["webhooks_open"] == 1


def test_webhook_outbox_lifecycle(store: Store) -> None:
    item_id = store.record_item(
        original_path="/tmp/invoice.pdf",
        destination="/archive/invoice.pdf",
        category="rechnung",
        confidence=0.9,
        summary="Invoice",
        tags=["rechnung"],
        language="de",
        route_name="archiv",
    )

    delivery_id = store.enqueue_webhook(
        item_id=item_id,
        route_name="n8n",
        url="http://localhost:5678/webhook/kurier",
        payload={"payload_version": 1, "category": "rechnung"},
        last_error="Webhook delivery failed: n8n",
    )

    [pending] = store.list_webhook_outbox(statuses=("pending",))
    assert pending["id"] == delivery_id
    assert pending["item_id"] == item_id
    assert pending["attempt_count"] == 1
    assert pending["payload"]["payload_version"] == 1

    store.mark_webhook_failed(
        delivery_id,
        error="still down",
        next_attempt_at="2099-01-01T00:00:00+00:00",
    )

    [updated] = store.list_webhook_outbox(statuses=("pending",), due_only=False)
    assert updated["attempt_count"] == 2
    assert updated["last_error"] == "still down"
    assert updated["next_attempt_at"] == "2099-01-01T00:00:00+00:00"
    assert store.list_webhook_outbox(statuses=("pending",), due_only=True) == []

    store.mark_webhook_delivered(delivery_id)

    assert store.list_webhook_outbox(statuses=("pending", "failed")) == []
    [delivered] = store.list_webhook_outbox(statuses=("delivered",))
    assert delivered["attempt_count"] == 3
    assert delivered["delivered_at"] is not None


def test_enqueue_webhook_persists_retryable_delivery(store: Store) -> None:
    item_id = store.record_item(
        original_path="/tmp/article.md",
        destination="",
        category="artikel",
        confidence=0.8,
        summary="Article",
        tags=["python"],
        language="en",
        route_name="",
        status="pending",
    )

    delivery_id = store.enqueue_webhook(
        item_id=item_id,
        route_name="notify",
        url="https://example.com/hook",
        payload={"category": "artikel", "tags": ["python"]},
        last_error="HTTP 500",
    )

    [delivery] = store.list_webhook_outbox(statuses=("pending",))
    assert delivery["id"] == delivery_id
    assert delivery["item_id"] == item_id
    assert delivery["route_name"] == "notify"
    assert delivery["url"] == "https://example.com/hook"
    assert delivery["payload"] == {"category": "artikel", "tags": ["python"]}
    assert delivery["attempt_count"] == 1
    assert delivery["last_error"] == "HTTP 500"


def test_mark_webhook_delivered_removes_delivery_from_pending_outbox(store: Store) -> None:
    delivery_id = store.enqueue_webhook(
        item_id=None,
        route_name="notify",
        url="https://example.com/hook",
        payload={"category": "artikel"},
        last_error="HTTP 500",
    )

    store.mark_webhook_delivered(delivery_id)

    assert store.list_webhook_outbox(statuses=("pending",)) == []
    [delivery] = store.list_webhook_outbox(statuses=("delivered",))
    assert delivery["id"] == delivery_id
    assert delivery["status"] == "delivered"
    assert delivery["attempt_count"] == 2
    assert delivery["last_error"] is None
    assert delivery["delivered_at"] is not None


def test_mark_webhook_failed_keeps_delivery_pending_until_terminal(store: Store) -> None:
    delivery_id = store.enqueue_webhook(
        item_id=None,
        route_name="notify",
        url="https://example.com/hook",
        payload={"category": "artikel"},
        last_error="HTTP 500",
    )

    store.mark_webhook_failed(
        delivery_id,
        error="timeout",
        next_attempt_at="2026-05-16T12:00:00+00:00",
    )

    [pending] = store.list_webhook_outbox(statuses=("pending",), due_only=False)
    assert pending["id"] == delivery_id
    assert pending["attempt_count"] == 2
    assert pending["last_error"] == "timeout"
    assert pending["next_attempt_at"] == "2026-05-16T12:00:00+00:00"

    store.mark_webhook_failed(delivery_id, error="gone", next_attempt_at=None, terminal=True)

    assert store.list_webhook_outbox(statuses=("pending",)) == []
    [failed] = store.list_webhook_outbox(statuses=("failed",))
    assert failed["id"] == delivery_id
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 3
    assert failed["last_error"] == "gone"


def _record_failed_item(store, destination: str) -> int:
    return store.record_item(
        original_path="/tmp/doc.txt",
        destination=destination,
        category="rechnung",
        confidence=0.9,
        summary="s",
        tags=[],
        language="de",
        route_name="hook" if destination.startswith("http") else "archiv",
        status="failed",
    )


def test_reconcile_flips_webhook_failed_item_to_routed(tmp_path):
    store = Store(tmp_path / "test.db")
    item_id = _record_failed_item(store, "http://example.invalid/hook")
    delivery_id = store.enqueue_webhook(
        item_id=item_id,
        route_name="hook",
        url="http://example.invalid/hook",
        payload={"a": 1},
        last_error="boom",
    )

    # Solange die Zustellung offen ist, bleibt failed
    store.reconcile_item_after_webhook_delivery(item_id)
    assert store.get_recent(limit=1)[0]["status"] == "failed"

    store.mark_webhook_delivered(delivery_id)
    store.reconcile_item_after_webhook_delivery(item_id)
    assert store.get_recent(limit=1)[0]["status"] == "routed"


def test_reconcile_leaves_folder_failed_item_untouched(tmp_path):
    store = Store(tmp_path / "test.db")
    item_id = _record_failed_item(store, "")  # Folder-Fehlschlag: keine URL

    store.reconcile_item_after_webhook_delivery(item_id)

    assert store.get_recent(limit=1)[0]["status"] == "failed"
