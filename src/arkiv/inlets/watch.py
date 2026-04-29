"""Filesystem watcher inlet — monitors a directory for new and existing files."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Semaphore

from watchdog.events import DirCreatedEvent, FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


def _should_skip_path(path: Path) -> bool:
    """Ignore hidden and temporary files in the inbox."""
    return path.name.startswith(".") or path.name.endswith(".tmp")


def list_inbox_files(inbox_dir: Path) -> list[Path]:
    """Return visible inbox files in a stable order."""
    if not inbox_dir.exists():
        return []

    return sorted(
        (path for path in inbox_dir.iterdir() if path.is_file() and not _should_skip_path(path)),
        key=lambda path: path.name.casefold(),
    )


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

    def process_path(
        self,
        path: Path,
        *,
        use_cooldown: bool = True,
        source_label: str = "New file detected",
    ) -> None:
        """Process one inbox file with the same safeguards as live events."""
        if _should_skip_path(path):
            return

        src_str = str(path)

        if use_cooldown:
            now = time.time()
            last_seen = self._seen.get(src_str, 0)
            if now - last_seen < self.cooldown:
                return
            self._seen[src_str] = now

        logger.info("%s: %s", source_label, path.name)

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

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        if event.is_directory:
            return

        src = event.src_path
        src_str = src.decode() if isinstance(src, bytes) else src
        path = Path(src_str)
        self.process_path(path)


class Watcher:
    """Watches the inbox directory and triggers processing."""

    def __init__(
        self,
        inbox_dir: Path,
        callback: Callable[[Path], None],
        max_concurrent: int = 3,
        llm_provider: str = "ollama",
        drain_existing: bool = False,
    ) -> None:
        self.inbox_dir = inbox_dir
        self.observer = Observer()
        self._semaphore = Semaphore(max_concurrent)
        self.handler = InboxHandler(callback, semaphore=self._semaphore)
        self._stop_event = Event()
        self._llm_provider = llm_provider
        self._drain_existing = drain_existing

    def _drain_existing_files(self) -> int:
        """Process files that already exist before the watcher starts."""
        existing_files = list_inbox_files(self.inbox_dir)
        if not existing_files:
            return 0

        logger.info(
            "Processing %d existing file(s) in %s before watch starts.",
            len(existing_files),
            self.inbox_dir,
        )
        for path in existing_files:
            self.handler.process_path(path, use_cooldown=False, source_label="Existing file found")
        return len(existing_files)

    def _wait_for_ollama(self) -> None:
        """Poll Ollama until reachable. Blocks with 30s intervals."""
        import urllib.request

        url = "http://localhost:11434/api/tags"
        while not self._stop_event.is_set():
            try:
                urllib.request.urlopen(url, timeout=5)
                logger.info("Ollama is ready.")
                return
            except Exception:
                logger.warning("Waiting for Ollama (%s)...", url)
                self._stop_event.wait(timeout=30)

    def start(self) -> None:
        """Start watching. Blocks until stop() is called."""
        if self._llm_provider == "ollama":
            self._wait_for_ollama()
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        if self._drain_existing:
            drained = self._drain_existing_files()
            if drained:
                logger.info("Existing inbox drained: %d file(s) processed.", drained)
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
