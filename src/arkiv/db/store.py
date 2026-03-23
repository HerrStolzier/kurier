"""SQLite storage with FTS5 full-text search and sqlite-vec vector search."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA = """\
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    destination TEXT,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT,
    tags TEXT,  -- JSON array
    language TEXT,
    route_name TEXT,
    content_text TEXT,  -- original content for re-embedding
    status TEXT NOT NULL DEFAULT 'routed',  -- pending, routed, failed, undone
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    original_path, category, summary, tags, content_text,
    content='items',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, original_path, category, summary, tags, content_text)
    VALUES (new.id, new.original_path, new.category, new.summary, new.tags, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(
        items_fts, rowid, original_path, category, summary, tags, content_text
    ) VALUES (
        'delete', old.id, old.original_path, old.category,
        old.summary, old.tags, old.content_text
    );
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(
        items_fts, rowid, original_path, category, summary, tags, content_text
    ) VALUES (
        'delete', old.id, old.original_path, old.category,
        old.summary, old.tags, old.content_text
    );
    INSERT INTO items_fts(
        rowid, original_path, category, summary, tags, content_text
    ) VALUES (
        new.id, new.original_path, new.category,
        new.summary, new.tags, new.content_text
    );
END;
"""

# sqlite-vec virtual table (created separately since it needs the extension loaded)
VEC_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0(
    embedding float[384]
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
        self._migrate_fts_if_needed()
        self._conn.executescript(SCHEMA)
        self._migrate_status_column_if_needed()

        # Try to enable vector search
        self._vec_enabled = _load_sqlite_vec(self._conn)
        if self._vec_enabled:
            self._conn.executescript(VEC_SCHEMA)
            logger.info("Vector search enabled (sqlite-vec)")
        else:
            logger.info("Vector search disabled (install sqlite-vec for semantic search)")

    def _migrate_fts_if_needed(self) -> None:
        """Recreate FTS table if schema has changed (e.g., content_text added)."""
        try:
            # Check if items_fts exists and has the right columns
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'items_fts'"
            ).fetchone()
            if row and "content_text" not in (row[0] or ""):
                logger.info("Migrating FTS index to include content_text")
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
        content_text: str = "",
        embedding: bytes | None = None,
        status: str = "routed",
    ) -> int:
        """Record a processed item. Returns the item ID."""
        cursor = self._conn.execute(
            """INSERT INTO items (
                original_path, destination, category, confidence,
                summary, tags, language, route_name, content_text,
                status, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                original_path,
                destination,
                category,
                confidence,
                summary,
                json.dumps(tags),
                language,
                route_name,
                content_text,
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
        """Get most recently processed items."""
        cursor = self._conn.execute(
            "SELECT * FROM items ORDER BY created_at DESC LIMIT ?",
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

    def count_embeddings(self) -> int:
        """Count items that have embeddings stored."""
        if not self._vec_enabled:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM items_vec").fetchone()
        return row[0] if row else 0

    def stats(self) -> dict[str, Any]:
        """Get processing statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        categories = self._conn.execute(
            "SELECT category, COUNT(*) as count FROM items GROUP BY category ORDER BY count DESC"
        ).fetchall()
        routes = self._conn.execute(
            "SELECT route_name, COUNT(*) as count FROM items "
            "GROUP BY route_name ORDER BY count DESC"
        ).fetchall()

        result = {
            "total_items": total,
            "categories": {row["category"]: row["count"] for row in categories},
            "routes": {row["route_name"]: row["count"] for row in routes},
            "vec_enabled": self._vec_enabled,
        }

        if self._vec_enabled:
            result["embeddings"] = self.count_embeddings()

        return result

    def close(self) -> None:
        self._conn.close()
