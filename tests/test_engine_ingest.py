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


def test_inhaltsgleiche_datei_unter_anderem_namen_ist_ein_duplikat(tmp_path: Path) -> None:
    """Dieselbe Rechnung als scan1.pdf und kopie.pdf: das zweite Mal wird nicht
    noch einmal klassifiziert und nicht noch einmal abgelegt."""
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
    inhalt = "Rechnung 42 von den Stadtwerken"

    erste = tmp_path / "scan1.txt"
    erste.write_text(inhalt)
    assert _ingest(engine, erste).success

    zweite = tmp_path / "kopie.txt"
    zweite.write_text(inhalt)
    with patch.object(engine.classifier, "classify") as klassifikation:
        ergebnis = engine.ingest_file(zweite)

    klassifikation.assert_not_called()  # kein zweiter KI-Aufruf
    assert ergebnis.route_name == "__duplicate__"
    assert "Inhaltsgleich mit:" in ergebnis.message
    assert zweite.exists()  # Datei bleibt liegen, wo sie ist

    eintraege = engine.store.recent(limit=10)
    duplikat = next(i for i in eintraege if i["status"] == "duplicate")
    original = next(i for i in eintraege if i["status"] == "routed")
    assert duplikat["duplicate_of"] == original["id"]


def test_unterschiedlicher_inhalt_ist_kein_duplikat(tmp_path: Path) -> None:
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

    a = tmp_path / "a.txt"
    a.write_text("Rechnung 42")
    b = tmp_path / "b.txt"
    b.write_text("Rechnung 43")

    assert _ingest(engine, a).success
    zweites = _ingest(engine, b)

    assert zweites.route_name != "__duplicate__"
    assert zweites.success


def test_duplikat_zaehlt_nicht_in_die_statistik(tmp_path: Path) -> None:
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
    inhalt = "Rechnung 42"
    for name in ("a.txt", "b.txt"):
        p = tmp_path / name
        p.write_text(inhalt)
        _ingest(engine, p)

    stats = engine.store.stats()

    assert stats["categories"].get("rechnung") == 1
    assert "__duplicate__" not in stats["routes"]


def test_fehlgeschlagenes_original_blockiert_keine_neuverarbeitung(tmp_path: Path) -> None:
    """Nur erfolgreich abgelegte Dokumente zaehlen als Original."""
    engine = _make_engine(tmp_path, {})
    a = tmp_path / "leer.txt"
    a.write_text("   ")
    engine.ingest_file(a)  # -> failed

    b = tmp_path / "auch_leer.txt"
    b.write_text("   ")
    ergebnis = engine.ingest_file(b)

    assert ergebnis.route_name != "__duplicate__"


def test_erneuter_scan_legt_kein_zweites_duplikat_an(tmp_path: Path) -> None:
    """Die Duplikat-Datei bleibt im Eingang liegen. Ohne diese Pruefung legte
    jeder Watcher-Neustart einen weiteren Eintrag an (Review 2026-08-09)."""
    from arkiv.application.ingest import already_handled_unchanged

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
    inhalt = "Rechnung 42"

    erste = tmp_path / "original.txt"
    erste.write_text(inhalt)
    _ingest(engine, erste)

    kopie = tmp_path / "kopie.txt"
    kopie.write_text(inhalt)
    engine.ingest_file(kopie)

    assert already_handled_unchanged(engine.store, kopie) is True

    engine.ingest_file(kopie)  # zweiter Scan
    duplikate = [i for i in engine.store.recent(limit=20) if i["status"] == "duplicate"]
    assert len(duplikate) == 1


def test_undo_ueberspringt_duplikate(tmp_path: Path) -> None:
    """Ein Duplikat wurde nie bewegt. Stuende es vorn, liefe undo ins Leere."""
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
    inhalt = "Rechnung 42"
    erste = tmp_path / "original.txt"
    erste.write_text(inhalt)
    _ingest(engine, erste)
    kopie = tmp_path / "kopie.txt"
    kopie.write_text(inhalt)
    engine.ingest_file(kopie)

    letzter = engine.store.get_last_undoable()

    assert letzter is not None
    assert letzter["status"] == "routed"
    assert letzter["destination"]


def test_duplikat_erscheint_nicht_in_der_pruefliste(tmp_path: Path) -> None:
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
    inhalt = "Rechnung 42"
    for name in ("a.txt", "b.txt"):
        p = tmp_path / name
        p.write_text(inhalt)
        _ingest(engine, p)

    assert engine.store.low_confidence(threshold=0.6) == []


def test_duplikat_erhoeht_die_dokumentzahl_nicht(tmp_path: Path) -> None:
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
    inhalt = "Rechnung 42"
    erste = tmp_path / "a.txt"
    erste.write_text(inhalt)
    _ingest(engine, erste)
    vorher = engine.store.stats()["total_items"]

    kopie = tmp_path / "b.txt"
    kopie.write_text(inhalt)
    engine.ingest_file(kopie)

    assert engine.store.stats()["total_items"] == vorher


def test_ohne_lesbaren_text_landet_das_dokument_in_der_prueferliste(tmp_path: Path) -> None:
    """Ein Bild ohne erkennbaren Text darf nicht nach Dateiname einsortiert werden.

    Vorher meldete Kurier 90 % Sicherheit, obwohl das Modell nur den Dateinamen
    gesehen hatte — ein Minijob-Vertrag namens "RechnungDienstleistungen.pdf"
    landete so bei den Rechnungen (2026-08-09).
    """
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
    source = tmp_path / "RechnungDienstleistungen.pdf"
    source.write_bytes(b"%PDF-1.4 kein extrahierbarer Text")

    with (
        patch.object(engine.classifier, "classify", return_value=_classification()),
        patch.object(engine, "_generate_embedding", return_value=None),
        patch("arkiv.core.ocr.extract_text", return_value=""),
    ):
        engine.ingest_file(source)

    item = engine.store.get_recent(limit=1)[0]
    assert item["confidence"] <= 0.2
    assert "kein Text lesen" in item["summary"]
    assert item["route_name"] == "__review__"


def test_echter_text_bleibt_unangetastet(tmp_path: Path) -> None:
    """Gegenprobe: Bei lesbarem Inhalt bleibt die gemeldete Sicherheit erhalten."""
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
    source = tmp_path / "rechnung.txt"
    source.write_text("Rechnung über 42 EUR von den Stadtwerken")

    _ingest(engine, source)

    item = engine.store.get_recent(limit=1)[0]
    assert item["confidence"] == 0.9
    assert "kein Text lesen" not in (item["summary"] or "")


def test_post_hook_kann_die_deckelung_nicht_aufheben(tmp_path: Path) -> None:
    """Ein Plugin darf eine Namens-Vermutung nicht zur sicheren Ablage machen.

    post_classify darf die Klassifikation absichtlich veraendern. Wuerde die
    Deckelung vor dem Hook greifen, koennte ein Plugin die Sicherheit wieder
    hochsetzen und die Datei landete trotz fehlendem Text in einem Ordner
    (Cross-Model-Review 2026-08-09, P2).
    """

    class ConfidencePlugin:
        @hookimpl
        def post_classify(self, classification: Classification, path: str) -> None:
            classification.confidence = 0.99

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
    engine.plugin_manager.register(ConfidencePlugin(), name="confidence")
    source = tmp_path / "RechnungDienstleistungen.pdf"
    source.write_bytes(b"%PDF-1.4 kein extrahierbarer Text")

    with (
        patch.object(engine.classifier, "classify", return_value=_classification()),
        patch.object(engine, "_generate_embedding", return_value=None),
        patch("arkiv.core.ocr.extract_text", return_value=""),
    ):
        engine.ingest_file(source)

    item = engine.store.get_recent(limit=1)[0]
    assert item["confidence"] <= 0.2
    assert item["route_name"] == "__review__"


def test_inhalt_mit_klammer_am_anfang_gilt_als_echter_text(tmp_path: Path) -> None:
    """Gegenprobe zum frueheren Marker-im-Text: Solcher Inhalt ist echt.

    Der Notbehelf wird ueber einen eigenen Rueckgabewert gemeldet, nicht ueber
    eine Zeichenfolge im Text — sonst wuerde ein Dokument, das zufaellig so
    beginnt, faelschlich als unlesbar gelten (Cross-Model-Review 2026-08-09, P2).
    """
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
    source = tmp_path / "notiz.txt"
    source.write_text("[kein-lesbarer-text] Rechnung über 42 EUR von den Stadtwerken")

    _ingest(engine, source)

    item = engine.store.get_recent(limit=1)[0]
    assert item["confidence"] == 0.9
    assert item["route_name"] == "archiv"
    assert "[kein-lesbarer-text]" in (item["content_text"] or "")
