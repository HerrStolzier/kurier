"""Filesystem watcher inlet — monitors a directory for new and existing files."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Semaphore

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
        # Pfade, die gerade verarbeitet werden. Startscan (Main-Thread) und
        # Event-Dispatch (Observer-Thread) koennen dieselbe Datei gleichzeitig
        # anfassen — das Lock macht Pruefen-und-Vormerken atomar
        # (Cross-Model-Review 2026-08-06, P1).
        self._in_flight: set[str] = set()
        self._lock = Lock()
        # Callbacks laufen strikt nacheinander. Vor dem Startscan-Umbau war das
        # implizit so (ein einziger Event-Dispatch-Thread); seit der Scan
        # parallel zum Beobachter laeuft, koennten sonst zwei Callbacks
        # gleichzeitig auf demselben Engine/SQLite-Handle arbeiten — und z.B.
        # die Notification des einen die Daten des anderen anzeigen
        # (Cross-Model-Review 2026-08-06, P2).
        self._callback_lock = Lock()
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
    ) -> bool:
        """Process one inbox file with the same safeguards as live events.

        True, wenn die Datei wirklich verarbeitet wurde."""
        if _should_skip_path(path):
            return False

        src_str = str(path)

        with self._lock:
            if src_str in self._in_flight:
                return False
            if _signature(path) == self._processed.get(src_str):
                # Unveraendert seit der letzten Verarbeitung — z.B. eine Datei,
                # die nach einer Webhook-only-Route im Eingang bleibt und
                # spaeter noch einmal gemeldet wird (Startscan oder ein
                # verspaetetes Created-Event nach Ablauf des Cooldowns).
                return False
            if use_cooldown:
                now = time.time()
                last_seen = self._seen.get(src_str, 0)
                if now - last_seen < self.cooldown:
                    return False
                self._seen[src_str] = now
            self._in_flight.add(src_str)

        try:
            while True:
                # Nicht auf halbfertigen Dateien arbeiten: warten, bis die
                # Groesse stabil ist. Bei Timeout Cooldown-Eintrag freigeben,
                # damit ein spaeteres Modify-Event einen frischen Versuch
                # bekommt.
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
                    return False

                self._retry_pending.discard(src_str)
                logger.info("%s: %s", source_label, path.name)

                if self._semaphore is not None:
                    logger.debug("Waiting for processing slot...")
                    self._semaphore.acquire()

                try:
                    with self._callback_lock:
                        # Baseline erst NACH dem Lock festhalten: waehrend des
                        # Wartens kann die Datei weitergeschrieben worden sein,
                        # und der Callback liest den Stand von jetzt. Mit einer
                        # aelteren Baseline wuerde der Nachfass-Vergleich unten
                        # denselben Inhalt gleich noch einmal verarbeiten
                        # (Cross-Model-Review 2026-08-06, P2). Und vor dem
                        # Callback, nicht danach: Folder-Routen verschieben die
                        # Datei weg, danach gibt es nichts mehr zu messen.
                        current = _signature(path)
                        if current is not None:
                            self._processed[src_str] = current
                        self.callback(path)
                except Exception as e:
                    logger.error("Error processing %s: %s", path.name, e)
                finally:
                    if self._semaphore is not None:
                        self._semaphore.release()

                # Endvergleich und Freigabe der In-Flight-Sperre atomar: laege
                # dazwischen ein Fenster, wuerde ein genau dort eintreffendes
                # Modify-Event verworfen (Pfad noch "busy"), ohne dass der
                # Vergleich den neuen Stand noch saehe
                # (Cross-Model-Review 2026-08-06, P1).
                with self._lock:
                    if not path.exists():
                        # Folder-Routen verschieben die Datei weg. Fuer einen
                        # Pfad, den es nicht mehr gibt, kann es keinen
                        # Nachschreibe-Fall geben — und ein Move-/Delete-Event,
                        # das spaeter aufraeumen wuerde, behandelt dieser
                        # Handler nicht. Ohne das waechst `_processed` bei einem
                        # lang laufenden Watcher um jede jemals einsortierte
                        # Datei (Cross-Model-Review 2026-08-04, P2).
                        self._processed.pop(src_str, None)
                        self._in_flight.discard(src_str)
                        return True
                    if _signature(path) == self._processed.get(src_str):
                        self._in_flight.discard(src_str)
                        return True
                # Waehrend der Verarbeitung wurde weitergeschrieben. Die
                # Modify-Events dazu hat die In-Flight-Sperre verworfen, ein
                # weiteres Event kommt womoeglich nie — deshalb hier selbst
                # nachfassen statt auf eines zu warten
                # (Cross-Model-Review 2026-08-06, P1).
                source_label = "Changed during processing, reprocessing"
        finally:
            with self._lock:
                self._in_flight.discard(src_str)

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
            # Unbekannter Pfad — eigentlich der Fall fuer on_created. Aber
            # macOS/fsevents liefert fuer geklonte Dateien (Finder-Kopie, cp
            # auf APFS) NUR Modified-Events, nie Created — live beobachtet am
            # 2026-08-06 (is_cloned-Flag im fsevents-Log). Wer hier auf
            # on_created wartet, verpasst solche Dateien bis zum naechsten
            # Neustart. process_path dedupliziert selbst (In-Flight-Sperre,
            # Signaturvergleich, Cooldown), doppelte Events sind unschaedlich.
            self.process_path(path)
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
        drain_skip: Callable[[Path], bool] | None = None,
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
        # Persistente Skip-Pruefung fuer den Startscan: Dateien, die schon
        # frueher erfolgreich einsortiert wurden und seitdem unveraendert sind
        # (z.B. Webhook-only-Routen lassen die Datei im Eingang liegen), duerfen
        # nach einem Neustart nicht erneut verarbeitet werden — der In-Memory-
        # Merker des Handlers ueberlebt den Neustart nicht
        # (Cross-Model-Review 2026-08-06, P1).
        self._drain_skip = drain_skip

    def _drain_existing_files(self) -> int:
        """Process files that already exist before the watcher starts."""
        existing_files = list_inbox_files(self.inbox_dir)
        if not existing_files:
            return 0

        logger.info(
            "Checking %d existing file(s) in %s.",
            len(existing_files),
            self.inbox_dir,
        )
        drained = 0
        for path in existing_files:
            if self._drain_skip is not None and self._drain_skip(path):
                logger.debug("Already routed and unchanged, leaving alone: %s", path.name)
                continue
            # Doppelte Verarbeitung gegen Live-Events (der Beobachter laeuft
            # beim Drain bereits) verhindert process_path selbst: In-Flight-
            # Sperre plus Signaturvergleich.
            if self.handler.process_path(path, source_label="Existing file found"):
                drained += 1
        return drained

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
        # Erst beobachten, DANN den Backlog aufarbeiten. Umgekehrt fiele jede
        # Datei, die zwischen Snapshot und observer.start() ankommt, in eine
        # Luecke: sie ist weder im Backlog noch loest sie ein Event aus
        # (Cross-Model-Review 2026-08-06, P1).
        self.observer.schedule(self.handler, str(self.inbox_dir), recursive=False)
        self.observer.start()
        # Ab hier laeuft der Beobachter — ALLES Weitere gehoert in den
        # try-Block, damit Strg+C oder ein Fehler waehrend des Startscans den
        # Observer-Thread nicht verwaist zuruecklaesst
        # (Cross-Model-Review 2026-08-06, P2).
        try:
            if self._drain_existing:
                drained = self._drain_existing_files()
                if drained:
                    logger.info("Existing inbox drained: %d file(s) processed.", drained)
            logger.info("Watching %s for new files...", self.inbox_dir)

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
