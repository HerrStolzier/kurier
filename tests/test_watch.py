"""Tests for inbox watching and backlog draining."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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


def test_watcher_drains_existing_files_before_watch_loop(tmp_path: Path) -> None:
    """Backlog files should be processed once when drain_existing is enabled."""
    inbox = tmp_path / "Eingang"
    inbox.mkdir()
    existing = inbox / "Notiz.txt"
    existing.write_text("hello", encoding="utf-8")
    processed: list[Path] = []

    watcher = Watcher(
        inbox_dir=inbox,
        callback=lambda path: processed.append(path),
        llm_provider="openai",
        drain_existing=True,
    )
    watcher.handler._stability_interval = 0.05

    # Stop erst NACH dem Drain setzen (observer.start laeuft nach dem Drain),
    # sonst bricht das Stop-Event das Stabilitaets-Warten korrekt ab.
    with (
        patch.object(watcher.observer, "schedule"),
        patch.object(watcher.observer, "start", side_effect=watcher._stop_event.set),
        patch.object(watcher.observer, "stop"),
        patch.object(watcher.observer, "join"),
    ):
        watcher.start()

    assert processed == [existing]


def _handler(processed: list[Path], **kwargs) -> InboxHandler:
    defaults = {
        "stability_interval": 0.05,
        "stability_checks": 2,
        "stability_timeout": 2.0,
    }
    defaults.update(kwargs)
    return InboxHandler(lambda path: processed.append(path), cooldown=0.0, **defaults)


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

    stop_writing.set()
    t.join()
    handler.process_path(target)  # spaeteres (Modify-)Event: frischer Versuch

    assert processed == [target]


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
