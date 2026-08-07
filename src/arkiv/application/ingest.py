"""Shared ingest workflows."""

from __future__ import annotations

from pathlib import Path

from arkiv.application.context import AppContext
from arkiv.core.router import RouteResult
from arkiv.db.store import Store, file_source_signature


def ingest_file(ctx: AppContext, file_path: Path) -> RouteResult:
    """Ingest a file through the shared application context."""
    return ctx.engine.ingest_file(file_path)


def already_routed_unchanged(store: Store, path: Path) -> bool:
    """True, wenn diese Datei seit ihrem letzten Schreibzeitpunkt schon
    erfolgreich geroutet wurde.

    Dann darf ein Startscan sie liegen lassen: Webhook-only-Routen lassen die
    Datei bewusst im Eingang, und ohne diese persistente Pruefung wuerde jeder
    Neustart des Watchers sie erneut verarbeiten und den Webhook erneut feuern
    (Cross-Model-Review 2026-08-06, P1)."""
    try:
        st = path.stat()
    except OSError:
        return False
    return store.was_routed_unchanged(str(path), file_source_signature(st))


def ingest_text(ctx: AppContext, text: str, name: str = "text_input") -> RouteResult:
    """Ingest text through the shared application context."""
    return ctx.engine.ingest_text(text, name=name)
