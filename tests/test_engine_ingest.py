"""Tests for ingest_file status persistence (routed vs. failed)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from arkiv.core.classifier import Classification
from arkiv.core.config import ArkivConfig
from arkiv.core.engine import Engine
from arkiv.plugins.spec import hookimpl


def _make_engine(tmp_path: Path, routes: dict) -> Engine:
    config = ArkivConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
        routes=routes,
    )
    return Engine(config)


def _classification() -> Classification:
    return Classification(
        category="rechnung",
        confidence=0.9,
        summary="Testrechnung",
        tags=["test"],
        language="de",
    )


def _ingest(engine: Engine, source: Path):
    with (
        patch.object(engine.classifier, "classify", return_value=_classification()),
        patch.object(engine, "_generate_embedding", return_value=None),
    ):
        return engine.ingest_file(source)


def test_ingest_success_persists_routed_status(tmp_path: Path) -> None:
    routes = {
        "archiv": {
            "type": "folder",
            "path": str(tmp_path / "archiv"),
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
            "rename": False,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert result.success
    item = engine.store.get_recent(limit=1)[0]
    assert item["status"] == "routed"
    assert item["route_name"] == "archiv"
    assert item["destination"].endswith("invoice.txt")


def test_ingest_unsuccessful_route_persists_failed_status(tmp_path: Path) -> None:
    # Folder-Route ohne Pfad: execute() liefert success=False ohne Exception
    routes = {
        "kaputt": {
            "type": "folder",
            "path": None,
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert not result.success
    item = engine.store.get_recent(limit=1)[0]
    assert item["status"] == "failed"
    assert item["route_name"] == "kaputt"


class _RecorderPlugin:
    """Testplugin: protokolliert on_routed-Aufrufe."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    @hookimpl
    def on_routed(self, path: str, destination: str, route_name: str) -> None:
        self.calls.append((path, destination, route_name))


class _ExplodingPlugin:
    @hookimpl
    def on_routed(self, path: str, destination: str, route_name: str) -> None:
        raise RuntimeError("plugin kaputt")


def _folder_routes(tmp_path: Path) -> dict:
    return {
        "archiv": {
            "type": "folder",
            "path": str(tmp_path / "archiv"),
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
            "rename": False,
        }
    }


def test_on_routed_hook_fires_once_with_final_result(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _folder_routes(tmp_path))
    recorder = _RecorderPlugin()
    engine.plugin_manager.register(recorder, name="recorder")
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert result.success
    assert len(recorder.calls) == 1
    path, destination, route_name = recorder.calls[0]
    assert path == str(source)
    assert destination.endswith("invoice.txt")
    assert route_name == "archiv"


def test_on_routed_hook_not_fired_on_failure(tmp_path: Path) -> None:
    routes = {
        "kaputt": {
            "type": "folder",
            "path": None,
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
        }
    }
    engine = _make_engine(tmp_path, routes)
    recorder = _RecorderPlugin()
    engine.plugin_manager.register(recorder, name="recorder")
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert not result.success
    assert recorder.calls == []


def test_exploding_on_routed_hook_does_not_break_ingest(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, _folder_routes(tmp_path))
    engine.plugin_manager.register(_ExplodingPlugin(), name="boom")
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert result.success
    assert engine.store.get_recent(limit=1)[0]["status"] == "routed"


def test_extraction_fehler_erzeugt_failed_eintrag(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, {})
    source = tmp_path / "kaputt.pdf"
    source.write_text("kein echtes pdf")

    with patch.object(engine, "_extract_content", side_effect=OSError("read error")):
        result = engine.ingest_file(source)

    assert not result.success
    failed = engine.store.get_failed_items()
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["failure_reason"]  # verständlicher Grund gespeichert


def test_leere_datei_erzeugt_failed_eintrag(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, {})
    source = tmp_path / "leer.txt"
    source.write_text("   ")

    result = engine.ingest_file(source)

    assert not result.success
    failed = engine.store.get_failed_items()
    assert len(failed) == 1
    assert "leer" in failed[0]["failure_reason"]


def test_wiederholter_fehlschlag_haeuft_keine_duplikate_an(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, {})
    source = tmp_path / "leer.txt"
    source.write_text("   ")

    engine.ingest_file(source)
    engine.ingest_file(source)
    engine.ingest_file(source)

    assert len(engine.store.get_failed_items()) == 1


def test_klassifikations_fehler_erzeugt_failed_eintrag(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, {})
    source = tmp_path / "text.txt"
    source.write_text("Inhalt vorhanden")

    with patch.object(
        engine.classifier, "classify", side_effect=ConnectionError("Connection refused")
    ):
        result = engine.ingest_file(source)

    assert not result.success
    failed = engine.store.get_failed_items()
    assert len(failed) == 1
    assert "Ollama" in failed[0]["failure_reason"]


def test_fehlschlag_blockiert_spaeteren_erfolg_nicht(tmp_path: Path) -> None:
    routes = {
        "archiv": {
            "type": "folder",
            "path": str(tmp_path / "archiv"),
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
            "rename": False,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("   ")
    engine.ingest_file(source)  # leer -> failed

    source.write_text("Rechnung 42")
    result = _ingest(engine, source)

    assert result.success
    assert engine.store.get_recent(limit=1)[0]["status"] == "routed"


def test_routing_fehler_speichert_grund_statt_zusammenfassung(tmp_path: Path) -> None:
    routes = {
        "archiv": {
            "type": "folder",
            "path": "",  # Folder-Route ohne Pfad -> Routing schlaegt fehl
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
            "rename": False,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    result = _ingest(engine, source)

    assert not result.success
    failed = engine.store.get_failed_items()
    assert len(failed) == 1
    assert "fehlgeschlagen" in failed[0]["failure_reason"]
    assert failed[0]["summary"] == "Testrechnung"


def test_failed_platzhalter_erscheinen_nicht_in_der_prueferliste(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, {})
    source = tmp_path / "leer.txt"
    source.write_text("   ")
    engine.ingest_file(source)

    assert engine.store.get_failed_items()
    assert engine.store.low_confidence(threshold=0.6) == []


def test_neustart_ingested_datei_mit_offener_zustellung_nicht_erneut(tmp_path: Path) -> None:
    """Webhook-only-Fehlschlag: Datei bleibt im Eingang, Outbox-Zeile bleibt offen.

    Der Startscan des Watchers darf sie beim Neustart NICHT erneut verarbeiten —
    sonst entsteht eine zweite Zustellung fuer dasselbe Dokument und
    `kurier webhooks retry` sendet es doppelt (Cross-Model-Review 2026-08-07, P1).
    """
    from arkiv.application.ingest import already_handled_unchanged

    routes = {
        "notify": {
            "type": "webhook",
            "url": "https://example.com/hook",
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    with patch("arkiv_webhook.send_webhook", return_value=False):
        result = _ingest(engine, source)

    assert not result.success
    assert source.exists()  # Webhook-only bewegt die Datei nicht
    assert len(engine.store.list_webhook_outbox(statuses=("pending", "failed"))) == 1

    # Neustart: frischer Store-Handle auf dieselbe Datenbank
    restarted = _make_engine(tmp_path, routes)
    assert already_handled_unchanged(restarted.store, source) is True

    # Und wenn der Startscan sie trotzdem anfassen wuerde: die Pruefung ist der
    # einzige Schutz — hier belegen wir, dass keine zweite Zeile entstanden ist.
    assert len(restarted.store.list_webhook_outbox(statuses=("pending", "failed"))) == 1


def test_neustart_verarbeitet_geaenderte_datei_trotz_offener_zustellung(tmp_path: Path) -> None:
    """Wird die Datei nach dem Fehlschlag ersetzt, ist sie ein neuer Vorgang."""
    from arkiv.application.ingest import already_handled_unchanged

    routes = {
        "notify": {
            "type": "webhook",
            "url": "https://example.com/hook",
            "categories": ["rechnung"],
            "confidence_threshold": 0.7,
        }
    }
    engine = _make_engine(tmp_path, routes)
    source = tmp_path / "invoice.txt"
    source.write_text("Rechnung 42")

    with patch("arkiv_webhook.send_webhook", return_value=False):
        _ingest(engine, source)

    source.write_text("Rechnung 42 — korrigierte Fassung")

    assert already_handled_unchanged(engine.store, source) is False
