"""Shared status and listing workflows."""

from __future__ import annotations

from typing import Any

from arkiv.application.context import AppContext


def get_status(ctx: AppContext) -> dict[str, Any]:
    """Return current processing status."""
    return ctx.engine.stats()


def get_recent_items(ctx: AppContext, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent items for UI/API surfaces."""
    return ctx.engine.store.recent(limit=limit)


def get_failed_items(ctx: AppContext, *, limit: int = 20) -> list[dict[str, Any]]:
    """Fehlgeschlagene Einträge für UI-Flächen."""
    return ctx.engine.store.get_failed_items(limit=limit)
