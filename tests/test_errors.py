"""Tests für friendly_error()."""

from __future__ import annotations

from urllib.error import URLError

from arkiv.core.errors import friendly_error


def test_connection_refused_wird_zu_ollama_hinweis() -> None:
    msg = friendly_error(URLError(ConnectionRefusedError(61, "Connection refused")))
    assert "Ollama" in msg
    assert "erneut" in msg


def test_timeout_wird_zu_ollama_hinweis() -> None:
    msg = friendly_error(TimeoutError("timed out"))
    assert "Ollama" in msg


def test_file_not_found_nennt_pfad_pruefen() -> None:
    msg = friendly_error(FileNotFoundError(2, "No such file"))
    assert "nicht gefunden" in msg


def test_permission_error_nennt_berechtigungen() -> None:
    msg = friendly_error(PermissionError(13, "Permission denied auf Datei"))
    assert "Berechtigungen" in msg


def test_unbekannter_fehler_bleibt_kurz_und_handlungsorientiert() -> None:
    msg = friendly_error(ValueError("x" * 500))
    assert len(msg) < 200
    assert "noch einmal" in msg or "Versuche" in msg


def test_leere_exception_zeigt_klassennamen() -> None:
    msg = friendly_error(ValueError())
    assert "ValueError" in msg
