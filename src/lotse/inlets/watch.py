"""Filesystem watcher inlet — monitors a directory for new files."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Event

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class InboxHandler(FileSystemEventHandler):
    """Handles new files appearing in the inbox directory."""

    def __init__(self, callback: callable, cooldown: float = 2.0) -> None:
        self.callback = callback
        self.cooldown = cooldown
        self._seen: dict[str, float] = {}

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return

        path = Path(event.src_path)

        # Skip hidden files and temp files
        if path.name.startswith(".") or path.name.endswith(".tmp"):
            return

        # Cooldown to avoid processing partial writes
        now = time.time()
        last_seen = self._seen.get(event.src_path, 0)
        if now - last_seen < self.cooldown:
            return
        self._seen[event.src_path] = now

        logger.info("New file detected: %s", path.name)
        try:
            self.callback(path)
        except Exception as e:
            logger.error("Error processing %s: %s", path.name, e)


class Watcher:
    """Watches the inbox directory and triggers processing."""

    def __init__(self, inbox_dir: Path, callback: callable) -> None:
        self.inbox_dir = inbox_dir
        self.observer = Observer()
        self.handler = InboxHandler(callback)
        self._stop_event = Event()

    def start(self) -> None:
        """Start watching. Blocks until stop() is called."""
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.observer.schedule(self.handler, str(self.inbox_dir), recursive=False)
        self.observer.start()
        logger.info("Watching %s for new files...", self.inbox_dir)

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.observer.stop()
            self.observer.join()

    def stop(self) -> None:
        """Signal the watcher to stop."""
        self._stop_event.set()
