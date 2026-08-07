"""Verständliche Fehlermeldungen für rohe Exceptions.

Nutzer ohne Technik-Kenntnisse können mit "Connection refused" oder einem
Traceback-Fragment nichts anfangen. friendly_error() übersetzt bekannte
Fehlerbilder in einen Satz plus Handlungsanweisung.
"""

from __future__ import annotations

_CONNECTION_MARKERS = (
    "connection refused",
    "connect call failed",
    "errno 61",
    "errno 111",
    # EPERM-Wortlaut: httpx meldet einen blockierten Verbindungsversuch unter
    # macOS als ConnectError('[Errno 1] Operation not permitted'). Gefahrlos,
    # weil typisierte Datei-Fehler (PermissionError) VOR den Markern greifen.
    "operation not permitted",
    "timed out",
    "timeout",
    "urlopen error",
    "failed to establish",
)


def friendly_error(exc: BaseException, provider: str | None = None) -> str:
    """Übersetzt eine Exception in eine Meldung, die sagt, was jetzt zu tun ist.

    `provider` steuert den Ratschlag bei Verbindungsfehlern: Bei Ollama hilft
    "App öffnen", bei Cloud-Anbietern eher Internet/API-Schlüssel prüfen.
    """
    text = str(exc)
    lowered = text.lower()

    if isinstance(exc, PermissionError):
        return "Kein Zugriff auf die Datei oder den Ordner. Prüfe die Berechtigungen."
    if isinstance(exc, FileNotFoundError):
        return "Die Datei oder der Ordner wurde nicht gefunden. Prüfe, ob der Pfad noch stimmt."

    if any(marker in lowered for marker in _CONNECTION_MARKERS):
        if provider == "ollama":
            return (
                "Die lokale KI ist nicht erreichbar. "
                "Öffne die Ollama-App und versuche es danach erneut."
            )
        if provider:
            return (
                f"Der KI-Anbieter '{provider}' ist gerade nicht erreichbar. "
                "Prüfe Internet-Verbindung und API-Schlüssel, dann noch einmal versuchen."
            )
        return "Die KI ist gerade nicht erreichbar. Versuche es gleich noch einmal."
    if isinstance(exc, OSError | UnicodeDecodeError) or "failed to open" in lowered:
        return "Die Datei konnte nicht gelesen werden. Prüfe, ob sie sich öffnen lässt."

    first_line = text.strip().splitlines()[0][:80] if text.strip() else exc.__class__.__name__
    return f"Etwas hat nicht geklappt ({first_line}). Versuche es noch einmal."
