"""Filesystem watcher inlet — monitors a directory for new and existing files."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Semaphore

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


def _should_skip_path(path: Path) -> bool:
    """Ignore hidden and temporary files in the inbox."""
    return path.name.startswith(".") or path.name.endswith(".tmp")


def _wait_until_stable(
    path: Path,
    *,
    interval: float = 0.5,
    stable_checks: int = 2,
    timeout: float = 60.0,
    stop_event: Event | None = None,
) -> bool:
    """Warten, bis die Datei aufgehoert hat zu wachsen.

    True, wenn die Groesse bei `stable_checks` aufeinanderfolgenden Messungen
    unveraendert war. False bei Timeout, verschwundener Datei oder gesetztem
    stop_event. Erkennt nur wachsende Dateien (Kopiervorgaenge) — in-place-
    Writes bei gleicher Groesse sieht die Heuristik nicht.
    """
    deadline = time.monotonic() + timeout
    last_size = -1
    stable = 0

    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size == last_size:
            stable += 1
            if stable >= stable_checks:
                return True
        else:
            stable = 0
            last_size = size
        if stop_event is not None:
            stop_event.wait(timeout=interval)
        else:
            time.sleep(interval)
    return False


def _signature(path: Path) -> tuple[int, int] | None:
    """Groesse und mtime — woran sich erkennen laesst, ob sich etwas geaendert hat.

    None, wenn die Datei nicht (mehr) lesbar ist. mtime gehoert dazu, weil ein
    In-place-Write bei gleicher Groesse sonst unsichtbar bliebe — genau der Fall,
    den auch `_wait_until_stable()` nicht erkennt.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


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
        stop_event: Event | None = None,
        stability_interval: float = 0.5,
        stability_checks: int = 2,
        stability_timeout: float = 60.0,
    ) -> None:
        self.callback = callback
        self.cooldown = cooldown
        self._seen: dict[str, float] = {}
        # Pfade, deren Stabilitaets-Wartezeit in den Timeout lief — fuer diese
        # ist jedes Modify-Event ein Retry-Signal.
        self._retry_pending: set[str] = set()
        # Stand, in dem ein Pfad zuletzt verarbeitet wurde. Ein Modify-Event
        # zaehlt nur, wenn sich davon etwas unterscheidet: Dateien, die nach der
        # Verarbeitung im Eingang liegen bleiben (Webhook-only-Routen), duerfen
        # durch aufgestaute Events nicht erneut verarbeitet werden — eine Datei,
        # an der wirklich weitergeschrieben wurde, dagegen schon.
        self._processed: dict[str, tuple[int, int]] = {}
        self._semaphore = semaphore
        self._stop_event = stop_event
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks
        self._stability_timeout = stability_timeout

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

        # Nicht auf halbfertigen Dateien arbeiten: warten, bis die Groesse
        # stabil ist. Bei Timeout Cooldown-Eintrag freigeben, damit ein
        # spaeteres Modify-Event einen frischen Versuch bekommt.
        if not _wait_until_stable(
            path,
            interval=self._stability_interval,
            stable_checks=self._stability_checks,
            timeout=self._stability_timeout,
            stop_event=self._stop_event,
        ):
            logger.warning("File not stable yet, skipping for now: %s", path.name)
            self._seen.pop(src_str, None)
            if path.exists():
                self._retry_pending.add(src_str)
            return

        self._retry_pending.discard(src_str)
        # Vor dem Callback merken: Folder-Routen verschieben die Datei weg, danach
        # gibt es nichts mehr zu messen.
        current = _signature(path)
        if current is not None:
            self._processed[src_str] = current

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
            # Folder-Routen verschieben die Datei weg. Fuer einen Pfad, den es
            # nicht mehr gibt, kann es keinen Nachschreibe-Fall geben — und ein
            # Move-/Delete-Event, das spaeter aufraeumen wuerde, behandelt dieser
            # Handler nicht. Ohne das waechst `_processed` bei einem lang
            # laufenden Watcher um jede jemals einsortierte Datei
            # (Cross-Model-Review 2026-08-04, P2).
            if not path.exists():
                self._processed.pop(src_str, None)

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent) -> None:
        if event.is_directory:
            return
        self.process_path(self._event_path(event))

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        # Ein Modify-Event zaehlt in zwei Faellen: die Stabilitaets-Wartezeit lief
        # in den Timeout (Retry), oder die Datei sieht anders aus als bei der
        # letzten Verarbeitung (es wurde wirklich weitergeschrieben). Alles andere
        # wird ignoriert, sonst verarbeiten aufgestaute Events dieselbe Datei
        # mehrfach — sichtbar bei Webhook-only-Routen, wo sie im Eingang bleibt.
        #
        # Die reine Merker-Variante (nur Retry zaehlt) hatte eine Luecke: pausiert
        # der Erzeuger laenger als das Stabilitaetsfenster, gilt der Torso als
        # fertig, und das Nachschreiben loeste nie eine erneute Verarbeitung aus
        # (Cross-Model-Review 2026-08-04, P2).
        if event.is_directory:
            return
        path = self._event_path(event)
        src_str = str(path)

        if src_str in self._retry_pending:
            self.process_path(path)
            return

        last = self._processed.get(src_str)
        if last is None:
            # Nie verarbeitet — dafuer ist on_created bzw. der Startscan da.
            return
        current = _signature(path)
        if current is None:
            self._forget(src_str)  # weg (z.B. von einer Folder-Route verschoben)
            return
        if current == last:
            return
        # Ohne Cooldown: der Stabilitaets-Wait bremst die Event-Flut bereits, und
        # der Signaturvergleich verhindert doppelte Arbeit. Mit Cooldown ginge
        # genau der Nachschreibe-Fall wieder verloren, wenn der Erzeuger
        # innerhalb des Cooldown-Fensters fertig wird.
        self.process_path(path, use_cooldown=False)

    def on_deleted(self, event: DirDeletedEvent | FileDeletedEvent) -> None:
        # Ein Pfad, den es nicht mehr gibt, braucht keine Merker. Ohne diesen
        # Zweig behielte ein Dienst, der monatelang laeuft, je einen Eintrag fuer
        # jede Datei, die nach der Verarbeitung im Eingang lag und spaeter von
        # Hand geloescht wurde (Cross-Model-Review 2026-08-04, P2).
        if event.is_directory:
            return
        self._forget(str(self._event_path(event)))

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        # Wie on_deleted, nur dass der Pfad woandershin gewandert ist. `src_path`
        # ist der alte Name — nur der wird vergessen. Das Ziel meldet sich
        # gegebenenfalls selbst ueber ein Created-Event.
        if event.is_directory:
            return
        self._forget(str(self._event_path(event)))

    def _forget(self, src_str: str) -> None:
        """Alle Merker zu einem Pfad loeschen."""
        self._processed.pop(src_str, None)
        self._seen.pop(src_str, None)
        self._retry_pending.discard(src_str)

    @staticmethod
    def _event_path(event: FileSystemEvent) -> Path:
        src = event.src_path
        return Path(src.decode() if isinstance(src, bytes) else src)


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
        self._stop_event = Event()
        self.handler = InboxHandler(
            callback, semaphore=self._semaphore, stop_event=self._stop_event
        )
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
