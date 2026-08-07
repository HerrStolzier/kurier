"""SQLite storage with FTS5 full-text search and sqlite-vec vector search."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def file_source_signature(st: os.stat_result) -> str:
    """Erkennungsmerkmal einer Quelldatei: Groesse und mtime in Nanosekunden.

    Dasselbe Merkmal, das der Watcher in-memory nutzt — hier persistiert,
    damit ein Neustart erkennt, ob genau diese Version schon geroutet wurde.
    """
    return f"{st.st_size}:{st.st_mtime_ns}"


TABLE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    destination TEXT,
    suggested_filename TEXT,
    destination_name TEXT,
    display_title TEXT,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT,
    tags TEXT,  -- JSON array
    language TEXT,
    route_name TEXT,
    content_text TEXT,  -- original content for re-embedding
    source_signature TEXT,  -- "size:mtime_ns" of the source file at ingest time
    status TEXT NOT NULL DEFAULT 'routed',  -- pending, routed, failed, undone
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

FTS_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    original_path, suggested_filename, destination_name, display_title,
    category, summary, tags, content_text,
    content='items',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(
        rowid, original_path, suggested_filename, destination_name,
        display_title, category, summary, tags, content_text
    )
    VALUES (
        new.id, new.original_path, new.suggested_filename, new.destination_name,
        new.display_title, new.category, new.summary, new.tags, new.content_text
    );
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(
        items_fts, rowid, original_path, suggested_filename, destination_name,
        display_title, category, summary, tags, content_text
    ) VALUES (
        'delete', old.id, old.original_path, old.suggested_filename, old.destination_name,
        old.display_title, old.category, old.summary, old.tags, old.content_text
    );
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(
        items_fts, rowid, original_path, suggested_filename, destination_name,
        display_title, category, summary, tags, content_text
    ) VALUES (
        'delete', old.id, old.original_path, old.suggested_filename, old.destination_name,
        old.display_title, old.category, old.summary, old.tags, old.content_text
    );
    INSERT INTO items_fts(
        rowid, original_path, suggested_filename, destination_name,
        display_title, category, summary, tags, content_text
    ) VALUES (
        new.id, new.original_path, new.suggested_filename, new.destination_name,
        new.display_title, new.category, new.summary, new.tags, new.content_text
    );
END;
"""

WEBHOOK_OUTBOX_SCHEMA = """\
CREATE TABLE IF NOT EXISTS webhook_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
    route_name TEXT NOT NULL,
    url TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, delivered, failed
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS webhook_outbox_status_next_idx
ON webhook_outbox(status, next_attempt_at);
"""

# sqlite-vec virtual table (created separately since it needs the extension loaded)
VEC_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0(
    embedding float[384]
);
"""

BETA_EVENTS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS beta_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    context_json TEXT,
    item_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
);
"""


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Try to load the sqlite-vec extension. Returns True if available."""
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except (ImportError, Exception) as e:
        logger.debug("sqlite-vec not available: %s", e)
        return False


def _path_name(value: str | None) -> str:
    """Return the basename of a path-like string, or an empty string."""
    if not value:
        return ""
    return Path(value).name


def _display_title(
    *,
    suggested_filename: str | None,
    destination_name: str | None,
    original_path: str,
) -> str:
    """Pick the most useful human-readable title for search and display."""
    if suggested_filename and suggested_filename.strip():
        return suggested_filename.strip()
    if destination_name and destination_name.strip():
        return destination_name.strip()
    return _path_name(original_path) or original_path


class Store:
    """SQLite-backed item store with full-text search and optional vector search."""

    def __init__(self, db_path: Path) -> None:
        import contextlib
        import os

        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)

        # Restrict database file to owner-only access
        if db_path.exists():
            with contextlib.suppress(OSError):
                os.chmod(db_path, 0o600)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(TABLE_SCHEMA)
        self._conn.executescript(BETA_EVENTS_SCHEMA)
        self._conn.executescript(WEBHOOK_OUTBOX_SCHEMA)
        self._migrate_status_column_if_needed()
        self._migrate_title_columns_if_needed()
        self._migrate_source_signature_column_if_needed()
        self._migrate_failure_reason_column_if_needed()
        self._migrate_fts_if_needed()
        self._conn.executescript(FTS_SCHEMA)
        self._backfill_title_fields_if_needed()

        # Try to enable vector search
        self._vec_enabled = _load_sqlite_vec(self._conn)
        if self._vec_enabled:
            self._conn.executescript(VEC_SCHEMA)
            logger.info("Vector search enabled (sqlite-vec)")
        else:
            logger.info("Vector search disabled (install sqlite-vec for semantic search)")

    def _migrate_fts_if_needed(self) -> None:
        """Recreate FTS table if schema has changed."""
        try:
            # Check if items_fts exists and has the right columns
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'items_fts'"
            ).fetchone()
            required_columns = (
                "content_text",
                "suggested_filename",
                "destination_name",
                "display_title",
            )
            if row and any(column not in (row[0] or "") for column in required_columns):
                logger.info("Migrating FTS index to include title signals")
                self._conn.executescript("""
                    DROP TRIGGER IF EXISTS items_ai;
                    DROP TRIGGER IF EXISTS items_ad;
                    DROP TRIGGER IF EXISTS items_au;
                    DROP TABLE IF EXISTS items_fts;
                """)
        except Exception as e:
            logger.debug("FTS migration check: %s", e)

    def _migrate_status_column_if_needed(self) -> None:
        """Add status column to items table if it doesn't exist yet (existing DBs)."""
        try:
            self._conn.execute("ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'routed'")
            self._conn.commit()
            logger.info("Migrated items table: added status column")
        except sqlite3.OperationalError as e:
            # Column already exists — that's fine
            if "duplicate column name" not in str(e).lower():
                logger.debug("Status column migration: %s", e)

    def _migrate_source_signature_column_if_needed(self) -> None:
        """Add source_signature column for older databases (stays NULL there)."""
        try:
            self._conn.execute("ALTER TABLE items ADD COLUMN source_signature TEXT")
            self._conn.commit()
            logger.info("Migrated items table: added source_signature column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.debug("source_signature column migration: %s", e)

    def _migrate_failure_reason_column_if_needed(self) -> None:
        """Add failure_reason column for older databases (stays NULL there)."""
        try:
            self._conn.execute("ALTER TABLE items ADD COLUMN failure_reason TEXT")
            self._conn.commit()
            logger.info("Migrated items table: added failure_reason column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.debug("failure_reason column migration: %s", e)
        # Zwischenstand-Datenbanken: Fehlergrund lag kurzzeitig in summary und
        # wuerde dort im Suchindex haengen bleiben — nach failure_reason umziehen.
        self._conn.execute(
            "UPDATE items SET failure_reason = summary, summary = '' "
            "WHERE route_name = '__error__' AND status = 'failed' "
            "AND (failure_reason IS NULL OR failure_reason = '') AND summary != ''"
        )
        self._conn.commit()

    def _migrate_title_columns_if_needed(self) -> None:
        """Add memory-search title columns for older databases."""
        columns = (
            ("suggested_filename", "TEXT"),
            ("destination_name", "TEXT"),
            ("display_title", "TEXT"),
        )
        for column_name, column_type in columns:
            try:
                self._conn.execute(f"ALTER TABLE items ADD COLUMN {column_name} {column_type}")
                self._conn.commit()
                logger.info("Migrated items table: added %s column", column_name)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.debug("Title column migration for %s: %s", column_name, e)

    def _backfill_title_fields_if_needed(self) -> None:
        """Fill derived title fields for existing rows after migrations."""
        rows = self._conn.execute(
            """SELECT id, original_path, destination, suggested_filename,
                      destination_name, display_title
               FROM items"""
        ).fetchall()

        updated = False
        for row in rows:
            destination_name = row["destination_name"] or _path_name(row["destination"])
            display_title = row["display_title"] or _display_title(
                suggested_filename=row["suggested_filename"],
                destination_name=destination_name,
                original_path=row["original_path"],
            )
            if destination_name != (row["destination_name"] or "") or display_title != (
                row["display_title"] or ""
            ):
                self._conn.execute(
                    "UPDATE items SET destination_name = ?, display_title = ? WHERE id = ?",
                    (destination_name, display_title, row["id"]),
                )
                updated = True

        if updated:
            self._conn.commit()
            logger.info("Backfilled title fields for existing items")

    @property
    def vec_enabled(self) -> bool:
        return self._vec_enabled

    def record_item(
        self,
        original_path: str,
        destination: str,
        category: str,
        confidence: float,
        summary: str,
        tags: list[str],
        language: str,
        route_name: str,
        suggested_filename: str = "",
        content_text: str = "",
        embedding: bytes | None = None,
        status: str = "routed",
        source_signature: str | None = None,
    ) -> int:
        """Record a processed item. Returns the item ID."""
        destination_name = _path_name(destination)
        display_title = _display_title(
            suggested_filename=suggested_filename,
            destination_name=destination_name,
            original_path=original_path,
        )
        cursor = self._conn.execute(
            """INSERT INTO items (
                original_path, destination, suggested_filename, destination_name,
                display_title, category, confidence, summary, tags, language,
                route_name, content_text,
                source_signature, status, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                original_path,
                destination,
                suggested_filename,
                destination_name,
                display_title,
                category,
                confidence,
                summary,
                json.dumps(tags),
                language,
                route_name,
                content_text,
                source_signature,
                status,
                datetime.now(UTC).isoformat(),
            ),
        )
        item_id = cursor.lastrowid or 0

        # Store embedding in vector table
        if embedding and self._vec_enabled:
            self._conn.execute(
                "INSERT INTO items_vec(rowid, embedding) VALUES (?, ?)",
                (item_id, embedding),
            )

        self._conn.commit()
        return item_id

    def upsert_failure(
        self,
        original_path: str,
        source_signature: str | None,
        reason: str,
    ) -> int:
        """Fehlschlag festhalten, ohne bei Wiederholungen Duplikate anzuhäufen.

        Existiert schon ein failed-Eintrag für dieselbe Datei im selben Stand,
        wird nur der Grund und der Zeitstempel aufgefrischt.
        """
        row = self._conn.execute(
            "SELECT id FROM items WHERE original_path = ? AND status = 'failed' "
            "AND ifnull(source_signature, '') = ifnull(?, '') "
            "ORDER BY id DESC LIMIT 1",
            (original_path, source_signature),
        ).fetchone()
        if row is not None:
            item_id = int(row["id"])
            self._conn.execute(
                "UPDATE items SET failure_reason = ?, created_at = ? WHERE id = ?",
                (reason, datetime.now(UTC).isoformat(), item_id),
            )
            self._conn.commit()
            return item_id

        item_id = self.record_item(
            original_path=original_path,
            destination="",
            category="",
            confidence=0.0,
            summary="",
            tags=[],
            language="",
            route_name="__error__",
            status="failed",
            source_signature=source_signature,
        )
        self.update_failure_reason(item_id, reason)
        return item_id

    def update_failure_reason(self, item_id: int, reason: str) -> None:
        """Grund für einen Fehlschlag festhalten — in eigener Spalte.

        Die Zusammenfassung bleibt unangetastet: Gelingt eine spätere
        Webhook-Zustellung, zeigen Suche und Liste weiter die Dokument-
        Zusammenfassung statt eines alten Fehlertexts (Review 2026-08-07).
        """
        self._conn.execute("UPDATE items SET failure_reason = ? WHERE id = ?", (reason, item_id))
        self._conn.commit()

    def get_failed_items(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fehlgeschlagene Einträge, neueste zuerst."""
        rows = self._conn.execute(
            "SELECT * FROM items WHERE status = 'failed' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_routing_metadata(self, item_id: int, destination: str, route_name: str) -> None:
        """Update destination-related fields after routing has completed."""
        row = self._conn.execute(
            "SELECT original_path, suggested_filename FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return

        destination_name = _path_name(destination)
        display_title = _display_title(
            suggested_filename=row["suggested_filename"],
            destination_name=destination_name,
            original_path=row["original_path"],
        )
        self._conn.execute(
            """UPDATE items
               SET destination = ?, destination_name = ?, display_title = ?, route_name = ?
               WHERE id = ?""",
            (destination, destination_name, display_title, route_name, item_id),
        )
        self._conn.commit()

    def search(
        self,
        query: str,
        limit: int = 20,
        query_embedding: bytes | None = None,
        mode: str = "auto",
    ) -> list[dict[str, Any]]:
        """Search items. Modes: 'fts' (keyword only), 'vec' (semantic only), 'auto' (hybrid).

        When mode='auto' and a query_embedding is provided, uses hybrid search
        with Reciprocal Rank Fusion to combine keyword + semantic results.
        """
        if mode == "vec" and query_embedding and self._vec_enabled:
            results = self._search_vec(query_embedding, limit)
        elif mode == "fts" or not query_embedding or not self._vec_enabled:
            results = self._search_fts(query, limit)
        else:
            results = self._search_hybrid(query, query_embedding, limit)
        # Final safety filter — no undone items should appear in any search path
        return [r for r in results if r.get("status") != "undone"]

    def _search_fts(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Full-text keyword search."""
        cursor = self._conn.execute(
            """SELECT items.*, rank
               FROM items_fts
               JOIN items ON items.id = items_fts.rowid
               WHERE items_fts MATCH ?
               AND items.status != 'undone'
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _search_vec(self, query_embedding: bytes, limit: int) -> list[dict[str, Any]]:
        """Pure vector similarity search."""
        vec_results = self._conn.execute(
            """SELECT rowid, distance
               FROM items_vec
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (query_embedding, limit),
        ).fetchall()

        results = []
        for row in vec_results:
            item = self._conn.execute(
                "SELECT * FROM items WHERE id = ? AND status != 'undone'", (row["rowid"],)
            ).fetchone()
            if item:
                d = dict(item)
                d["distance"] = row["distance"]
                results.append(d)
        return results

    def _search_hybrid(
        self, query: str, query_embedding: bytes, limit: int
    ) -> list[dict[str, Any]]:
        """Hybrid search using Reciprocal Rank Fusion (RRF).

        Combines FTS5 keyword results with sqlite-vec semantic results.
        RRF formula: score(d) = sum(1 / (k + rank(d))) across both systems.
        k=60 is the standard constant from the original RRF paper.
        """
        fetch_count = limit * 3  # Over-fetch for better fusion
        k = 60

        # Get FTS5 results
        fts_rows = self._conn.execute(
            """SELECT items.id, rank
               FROM items_fts
               JOIN items ON items.id = items_fts.rowid
               WHERE items_fts MATCH ?
               AND items.status != 'undone'
               ORDER BY rank
               LIMIT ?""",
            (query, fetch_count),
        ).fetchall()

        # Get vector results
        vec_rows = self._conn.execute(
            """SELECT rowid, distance
               FROM items_vec
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (query_embedding, fetch_count),
        ).fetchall()

        # Compute RRF scores
        rrf_scores: dict[int, float] = {}

        for rank_pos, row in enumerate(fts_rows, 1):
            doc_id = row["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1 / (k + rank_pos)

        for rank_pos, row in enumerate(vec_rows, 1):
            doc_id = row["rowid"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1 / (k + rank_pos)

        # Sort by RRF score and fetch full items
        top_ids = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)[:limit]

        results = []
        for doc_id in top_ids:
            item = self._conn.execute(
                "SELECT * FROM items WHERE id = ? AND status != 'undone'", (doc_id,)
            ).fetchone()
            if item:
                d = dict(item)
                d["rrf_score"] = rrf_scores[doc_id]
                results.append(d)
        return results

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get most recently processed items.

        Fehlgeschlagene Einträge haben ihre eigene Liste (get_failed_items) —
        in "Zuletzt erledigt" wären sie als 'Erledigt' beschriftet und falsch.
        """
        cursor = self._conn.execute(
            "SELECT * FROM items WHERE status != 'failed' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_status(self, item_id: int, status: str) -> None:
        """Update the status of an item."""
        self._conn.execute(
            "UPDATE items SET status = ? WHERE id = ?",
            (status, item_id),
        )
        self._conn.commit()

    def enqueue_webhook(
        self,
        *,
        item_id: int | None,
        route_name: str,
        url: str,
        payload: dict[str, Any],
        last_error: str,
        next_attempt_at: str | None = None,
    ) -> int:
        """Persist a failed webhook delivery for a later retry."""
        now = datetime.now(UTC).isoformat()
        cursor = self._conn.execute(
            """INSERT INTO webhook_outbox (
                item_id, route_name, url, payload_json, status, attempt_count,
                last_error, next_attempt_at, created_at, updated_at
               ) VALUES (?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?)""",
            (
                item_id,
                route_name,
                url,
                json.dumps(payload, ensure_ascii=False),
                last_error,
                next_attempt_at,
                now,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def list_webhook_outbox(
        self,
        *,
        statuses: tuple[str, ...] = ("pending", "failed"),
        due_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List persisted webhook deliveries."""
        if not statuses:
            return []

        placeholders = ",".join("?" for _ in statuses)
        params: list[Any] = list(statuses)
        where = [f"status IN ({placeholders})"]
        if due_only:
            now = datetime.now(UTC).isoformat()
            where.append("(next_attempt_at IS NULL OR next_attempt_at <= ?)")
            params.append(now)
        params.append(limit)

        cursor = self._conn.execute(
            f"""SELECT * FROM webhook_outbox
                WHERE {" AND ".join(where)}
                ORDER BY created_at ASC
                LIMIT ?""",
            params,
        )
        rows = []
        for row in cursor.fetchall():
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            rows.append(item)
        return rows

    def mark_webhook_delivered(self, delivery_id: int) -> None:
        """Mark a webhook outbox row as delivered."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """UPDATE webhook_outbox
               SET status = 'delivered',
                   attempt_count = attempt_count + 1,
                   last_error = NULL,
                   next_attempt_at = NULL,
                   updated_at = ?,
                   delivered_at = ?
               WHERE id = ?""",
            (now, now, delivery_id),
        )
        self._conn.commit()

    def reconcile_item_after_webhook_delivery(self, item_id: int) -> None:
        """Failed-Item wieder auf routed setzen, wenn alle Webhooks zugestellt sind.

        Greift nur bei Items, deren fehlgeschlagene Route ein Webhook war
        (destination = URL) — ein Item, das wegen einer Folder-Route failed
        ist, bleibt failed.
        """
        open_deliveries = self._conn.execute(
            """SELECT COUNT(*) FROM webhook_outbox
               WHERE item_id = ? AND status IN ('pending', 'failed')""",
            (item_id,),
        ).fetchone()[0]
        if open_deliveries:
            return
        self._conn.execute(
            """UPDATE items SET status = 'routed'
               WHERE id = ? AND status = 'failed' AND destination LIKE 'http%'""",
            (item_id,),
        )
        self._conn.commit()

    def mark_webhook_failed(
        self,
        delivery_id: int,
        *,
        error: str,
        next_attempt_at: str | None,
        terminal: bool = False,
    ) -> None:
        """Update a webhook outbox row after a failed retry attempt."""
        now = datetime.now(UTC).isoformat()
        status = "failed" if terminal else "pending"
        self._conn.execute(
            """UPDATE webhook_outbox
               SET status = ?,
                   attempt_count = attempt_count + 1,
                   last_error = ?,
                   next_attempt_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (status, error, next_attempt_at, now, delivery_id),
        )
        self._conn.commit()

    def undo_item(self, item_id: int) -> dict[str, Any] | None:
        """Get item info for undo (original_path, destination). Returns None if not found."""
        row = self._conn.execute(
            "SELECT id, original_path, destination FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None

    def delete_item(self, item_id: int) -> None:
        """Delete an item from DB (for undo)."""
        self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if self._vec_enabled:
            self._conn.execute("DELETE FROM items_vec WHERE rowid = ?", (item_id,))
        self._conn.commit()

    def get_all_items(self, category: str | None = None) -> list[dict[str, Any]]:
        """Get all items, optionally filtered by category. Excludes undone items."""
        if category is not None:
            cursor = self._conn.execute(
                "SELECT * FROM items WHERE category = ?"
                " AND status != 'undone' ORDER BY created_at DESC",
                (category,),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM items WHERE status != 'undone' ORDER BY created_at DESC",
            )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent(self, limit: int = 1) -> list[dict[str, Any]]:
        """Get most recent items."""
        cursor = self._conn.execute(
            "SELECT * FROM items ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def was_routed_unchanged(self, original_path: str, source_signature: str) -> bool:
        """True, wenn genau DIESE Version der Datei bereits erfolgreich geroutet wurde.

        Massgeblich ist ausschliesslich das persistierte Datei-Kennzeichen
        (Groesse:mtime_ns) — dieselbe Identitaets-Heuristik, die der Watcher
        in-memory nutzt. Ein Zeitvergleich stattdessen uebersieht Ersetzungen,
        die einen aelteren Zeitstempel mitbringen (cp -p, rsync, Backup-
        Restore) — die neue Datei wuerde fuer immer uebersprungen
        (Cross-Model-Review 2026-08-06, P1).

        Alt-Zeilen ohne Kennzeichen matchen bewusst NIE: nach dem Upgrade wird
        eine liegengebliebene Webhook-only-Datei damit genau einmal erneut
        verarbeitet (neue Zeile inklusive Kennzeichen). Ein Zeit-Fallback fuer
        diese Zeilen waere dauerhaft verlustbehaftet, die Einmal-Verarbeitung
        ist es nicht (Cross-Model-Review 2026-08-06, P1).

        Nur status='routed' zaehlt: pending/failed sollen erneut versucht werden.
        """
        row = self._conn.execute(
            "SELECT 1 FROM items"
            " WHERE original_path = ? AND status = 'routed' AND source_signature = ?"
            " LIMIT 1",
            (original_path, source_signature),
        ).fetchone()
        return row is not None

    def count_embeddings(self) -> int:
        """Count items that have embeddings stored."""
        if not self._vec_enabled:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM items_vec").fetchone()
        return row[0] if row else 0

    def stats(self) -> dict[str, Any]:
        """Get processing statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        # Fehlgeschlagene Einträge haben keine Kategorie/Route — sie würden in
        # den Statistiken als leeres Schildchen auftauchen.
        categories = self._conn.execute(
            "SELECT category, COUNT(*) as count FROM items "
            "WHERE status != 'failed' AND category != '' "
            "GROUP BY category ORDER BY count DESC"
        ).fetchall()
        routes = self._conn.execute(
            "SELECT route_name, COUNT(*) as count FROM items "
            "WHERE status != 'failed' "
            "GROUP BY route_name ORDER BY count DESC"
        ).fetchall()
        webhook_rows = self._conn.execute(
            "SELECT status, COUNT(*) as count FROM webhook_outbox GROUP BY status"
        ).fetchall()
        webhooks = {row["status"]: row["count"] for row in webhook_rows}

        result = {
            "total_items": total,
            "categories": {row["category"]: row["count"] for row in categories},
            "routes": {row["route_name"]: row["count"] for row in routes},
            "webhooks": webhooks,
            "webhooks_open": webhooks.get("pending", 0) + webhooks.get("failed", 0),
            "vec_enabled": self._vec_enabled,
        }

        if self._vec_enabled:
            result["embeddings"] = self.count_embeddings()

        return result

    def low_confidence(self, threshold: float = 0.6, limit: int = 50) -> list[dict[str, Any]]:
        """Return items classified with confidence below *threshold*.

        Excludes items that are already in the review queue (route_name == '__review__')
        and items that were undone.
        """
        cursor = self._conn.execute(
            """SELECT * FROM items
               WHERE confidence < ?
               AND status != 'undone'
               AND status != 'failed'
               AND route_name != '__review__'
               ORDER BY confidence ASC, created_at DESC
               LIMIT ?""",
            (threshold, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_category(self, item_id: int, new_category: str) -> None:
        """Update an item's category and treat the manual correction as confirmed."""
        self._conn.execute(
            "UPDATE items SET category = ?, confidence = 1.0 WHERE id = ?",
            (new_category, item_id),
        )
        self._conn.commit()

    def confirm_classification(self, item_id: int) -> None:
        """Mark an item's classification as confirmed (sets confidence to 1.0)."""
        self._conn.execute(
            "UPDATE items SET confidence = 1.0 WHERE id = ?",
            (item_id,),
        )
        self._conn.commit()

    def record_beta_event(
        self,
        event_type: str,
        message: str,
        *,
        severity: str = "info",
        context: dict[str, Any] | None = None,
        item_id: int | None = None,
    ) -> int:
        """Record a local beta signal for later UX hardening."""
        cursor = self._conn.execute(
            """INSERT INTO beta_events (
                event_type, severity, message, context_json, item_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                severity,
                message,
                json.dumps(context or {}, ensure_ascii=False),
                item_id,
                datetime.now(UTC).isoformat(),
            ),
        )
        event_id = cursor.lastrowid or 0
        self._conn.commit()
        return event_id

    def recent_beta_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent local beta signals."""
        cursor = self._conn.execute(
            """SELECT beta_events.*, items.display_title
               FROM beta_events
               LEFT JOIN items ON items.id = beta_events.item_id
               ORDER BY beta_events.created_at DESC
               LIMIT ?""",
            (limit,),
        )
        events = []
        for row in cursor.fetchall():
            event = dict(row)
            try:
                event["context"] = json.loads(event.pop("context_json") or "{}")
            except json.JSONDecodeError:
                event["context"] = {}
            events.append(event)
        return events

    def beta_event_summary(self, days: int = 7) -> dict[str, Any]:
        """Summarize beta signals from the last *days* days."""
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """SELECT event_type, severity, COUNT(*) AS count
               FROM beta_events
               WHERE created_at >= ?
               GROUP BY event_type, severity
               ORDER BY count DESC, event_type ASC""",
            (since,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        return {
            "days": days,
            "total": sum(row["count"] for row in rows),
            "by_type": rows,
        }

    def close(self) -> None:
        self._conn.close()
