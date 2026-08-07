"""Tests for inbox watching and backlog draining."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from watchdog.events import FileDeletedEvent, FileModifiedEvent, FileMovedEvent

from arkiv.inlets.watch import InboxHandler, Watcher, list_inbox_files


def test_list_inbox_files_skips_hidden_and_temp(tmp_path: Path) -> None:
    """Only real, visible inbox files should be returned."""
    inbox = tmp_path / "Eingang"
    inbox.mkdir()
    (inbox / ".DS_Store").write_text("ignore", encoding="utf-8")
    (inbox / "partial.tmp").write_text("ignore", encoding="utf-8")
    wanted = inbox / "Rechnung.pdf"
    wanted.write_text("keep", encoding="utf-8")

    assert list_inbox_files(inbox) == [wanted]


def test_watcher_drains_existing_files_after_observer_start(tmp_path: Path) -> None:
    """Backlog-Dateien werden genau einmal verarbeitet — und zwar NACHDEM der
    Beobachter laeuft. Umgekehrt fiele eine Datei, die zwischen Snapshot und
    observer.start() ankommt, in eine Luecke ohne Event und ohne Backlog-Eintrag
    (Cross-Model-Review 2026-08-06, P1)."""
    inbox = tmp_path / "Eingang"
    inbox.mkdir()
    existing = inbox / "Notiz.txt"
    existing.write_text("hello", encoding="utf-8")
    order: list[str] = []

    def callback(path: Path) -> None:
        order.append(f"processed:{path.name}")
        # Nach dem Drain die Watch-Schleife beenden, sonst blockiert start().
        watcher._stop_event.set()

    watcher = Watcher(
        inbox_dir=inbox,
        callback=callback,
        llm_provider="openai",
        drain_existing=True,
    )
    watcher.handler._stability_interval = 0.05

    with (
        patch.object(watcher.observer, "schedule"),
        patch.object(watcher.observer, "start", side_effect=lambda: order.append("observing")),
        patch.object(watcher.observer, "stop"),
        patch.object(watcher.observer, "join"),
    ):
        watcher.start()

    assert order == ["observing", "processed:Notiz.txt"]


def test_drain_skips_file_already_handled_by_live_event(tmp_path: Path) -> None:
    """Weil der Beobachter beim Drain schon laeuft, kann eine Snapshot-Datei
    bereits per Live-Event verarbeitet worden sein. Unveraenderter Stand darf
    nicht doppelt verarbeitet werden."""
    from arkiv.inlets.watch import _signature

    inbox = tmp_path / "Eingang"
    inbox.mkdir()
    handled = inbox / "schon-da.txt"
    handled.write_text("data", encoding="utf-8")
    processed: list[Path] = []

    watcher = Watcher(
        inbox_dir=inbox,
        callback=lambda path: processed.append(path),
        llm_provider="openai",
        drain_existing=True,
    )
    watcher.handler._stability_interval = 0.05
    signature = _signature(handled)
    assert signature is not None
    watcher.handler._processed[str(handled)] = signature

    assert watcher._drain_existing_files() == 0
    assert processed == []


def test_drain_skip_predicate_leaves_persisted_files_alone(tmp_path: Path) -> None:
    """Webhook-only-Dateien bleiben im Eingang. Der Startscan darf sie nach
    einem Neustart nicht erneut verarbeiten — der In-Memory-Merker ist dann weg,
    also entscheidet die von aussen gereichte Skip-Pruefung
    (Cross-Model-Review 2026-08-06, P1)."""
    inbox = tmp_path / "Eingang"
    inbox.mkdir()
    (inbox / "webhook-bleibt.txt").write_text("data", encoding="utf-8")
    processed: list[Path] = []
    asked: list[Path] = []

    def skip(path: Path) -> bool:
        asked.append(path)
        return True

    watcher = Watcher(
        inbox_dir=inbox,
        callback=lambda path: processed.append(path),
        llm_provider="openai",
        drain_existing=True,
        drain_skip=skip,
    )
    watcher.handler._stability_interval = 0.05

    assert watcher._drain_existing_files() == 0
    assert processed == []
    assert asked == [inbox / "webhook-bleibt.txt"]


def test_late_created_event_skips_unchanged_processed_file(tmp_path: Path) -> None:
    """Ein Created-Event, das erst nach Ablauf des Cooldowns eintrifft (z.B.
    weil es hinter einer langsamen Verarbeitung im Event-Thread wartete), darf
    eine unveraenderte, schon verarbeitete Datei nicht erneut anfassen
    (Cross-Model-Review 2026-08-06, P1)."""
    from watchdog.events import FileCreatedEvent

    target = tmp_path / "stays.pdf"
    target.write_text("data")

    processed: list[Path] = []
    handler = _handler(processed)  # cooldown=0.0 — nur der Signaturvergleich schuetzt

    handler.process_path(target, use_cooldown=False)
    assert processed == [target]

    handler.on_created(FileCreatedEvent(str(target)))
    assert processed == [target]

    # Nach echtem Weiterschreiben zaehlt das Event wieder.
    with target.open("a", encoding="utf-8") as f:
        f.write(" mehr")
    handler.on_created(FileCreatedEvent(str(target)))
    assert processed == [target, target]


def test_write_during_callback_triggers_reprocessing(tmp_path: Path) -> None:
    """Wird waehrend der Verarbeitung weitergeschrieben, verwirft die In-Flight-
    Sperre die Modify-Events dazu — und ein weiteres Event kommt womoeglich nie.
    process_path muss deshalb selbst nachfassen
    (Cross-Model-Review 2026-08-06, P1)."""
    from arkiv.inlets.watch import _signature

    target = tmp_path / "nachzuegler.pdf"
    target.write_text("torso")
    processed: list[Path] = []

    def callback(path: Path) -> None:
        processed.append(path)
        if len(processed) == 1:
            with path.open("a", encoding="utf-8") as f:
                f.write(" rest")

    handler = InboxHandler(callback, cooldown=0.0, stability_interval=0.05, stability_checks=2)
    handler.process_path(target, use_cooldown=False)

    assert processed == [target, target]  # zweite Runde auf dem vollen Inhalt
    assert handler._processed[str(target)] == _signature(target)


def test_modified_event_for_unknown_file_is_processed(tmp_path: Path) -> None:
    """macOS/fsevents meldet fuer geklonte Dateien (Finder-Kopie, cp auf APFS)
    nur Modified-Events, nie Created. Solche Dateien muessen trotzdem
    verarbeitet werden — sonst liegen sie bis zum naechsten Neustart im Eingang
    (live beobachtet 2026-08-06)."""
    target = tmp_path / "geklont.pdf"
    target.write_text("data")

    processed: list[Path] = []
    handler = _handler(processed)

    handler.on_modified(FileModifiedEvent(str(target)))
    assert processed == [target]

    # Nachfolgende Events fuer den unveraenderten Stand bleiben folgenlos.
    handler.on_modified(FileModifiedEvent(str(target)))
    assert processed == [target]


def test_callbacks_from_two_threads_never_overlap(tmp_path: Path) -> None:
    """Startscan (Main-Thread) und Event-Dispatch (Observer-Thread) laufen
    parallel — die Callbacks selbst muessen trotzdem strikt nacheinander
    laufen, sonst teilen sich zwei Ingests denselben Engine/SQLite-Handle
    (Cross-Model-Review 2026-08-06, P2)."""
    import threading
    import time as _time

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")

    active = 0
    max_active = 0
    counter_lock = threading.Lock()

    def callback(_path: Path) -> None:
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        _time.sleep(0.15)
        with counter_lock:
            active -= 1

    processed: list[Path] = []

    def tracking_callback(p: Path) -> None:
        callback(p)
        processed.append(p)

    handler = InboxHandler(
        tracking_callback,
        cooldown=0.0,
        stability_interval=0.05,
        stability_checks=2,
    )

    t1 = threading.Thread(target=lambda: handler.process_path(a, use_cooldown=False))
    t2 = threading.Thread(target=lambda: handler.process_path(b, use_cooldown=False))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(processed) == [a, b]
    assert max_active == 1


def test_file_changed_while_waiting_for_callback_slot_runs_once(tmp_path: Path) -> None:
    """Wird eine Datei geaendert, WAEHREND sie auf den Callback-Lock wartet,
    liest ihr Callback bereits den neuen Stand. Die Baseline muss deshalb erst
    nach dem Lock entstehen — sonst verarbeitet der Nachfass-Vergleich
    denselben Inhalt gleich doppelt (Cross-Model-Review 2026-08-06, P2)."""
    import threading
    import time as _time

    blocker = tmp_path / "blocker.txt"
    queued = tmp_path / "queued.txt"
    blocker.write_text("a")
    queued.write_text("torso")

    calls: list[str] = []

    def callback(path: Path) -> None:
        calls.append(path.name)
        if path == blocker:
            _time.sleep(0.6)  # haelt den Callback-Lock, queued muss warten

    handler = InboxHandler(callback, cooldown=0.0, stability_interval=0.05, stability_checks=2)

    t1 = threading.Thread(target=lambda: handler.process_path(blocker, use_cooldown=False))
    t2 = threading.Thread(target=lambda: handler.process_path(queued, use_cooldown=False))
    t1.start()
    _time.sleep(0.25)  # blocker ist durch den Stabilitaets-Wait und im Callback
    t2.start()
    _time.sleep(0.25)  # queued haengt jetzt am Callback-Lock
    with queued.open("a", encoding="utf-8") as f:
        f.write(" rest")  # Aenderung, waehrend queued wartet
    t1.join()
    t2.join()

    assert calls.count("queued.txt") == 1  # neuer Stand, aber nur EINMAL


def test_observer_is_cleaned_up_when_drain_is_interrupted(tmp_path: Path) -> None:
    """Strg+C waehrend des Startscans darf den Observer-Thread nicht verwaist
    zuruecklassen (Cross-Model-Review 2026-08-06, P2)."""
    inbox = tmp_path / "Eingang"
    inbox.mkdir()
    (inbox / "Notiz.txt").write_text("hello", encoding="utf-8")

    def interrupt(_path: Path) -> None:
        raise KeyboardInterrupt

    watcher = Watcher(
        inbox_dir=inbox,
        callback=interrupt,
        llm_provider="openai",
        drain_existing=True,
    )
    watcher.handler._stability_interval = 0.05

    with (
        patch.object(watcher.observer, "schedule"),
        patch.object(watcher.observer, "start"),
        patch.object(watcher.observer, "stop") as observer_stop,
        patch.object(watcher.observer, "join") as observer_join,
    ):
        watcher.start()

    observer_stop.assert_called_once()
    observer_join.assert_called_once()


def test_cli_watch_enables_backlog_drain(tmp_path: Path) -> None:
    """`kurier watch` (auch vom Hintergrunddienst genutzt) muss Alt-Dateien im
    Eingang beim Start verarbeiten — sonst bleiben Dateien liegen, die vor dem
    Watcher-Start dort ankamen."""
    from unittest.mock import MagicMock

    from arkiv.commands.ingest import watch

    ctx = MagicMock()
    ctx.config.inbox_dir = tmp_path
    ctx.config.llm.provider = "openai"

    import typer
    from typer.testing import CliRunner

    app = typer.Typer()
    app.command()(watch)
    runner = CliRunner()

    # Ueber den ECHTEN CLI-Weg (nicht als Python-Funktion), damit die
    # Typer-Defaults aufgeloest werden. Beide Varianten muessen den Startscan
    # einschalten: der Default UND der explizite Dienst-Aufruf aus service.py.
    for args in ([], ["--drain-existing"]):
        with (
            patch("arkiv.commands.ingest.get_context", return_value=ctx),
            patch("arkiv.inlets.watch.Watcher") as watcher_cls,
        ):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert watcher_cls.call_args.kwargs["drain_existing"] is True
        # Persistente Skip-Pruefung muss mitverdrahtet sein, sonst verarbeitet
        # jeder Dienst-Neustart die Webhook-only-Dateien im Eingang erneut.
        assert callable(watcher_cls.call_args.kwargs["drain_skip"])
        watcher_cls.return_value.start.assert_called_once()


def _handler(processed: list[Path], **kwargs) -> InboxHandler:
    defaults = {
        "cooldown": 0.0,
        "stability_interval": 0.05,
        "stability_checks": 2,
        "stability_timeout": 2.0,
    }
    defaults.update(kwargs)
    return InboxHandler(lambda path: processed.append(path), **defaults)


def test_growing_file_is_processed_only_when_complete(tmp_path: Path) -> None:
    import threading
    import time as _time

    target = tmp_path / "big.pdf"
    target.write_text("start")
    done = threading.Event()

    def writer() -> None:
        for _ in range(4):
            with target.open("a") as f:
                f.write("x" * 100)
            _time.sleep(0.08)
        done.set()

    processed: list[Path] = []
    handler = _handler(processed)
    t = threading.Thread(target=writer)
    t.start()
    handler.process_path(target, use_cooldown=False)
    t.join()

    assert processed == [target]
    assert done.is_set()  # Callback kam erst nach Schreibende
    assert target.read_text() == "start" + "x" * 400


def test_vanished_file_is_skipped_without_error(tmp_path: Path) -> None:
    import threading
    import time as _time

    target = tmp_path / "flaky.pdf"
    target.write_text("data")

    def deleter() -> None:
        _time.sleep(0.07)
        target.unlink()

    processed: list[Path] = []
    # Nie stabil innerhalb des Fensters: erst waechst nichts, dann weg
    handler = _handler(processed, stability_checks=50)
    t = threading.Thread(target=deleter)
    t.start()
    handler.process_path(target, use_cooldown=False)
    t.join()

    assert processed == []


def test_timeout_clears_cooldown_for_later_retry(tmp_path: Path) -> None:
    import threading
    import time as _time

    target = tmp_path / "slow.pdf"
    target.write_text("start")
    stop_writing = threading.Event()

    def writer() -> None:
        while not stop_writing.is_set():
            with target.open("a") as f:
                f.write("x")
            _time.sleep(0.03)

    processed: list[Path] = []
    handler = _handler(processed, stability_timeout=0.3)
    t = threading.Thread(target=writer)
    t.start()

    handler.process_path(target)  # laeuft in den Timeout
    assert processed == []
    assert str(target) not in handler._seen  # Cooldown freigegeben
    assert str(target) in handler._retry_pending  # Retry vorgemerkt

    stop_writing.set()
    t.join()
    # Spaeteres Modify-Event: frischer Versuch, danach Retry-Merker geleert
    handler.on_modified(FileModifiedEvent(str(target)))

    assert processed == [target]
    assert str(target) not in handler._retry_pending


def test_modified_event_ignored_for_already_processed_file(tmp_path: Path) -> None:
    """Webhook-only-Dateien bleiben im Eingang — Modify-Events duerfen sie
    nicht erneut verarbeiten."""
    target = tmp_path / "stays.pdf"
    target.write_text("data")

    processed: list[Path] = []
    handler = _handler(processed)

    handler.process_path(target, use_cooldown=False)
    assert processed == [target]

    handler.on_modified(FileModifiedEvent(str(target)))
    handler.on_modified(FileModifiedEvent(str(target)))

    assert processed == [target]  # kein Duplikat


def test_modified_event_reprocesses_file_that_grew_after_processing(tmp_path: Path) -> None:
    """Pausiert der Erzeuger laenger als das Stabilitaetsfenster, gilt der Torso
    als fertig. Wird danach weitergeschrieben, muss die fertige Fassung noch
    verarbeitet werden (Cross-Model-Review 2026-08-04, P2)."""
    target = tmp_path / "resumed.pdf"
    target.write_text("torso")

    processed: list[Path] = []
    handler = _handler(processed)

    handler.process_path(target, use_cooldown=False)
    assert processed == [target]

    with target.open("a", encoding="utf-8") as f:
        f.write(" rest")

    handler.on_modified(FileModifiedEvent(str(target)))

    assert processed == [target, target]  # zweite Runde auf dem vollen Inhalt

    # Danach ist wieder Ruhe: unveraenderte Datei, kein weiterer Durchlauf.
    handler.on_modified(FileModifiedEvent(str(target)))
    assert processed == [target, target]


def test_regrown_file_is_reprocessed_inside_the_cooldown_window(tmp_path: Path) -> None:
    """Der Cooldown darf den Nachschreibe-Fall nicht schlucken: der Erzeuger kann
    innerhalb des Cooldown-Fensters fertig werden, und dann kommt kein Event mehr."""
    target = tmp_path / "quick.pdf"
    target.write_text("torso")

    processed: list[Path] = []
    handler = _handler(processed, cooldown=60.0)

    handler.process_path(target)
    assert processed == [target]

    with target.open("a", encoding="utf-8") as f:
        f.write(" rest")

    handler.on_modified(FileModifiedEvent(str(target)))

    assert processed == [target, target]


def test_routed_away_file_leaves_no_signature_behind(tmp_path: Path) -> None:
    """Folder-Routen verschieben die Datei weg. Der Merker darf nicht auf ein
    spaeteres Event warten, das nie kommt — sonst waechst er unbegrenzt."""
    target = tmp_path / "invoice.pdf"
    target.write_text("data")
    archive = tmp_path / "Archiv"
    archive.mkdir()

    moved: list[Path] = []

    def route(path: Path) -> None:
        path.rename(archive / path.name)
        moved.append(path)

    handler = InboxHandler(route, cooldown=0.0, stability_interval=0.05, stability_checks=2)
    handler.process_path(target, use_cooldown=False)

    assert moved == [target]
    assert handler._processed == {}


def test_delete_and_move_events_clear_the_markers(tmp_path: Path) -> None:
    """Webhook-only-Dateien bleiben im Eingang liegen. Werden sie spaeter von
    Hand geloescht oder weggeschoben, meldet watchdog ein Delete- bzw.
    Move-Event — kein Modify. Ohne diese Handler blieben die Merker fuer die
    ganze Laufzeit des Dienstes stehen."""
    processed: list[Path] = []
    handler = _handler(processed)

    deleted = tmp_path / "geloescht.pdf"
    deleted.write_text("data")
    handler.process_path(deleted, use_cooldown=False)

    moved = tmp_path / "verschoben.pdf"
    moved.write_text("data")
    handler.process_path(moved, use_cooldown=False)

    assert set(handler._processed) == {str(deleted), str(moved)}

    handler.on_deleted(FileDeletedEvent(str(deleted)))
    handler.on_moved(FileMovedEvent(str(moved), str(tmp_path / "woanders.pdf")))

    assert handler._processed == {}
    assert handler._seen == {}
    assert handler._retry_pending == set()


def test_modified_event_forgets_file_that_disappeared(tmp_path: Path) -> None:
    """Folder-Routen verschieben die Datei weg — der Merker darf nicht bleiben."""
    target = tmp_path / "moved.pdf"
    target.write_text("data")

    processed: list[Path] = []
    handler = _handler(processed)

    handler.process_path(target, use_cooldown=False)
    assert str(target) in handler._processed

    target.unlink()
    handler.on_modified(FileModifiedEvent(str(target)))

    assert processed == [target]
    assert str(target) not in handler._processed


def test_stop_event_aborts_stability_wait(tmp_path: Path) -> None:
    from threading import Event

    target = tmp_path / "doc.pdf"
    target.write_text("data")
    stop = Event()
    stop.set()

    processed: list[Path] = []
    handler = InboxHandler(
        lambda path: processed.append(path),
        cooldown=0.0,
        stop_event=stop,
        stability_interval=0.05,
        stability_timeout=5.0,
    )
    handler.process_path(target, use_cooldown=False)

    assert processed == []
