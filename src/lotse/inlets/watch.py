"""Filesystem watcher inlet — monitors a directory for new files."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Semaphore

from watchdog.events import DirCreatedEvent, FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class InboxHandler(FileSystemEventHandler):
    """Handles new files appearing in the inbox directory."""

    def __init__(
        self,
        callback: Callable[[Path], None],
        cooldown: float = 2.0,
        semaphore: Semaphore | None = None,
    ) -> None:
        self.callback = callback
        self.cooldown = cooldown
        self._seen: dict[str, float] = {}
        self._semaphore = semaphore

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        if event.is_directory:
            return

        src = event.src_path
        src_str = src.decode() if isinstance(src, bytes) else src
        path = Path(src_str)

        # Skip hidden files and temp files
        if path.name.startswith(".") or path.name.endswith(".tmp"):
            return

        # Cooldown to avoid processing partial writes
        now = time.time()
        last_seen = self._seen.get(src_str, 0)
        if now - last_seen < self.cooldown:
            return
        self._seen[src_str] = now

        logger.info("New file detected: %s", path.name)

        if self._semaphore is not None:
            logger.debug("Waiting for processing slot...")
            self._semaphore.acquire()

        try:
            self.callback(path)
        except Exception as e:
            logger.error("Error processing %s: %s", path.name, e)
        finally:
            if self._semaphore is not None:
                self._semaphore.release()


class Watcher:
    """Watches the inbox directory and triggers processing."""

    def __init__(
        self,
        inbox_dir: Path,
        callback: Callable[[Path], None],
        max_concurrent: int = 3,
    ) -> None:
        self.inbox_dir = inbox_dir
        self.observer = Observer()
        self._semaphore = Semaphore(max_concurrent)
        self.handler = InboxHandler(callback, semaphore=self._semaphore)
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
