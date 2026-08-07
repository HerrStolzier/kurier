"""Shared ingest workflows."""

from __future__ import annotations

from pathlib import Path

from arkiv.application.context import AppContext
from arkiv.core.router import RouteResult
from arkiv.db.store import Store, file_source_signature


def ingest_file(ctx: AppContext, file_path: Path) -> RouteResult:
    """Ingest a file through the shared application context."""
    return ctx.engine.ingest_file(file_path)


def already_handled_unchanged(store: Store, path: Path) -> bool:
    """True, wenn diese Datei-Version bereits erledigt ist ODER noch in Zustellung.

    Dann darf ein Startscan sie liegen lassen: Webhook-only-Routen lassen die
    Datei bewusst im Eingang, und ohne diese persistente Pruefung wuerde jeder
    Neustart des Watchers sie erneut verarbeiten und den Webhook erneut feuern
    (Cross-Model-Review 2026-08-06, P1).

    Zwei Faelle zaehlen als "nicht nochmal anfassen":
    1. erfolgreich geroutet und seitdem unveraendert,
    2. Zustellung fehlgeschlagen, aber die Outbox-Zeile ist noch offen — die
       gehoert dem Retry-Pfad. Ohne (2) erzeugte jeder Neustart eine zweite
       Zustellung fuer dasselbe Dokument (Cross-Model-Review 2026-08-07, P1).

    Geprueft wird ausschliesslich gegen den Inhalts-Fingerabdruck. Zeilen im
    alten Groesse:mtime-Format matchen bewusst NICHT: sie weiterhin zu
    akzeptieren wuerde fuer genau diese Dateien die Luecke offen halten, die der
    Fingerabdruck schliesst (gleiche Groesse, erhaltener Zeitstempel, anderer
    Inhalt -> fuer immer uebersprungen). Der Preis ist derselbe wie bei Zeilen
    ganz ohne Kennzeichen: nach dem Upgrade wird eine liegengebliebene
    Webhook-only-Datei genau einmal erneut verarbeitet, danach traegt ihre neue
    Zeile den Fingerabdruck (Cross-Model-Review 2026-08-07, P1)."""
    try:
        signature = file_source_signature(path)
    except OSError:
        return False
    path_str = str(path)
    if store.was_routed_unchanged(path_str, signature):
        return True
    return store.has_open_webhook_delivery(path_str, signature)


def ingest_text(ctx: AppContext, text: str, name: str = "text_input") -> RouteResult:
    """Ingest text through the shared application context."""
    return ctx.engine.ingest_text(text, name=name)
