"""Tests for the SQLite store."""

import os
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


def _record_routed(store: Store, path: str, status: str, signature: str | None = None) -> None:
    store.record_item(
        original_path=path,
        destination="",
        category="notiz",
        confidence=0.9,
        summary="",
        tags=[],
        language="de",
        route_name="webhook",
        status=status,
        source_signature=signature,
    )


def test_was_routed_unchanged_matches_on_source_signature(store: Store) -> None:
    """Massgeblich ist das persistierte Datei-Kennzeichen. Ein Zeitvergleich
    stattdessen wuerde eine Ersetzung mit aelterem Zeitstempel (cp -p, rsync,
    Backup-Restore) fuer immer ueberspringen
    (Cross-Model-Review 2026-08-06, P1)."""
    _record_routed(store, "/inbox/bleibt.txt", "routed", signature="100:200")
    _record_routed(store, "/inbox/kaputt.txt", "failed", signature="100:200")

    assert store.was_routed_unchanged("/inbox/bleibt.txt", "100:200") is True
    # Gleicher Pfad, aber ANDERE Datei (Ersetzung, egal mit welchem
    # Zeitstempel): Kennzeichen passt nicht -> muss verarbeitet werden.
    assert store.was_routed_unchanged("/inbox/bleibt.txt", "999:111") is False
    # failed soll erneut versucht werden.
    assert store.was_routed_unchanged("/inbox/kaputt.txt", "100:200") is False
    assert store.was_routed_unchanged("/inbox/unbekannt.txt", "100:200") is False


def test_was_routed_unchanged_never_matches_legacy_rows(store: Store) -> None:
    """Alt-Zeilen aus der Zeit vor der Kennzeichen-Spalte matchen bewusst NIE:
    einmaliges Neu-Verarbeiten nach dem Upgrade ist verkraftbar, ein
    Zeit-Fallback waere bei Ersetzungen mit aelterem Zeitstempel dauerhaft
    verlustbehaftet (Cross-Model-Review 2026-08-06, P1)."""
    store._conn.execute(
        "INSERT INTO items (original_path, category, confidence, status, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("/inbox/alt.txt", "notiz", 0.9, "routed", "2026-01-01T12:00:00.400000+00:00"),
    )

    assert store.was_routed_unchanged("/inbox/alt.txt", "1:1") is False


def test_file_source_signature_differs_for_same_size_and_mtime(tmp_path: Path) -> None:
    """Der Fingerabdruck muss zwei Dateien unterscheiden, die in Groesse UND
    Zeitstempel uebereinstimmen — genau die Lage nach einem Backup-Restore oder
    `cp -p`. Mit dem alten Groesse:mtime-Merkmal galt der neue Inhalt als
    bereits verarbeitet und wurde dauerhaft uebersprungen
    (Cross-Model-Review 2026-08-07, P1)."""
    from arkiv.db.store import file_source_signature

    original = tmp_path / "rechnung.txt"
    original.write_text("Rechnung Nummer 1")
    replacement = tmp_path / "rechnung_neu.txt"
    replacement.write_text("Rechnung Nummer 2")  # gleiche Laenge, anderer Inhalt

    stat_original = original.stat()
    os.utime(replacement, ns=(stat_original.st_atime_ns, stat_original.st_mtime_ns))
    stat_replacement = replacement.stat()

    # Vorbedingung: fuer das alte Merkmal sind die beiden nicht zu unterscheiden.
    assert (stat_original.st_size, stat_original.st_mtime_ns) == (
        stat_replacement.st_size,
        stat_replacement.st_mtime_ns,
    )
    assert file_source_signature(original) != file_source_signature(replacement)


def test_was_routed_unchanged_ignores_old_format_signatures(store: Store) -> None:
    """Zeilen im frueheren Groesse:mtime-Format duerfen nach der Umstellung NICHT
    mehr matchen. Wuerden sie es, bliebe fuer genau diese Dateien die Luecke
    offen, die der Fingerabdruck schliesst — gleiche Groesse, erhaltener
    Zeitstempel, anderer Inhalt (Cross-Model-Review 2026-08-07, P1)."""
    _record_routed(store, "/inbox/alt-format.txt", "routed", signature="100:200")
    _record_routed(store, "/inbox/neu-format.txt", "routed", signature="sha256:abc:17")

    assert store.was_routed_unchanged("/inbox/alt-format.txt", "sha256:xyz:17") is False
    assert store.was_routed_unchanged("/inbox/neu-format.txt", "sha256:abc:17") is True
    # Anderer Fingerabdruck -> die Datei wurde ersetzt.
    assert store.was_routed_unchanged("/inbox/neu-format.txt", "sha256:xyz:17") is False
    # Ohne Kennzeichen darf nichts matchen.
    assert store.was_routed_unchanged("/inbox/neu-format.txt", "") is False


def test_upsert_failure_fuehrt_alt_signatur_beim_sha_umbau_zusammen(tmp_path):
    from arkiv.db.store import Store

    store = Store(tmp_path / "t.db")
    alt = store.upsert_failure("/inbox/kaputt.pdf", "123:456789", "alter Grund")
    neu = store.upsert_failure("/inbox/kaputt.pdf", "sha256:abc:123", "neuer Grund")
    assert alt == neu
    failed = store.get_failed_items()
    assert len(failed) == 1
    assert failed[0]["failure_reason"] == "neuer Grund"
    assert failed[0]["source_signature"] == "sha256:abc:123"


def test_upsert_failure_bevorzugt_exakten_treffer_vor_alt_eintrag(tmp_path):
    from arkiv.db.store import Store

    store = Store(tmp_path / "t2.db")
    sha = store.upsert_failure("/inbox/x", "sha256:a:1", "sha zuerst")
    store.upsert_failure("/inbox/x", "1:2", "alt danach")
    retry = store.upsert_failure("/inbox/x", "sha256:a:1", "sha erneut")
    assert retry == sha
    failed = store.get_failed_items()
    assert len([f for f in failed if f["source_signature"] == "sha256:a:1"]) == 1
