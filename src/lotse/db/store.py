"""SQLite storage with FTS5 full-text search."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    original_path, category, summary, tags,
    content='items',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, original_path, category, summary, tags)
    VALUES (new.id, new.original_path, new.category, new.summary, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, original_path, category, summary, tags)
    VALUES ('delete', old.id, old.original_path, old.category, old.summary, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, original_path, category, summary, tags)
    VALUES ('delete', old.id, old.original_path, old.category, old.summary, old.tags);
    INSERT INTO items_fts(rowid, original_path, category, summary, tags)
    VALUES (new.id, new.original_path, new.category, new.summary, new.tags);
END;
"""


class Store:
    """SQLite-backed item store with full-text search."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

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
    ) -> int:
        """Record a processed item. Returns the item ID."""
        cursor = self._conn.execute(
            """INSERT INTO items (
                original_path, destination, category, confidence,
                summary, tags, language, route_name, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                original_path,
                destination,
                category,
                confidence,
                summary,
                json.dumps(tags),
                language,
                route_name,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across all items."""
        cursor = self._conn.execute(
            """SELECT items.*, rank
               FROM items_fts
               JOIN items ON items.id = items_fts.rowid
               WHERE items_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def recent(self, limit: int = 20) -> list[dict]:
        """Get most recently processed items."""
        cursor = self._conn.execute(
            "SELECT * FROM items ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def stats(self) -> dict:
        """Get processing statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        categories = self._conn.execute(
            "SELECT category, COUNT(*) as count FROM items "
            "GROUP BY category ORDER BY count DESC"
        ).fetchall()
        routes = self._conn.execute(
            "SELECT route_name, COUNT(*) as count FROM items "
            "GROUP BY route_name ORDER BY count DESC"
        ).fetchall()

        return {
            "total_items": total,
            "categories": {row["category"]: row["count"] for row in categories},
            "routes": {row["route_name"]: row["count"] for row in routes},
        }

    def close(self) -> None:
        self._conn.close()
