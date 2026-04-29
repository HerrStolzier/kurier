"""Tests for inbox watching and backlog draining."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from arkiv.inlets.watch import Watcher, list_inbox_files


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
    watcher._stop_event.set()

    with (
        patch.object(watcher.observer, "schedule"),
        patch.object(watcher.observer, "start"),
        patch.object(watcher.observer, "stop"),
        patch.object(watcher.observer, "join"),
    ):
        watcher.start()

    assert processed == [existing]
