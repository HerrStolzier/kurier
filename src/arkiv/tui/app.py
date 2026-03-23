"""Kurier TUI — Interaktive Terminal-Oberfläche (Iteration 4)."""

from __future__ import annotations

import contextlib
import platform
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

LOGO = """
██╗  ██╗██╗   ██╗██████╗ ██╗███████╗██████╗
██║ ██╔╝██║   ██║██╔══██╗██║██╔════╝██╔══██╗
█████╔╝ ██║   ██║██████╔╝██║█████╗  ██████╔╝
██╔═██╗ ██║   ██║██╔══██╗██║██╔══╝  ██╔══██╗
██║  ██╗╚██████╔╝██║  ██║██║███████╗██║  ██║
╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝╚══════╝╚═╝  ╚═╝
"""

MENU_ITEMS: list[tuple[str, str]] = [
    ("1", "Datei hinzufügen"),
    ("2", "Inbox überwachen"),
    ("3", "Suchen"),
    ("4", "Status & Statistiken"),
    ("5", "Letzte Einträge"),
    ("6", "Audit"),
    ("7", "Einstellungen prüfen (Doctor)"),
]

SEARCH_MODES = ["hybrid", "keyword", "semantic"]
SEARCH_MODE_MAP = {
    "hybrid": "auto",
    "keyword": "fts",
    "semantic": "vec",
}

# Status-Farben für die Tabellen
STATUS_COLORS = {
    "routed": "green",
    "pending": "yellow",
    "failed": "red",
    "undone": "dim",
}


def _truncate(text: str | None, max_len: int = 40) -> str:
    """Text auf max_len Zeichen kürzen."""
    if not text:
        return "—"
    text = str(text)
    return text[: max_len - 1] + "…" if len(text) > max_len else text


def _color_status(status: str | None) -> str:
    """Status-String mit Farbe versehen."""
    s = status or "unknown"
    color = STATUS_COLORS.get(s, "white")
    return f"[{color}]{s}[/{color}]"


# ---------------------------------------------------------------------------
# Detail-Modal
# ---------------------------------------------------------------------------


class DetailModal(ModalScreen[None]):
    """Zeigt alle Felder eines Suchergebnisses als Popup."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Schließen", show=True),
        Binding("q", "dismiss", "Schließen", show=False),
    ]

    def __init__(self, item: dict[str, Any]) -> None:
        super().__init__()
        self._item = item

    def compose(self) -> ComposeResult:
        lines: list[str] = []
        field_labels = {
            "id": "ID",
            "category": "Kategorie",
            "confidence": "Confidence",
            "summary": "Zusammenfassung",
            "original_path": "Originalpfad",
            "destination": "Ziel",
            "route_name": "Route",
            "status": "Status",
            "tags": "Tags",
            "language": "Sprache",
            "created_at": "Erstellt",
        }
        for key, label in field_labels.items():
            value = self._item.get(key)
            if value is not None and value != "":
                if key == "confidence" and isinstance(value, float):
                    value = f"{value:.2f}"
                lines.append(f"[bold #f5a623]{label}:[/bold #f5a623] {value}")

        content = "\n".join(lines) if lines else "Keine Details verfügbar."
        yield Static(
            f"[bold]Detail-Ansicht[/bold]\n\n{content}\n\n[dim]ESC zum Schließen[/dim]",
            id="detail-content",
        )

    def on_click(self) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# Undo-Bestätigungs-Modal
# ---------------------------------------------------------------------------


class UndoConfirmModal(ModalScreen[bool]):
    """Bestätigungsdialog für Undo-Aktion."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "confirm_yes", "Ja", show=True),
        Binding("n", "confirm_no", "Nein", show=True),
        Binding("escape", "confirm_no", "Abbrechen", show=True),
    ]

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold]Rückgängig machen[/bold]\n\n"
            f"Soll [bold #f5a623]{self._filename}[/bold #f5a623] "
            f"rückgängig gemacht werden?\n\n"
            f"[green][j][/green] Ja    [red][n][/red] Nein / ESC Abbrechen",
            id="undo-confirm-content",
        )

    def action_confirm_yes(self) -> None:
        self.dismiss(True)

    def action_confirm_no(self) -> None:
        self.dismiss(False)

    def on_click(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Stats-Modal
# ---------------------------------------------------------------------------


class StatsModal(ModalScreen[None]):
    """Zeigt Statistiken als Popup."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Schließen", show=True),
        Binding("q", "dismiss", "Schließen", show=False),
    ]

    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold]Status & Statistiken[/bold]\n\n{self._content}\n\n[dim]ESC zum Schließen[/dim]",
            id="stats-content",
        )

    def on_click(self) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# SearchScreen
# ---------------------------------------------------------------------------


class SearchScreen(Screen[None]):
    """Suchmaske mit DataTable und Echtzeit-Ergebnissen."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_home", "Zurück", show=True),
        Binding("tab", "toggle_mode", "Modus wechseln", show=True),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config
        self._mode_index: int = 0  # Index in SEARCH_MODES
        self._debounce_timer: object | None = None
        self._last_results: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        mode_label = SEARCH_MODES[self._mode_index].capitalize()
        yield Static(
            f"[bold #f5a623]Kurier — Suche[/bold #f5a623]  [dim]Modus: {mode_label}[/dim]",
            id="search-header",
        )
        yield Input(
            placeholder="Suchbegriff eingeben...",
            id="search-input",
        )
        yield Static("Suchbegriff eingeben...", id="search-empty")
        yield DataTable(id="search-results", show_cursor=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#search-results", DataTable)
        table.add_columns(
            "Kategorie",
            "Confidence",
            "Zusammenfassung",
            "Pfad",
            "Datum",
        )
        # Fokus auf das Eingabefeld
        self.query_one("#search-input", Input).focus()

    # ------------------------------------------------------------------
    # Eingabe / Debounce
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        """Bei jeder Eingabe: Debounce-Timer neu starten."""
        if self._debounce_timer is not None:
            self._debounce_timer.stop()  # type: ignore[union-attr]
        query = event.value.strip()
        if not query:
            self._show_empty_state()
            return
        self._debounce_timer = self.set_timer(0.3, lambda: self._trigger_search(query))

    def _trigger_search(self, query: str) -> None:
        """Suche im Hintergrund-Thread starten."""
        thread = threading.Thread(target=self._run_search, args=(query,), daemon=True)
        thread.start()

    def _run_search(self, query: str) -> None:
        """Engine.search() im Hintergrund-Thread ausführen."""
        if self._config is None:
            self.call_from_thread(self._show_error, "Keine Konfiguration geladen.")
            return
        try:
            from arkiv.core.engine import Engine

            cfg = self._config  # type: ignore[assignment]
            engine = Engine(cfg)
            mode = SEARCH_MODE_MAP[SEARCH_MODES[self._mode_index]]
            results = engine.search(query, limit=50, mode=mode)
            self.call_from_thread(self._populate_table, results)
        except Exception as exc:
            self.call_from_thread(self._show_error, f"Fehler: {exc}")

    # ------------------------------------------------------------------
    # Tabelle befüllen
    # ------------------------------------------------------------------

    def _populate_table(self, results: list[dict[str, Any]]) -> None:
        """Suchergebnisse in DataTable eintragen."""
        self._last_results = results
        table = self.query_one("#search-results", DataTable)
        table.clear()

        empty = self.query_one("#search-empty", Static)

        if not results:
            empty.update("Keine Ergebnisse gefunden.")
            empty.display = True
            table.display = False
            return

        empty.display = False
        table.display = True

        for row in results:
            confidence = row.get("confidence", 0.0)
            conf_str = f"{confidence:.2f}" if isinstance(confidence, float) else str(confidence)
            date_raw = str(row.get("created_at", ""))
            date_short = date_raw[:10] if date_raw else "—"
            table.add_row(
                _truncate(row.get("category"), 20),
                conf_str,
                _truncate(row.get("summary"), 45),
                _truncate(row.get("original_path"), 40),
                date_short,
            )

    def _show_empty_state(self) -> None:
        """Leerzustand anzeigen."""
        self._last_results = []
        table = self.query_one("#search-results", DataTable)
        table.clear()
        table.display = False
        empty = self.query_one("#search-empty", Static)
        empty.update("Suchbegriff eingeben...")
        empty.display = True

    def _show_error(self, message: str) -> None:
        """Fehlermeldung in der leeren State-Anzeige zeigen."""
        table = self.query_one("#search-results", DataTable)
        table.display = False
        empty = self.query_one("#search-empty", Static)
        empty.update(f"[red]{message}[/red]")
        empty.display = True

    # ------------------------------------------------------------------
    # Zeilen-Auswahl → Detail-Modal
    # ------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter auf einer Zeile → Detail-Popup."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._last_results):
            item = self._last_results[row_index]
            self.app.push_screen(DetailModal(item))

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def action_go_home(self) -> None:
        self.app.pop_screen()

    def action_toggle_mode(self) -> None:
        """Suchmodus wechseln: hybrid → keyword → semantic → hybrid."""
        self._mode_index = (self._mode_index + 1) % len(SEARCH_MODES)
        mode_label = SEARCH_MODES[self._mode_index].capitalize()
        header = self.query_one("#search-header", Static)
        header.update(
            f"[bold #f5a623]Kurier — Suche[/bold #f5a623]  [dim]Modus: {mode_label}[/dim]"
        )
        # Aktuelle Suche erneut ausführen
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._trigger_search(query)


# ---------------------------------------------------------------------------
# RecentScreen
# ---------------------------------------------------------------------------


class RecentScreen(Screen[None]):
    """Letzte 50 Einträge mit Undo-Funktion."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_home", "Zurück", show=True),
        Binding("u", "undo_selected", "Undo", show=True),
        Binding("r", "reload", "Neu laden", show=True),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config
        self._items: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #f5a623]Kurier — Letzte Einträge[/bold #f5a623]  "
            "[dim]u=Undo  Enter=Detail  r=Neu laden  ESC=Zurück[/dim]",
            id="recent-header",
        )
        yield Static("Lade Einträge...", id="recent-empty")
        yield DataTable(id="recent-table", show_cursor=True)
        yield Static("", id="recent-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#recent-table", DataTable)
        table.add_columns("#", "Kategorie", "Confidence", "Pfad", "Status", "Datum")
        table.display = False
        self._load_items()

    # ------------------------------------------------------------------
    # Laden (Hintergrund-Thread)
    # ------------------------------------------------------------------

    def _load_items(self) -> None:
        thread = threading.Thread(target=self._run_load, daemon=True)
        thread.start()

    def _run_load(self) -> None:
        if self._config is None:
            self.call_from_thread(self._show_error, "Keine Konfiguration geladen.")
            return
        try:
            from arkiv.db.store import Store

            cfg = self._config  # type: ignore[assignment]
            if not cfg.database.path.exists():  # type: ignore[union-attr]
                self.call_from_thread(self._show_error, "Datenbank leer — noch keine Einträge.")
                return
            store = Store(cfg.database.path)  # type: ignore[union-attr]
            items = store.get_recent(limit=50)
            self.call_from_thread(self._populate_table, items)
        except Exception as exc:
            self.call_from_thread(self._show_error, f"Fehler beim Laden: {exc}")

    # ------------------------------------------------------------------
    # Tabelle befüllen
    # ------------------------------------------------------------------

    def _populate_table(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        table = self.query_one("#recent-table", DataTable)
        empty = self.query_one("#recent-empty", Static)
        table.clear()

        if not items:
            empty.update("Noch keine Einträge vorhanden.")
            empty.display = True
            table.display = False
            return

        empty.display = False
        table.display = True

        for i, row in enumerate(items, 1):
            confidence = row.get("confidence", 0.0)
            conf_str = f"{confidence:.2f}" if isinstance(confidence, float) else str(confidence)
            date_raw = str(row.get("created_at", ""))
            date_short = date_raw[:10] if date_raw else "—"
            status = row.get("status") or "unknown"
            table.add_row(
                str(i),
                _truncate(row.get("category"), 20),
                conf_str,
                _truncate(row.get("original_path"), 45),
                _color_status(status),
                date_short,
            )

    def _show_error(self, message: str) -> None:
        empty = self.query_one("#recent-empty", Static)
        empty.update(f"[red]{message}[/red]")
        empty.display = True
        table = self.query_one("#recent-table", DataTable)
        table.display = False

    def _set_status(self, message: str) -> None:
        try:
            bar = self.query_one("#recent-status", Static)
            bar.update(message)
        except NoMatches:
            pass

    # ------------------------------------------------------------------
    # Zeilen-Auswahl → Detail-Modal
    # ------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter auf einer Zeile → Detail-Popup."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._items):
            item = self._items[row_index]
            self.app.push_screen(DetailModal(item))

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def action_undo_selected(self) -> None:
        """u-Taste: Undo-Bestätigung für ausgewählte Zeile."""
        try:
            table = self.query_one("#recent-table", DataTable)
        except NoMatches:
            return

        row_index = table.cursor_row
        if not (0 <= row_index < len(self._items)):
            self._set_status("[yellow]Keine Zeile ausgewählt.[/yellow]")
            return

        item = self._items[row_index]
        if item.get("status") == "undone":
            self._set_status("[dim]Dieser Eintrag wurde bereits rückgängig gemacht.[/dim]")
            return

        filename = Path(item.get("destination") or item.get("original_path") or "?").name

        def _after_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._do_undo(item)

        self.app.push_screen(UndoConfirmModal(filename), _after_confirm)

    def _do_undo(self, item: dict[str, Any]) -> None:
        """Undo im Hintergrund-Thread ausführen."""
        thread = threading.Thread(target=self._run_undo, args=(item,), daemon=True)
        thread.start()

    def _run_undo(self, item: dict[str, Any]) -> None:
        """Datei zurückbewegen und DB-Status aktualisieren."""
        item_id = item.get("id")
        destination = item.get("destination")
        original_path = item.get("original_path")

        if not destination or not original_path:
            self.call_from_thread(
                self._set_status,
                "[red]Fehler: Pfadinformationen fehlen.[/red]",
            )
            return

        dest_path = Path(destination)
        orig_path = Path(original_path)

        # Datei am Zielort prüfen
        if not dest_path.exists():
            self.call_from_thread(
                self._set_status,
                "[red]Datei nicht mehr vorhanden.[/red]",
            )
            return

        # Originalpfad bereits belegt?
        if orig_path.exists():
            self.call_from_thread(
                self._set_status,
                "[red]Originalpfad bereits belegt.[/red]",
            )
            return

        try:
            # Zielverzeichnis für Originalpfad anlegen falls nötig
            orig_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest_path), str(orig_path))

            # DB-Status aktualisieren
            if self._config is not None:
                from arkiv.db.store import Store

                cfg = self._config  # type: ignore[assignment]
                store = Store(cfg.database.path)  # type: ignore[union-attr]
                store.update_status(item_id, "undone")

            self.call_from_thread(self._undo_success, item_id)
        except Exception as exc:
            self.call_from_thread(
                self._set_status,
                f"[red]Fehler beim Zurückbewegen: {exc}[/red]",
            )

    def _undo_success(self, item_id: int | None) -> None:
        """Nach erfolgreichem Undo: Status aktualisieren und Tabelle neu laden."""
        # Lokales Item-dict sofort aktualisieren
        for it in self._items:
            if it.get("id") == item_id:
                it["status"] = "undone"
                break
        self._set_status("[green]Datei erfolgreich zurückbewegt.[/green]")
        # Tabelle neu befüllen mit aktualisierten Daten
        self._populate_table(self._items)

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def action_go_home(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self._set_status("[dim]Lade neu...[/dim]")
        self._load_items()


# ---------------------------------------------------------------------------
# WatchScreen
# ---------------------------------------------------------------------------


class WatchScreen(Screen[None]):
    """Live-Überwachung des Inbox-Verzeichnisses mit Echtzeit-Log."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("s", "toggle_watch", "Start/Stop", show=True),
        Binding("escape", "go_home", "Zurück", show=True),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config
        self._watcher: Any = None
        self._watch_thread: threading.Thread | None = None
        self._running: bool = False
        self._processed: int = 0
        self._errors: int = 0
        self._start_time: datetime | None = None
        self._tick_timer: Any = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        inbox_path = self._get_inbox_path()
        yield Static(
            f"[bold #f5a623]Inbox überwachen: {inbox_path}[/bold #f5a623]  "
            "[dim]s=Start/Stop  ESC=Zurück[/dim]",
            id="watch-header",
        )
        yield RichLog(id="watch-log", auto_scroll=True, markup=True, wrap=True)
        yield Static(
            "Bereit. [dim]Drücke [bold]s[/bold] zum Starten.[/dim]",
            id="watch-stats",
        )
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#watch-log", RichLog)
        inbox_path = self._get_inbox_path()
        log.write(f"[dim]Überwache {inbox_path}... (s = starten, ESC = zurück)[/dim]")

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _get_inbox_path(self) -> str:
        if self._config is not None:
            try:
                cfg = self._config  # type: ignore[assignment]
                return str(cfg.inbox_dir)  # type: ignore[union-attr]
            except AttributeError:
                pass
        return "~/inbox"

    def _get_inbox_dir(self) -> Path:
        if self._config is not None:
            try:
                cfg = self._config  # type: ignore[assignment]
                return Path(cfg.inbox_dir)  # type: ignore[union-attr]
            except AttributeError:
                pass
        return Path.home() / "inbox"

    def _now_str(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    # ------------------------------------------------------------------
    # Watcher-Steuerung
    # ------------------------------------------------------------------

    def action_toggle_watch(self) -> None:
        """s-Taste: Überwachung starten oder stoppen."""
        if self._running:
            self._stop_watch()
        else:
            self._start_watch()

    def _start_watch(self) -> None:
        if self._config is None:
            log = self.query_one("#watch-log", RichLog)
            log.write(f"[{self._now_str()}] [red]✗ Fehler: Keine Konfiguration geladen.[/red]")
            return

        self._running = True
        self._processed = 0
        self._errors = 0
        self._start_time = datetime.now()

        log = self.query_one("#watch-log", RichLog)
        inbox_path = self._get_inbox_path()
        log.write(f"[{self._now_str()}] [green]▶ Starte Überwachung: {inbox_path}[/green]")

        self._tick_timer = self.set_interval(1.0, self._tick)

        self._watch_thread = threading.Thread(target=self._run_watcher, daemon=True)
        self._watch_thread.start()

    def _stop_watch(self) -> None:
        self._running = False

        if self._watcher is not None:
            with contextlib.suppress(Exception):
                self._watcher.stop()
            self._watcher = None

        if self._tick_timer is not None:
            with contextlib.suppress(Exception):
                self._tick_timer.stop()
            self._tick_timer = None

        log = self.query_one("#watch-log", RichLog)
        log.write(f"[{self._now_str()}] [yellow]■ Überwachung gestoppt.[/yellow]")
        self._update_stats()

    def _run_watcher(self) -> None:
        """Watcher im Hintergrund-Thread starten (blockierend)."""
        try:
            from arkiv.inlets.watch import Watcher

            inbox_dir = self._get_inbox_dir()
            self._watcher = Watcher(
                inbox_dir=inbox_dir,
                callback=self._watch_callback,
                max_concurrent=3,
            )
            self._watcher.start()
        except Exception as exc:
            self.call_from_thread(self._log_error_msg, f"Watcher-Fehler: {exc}")

    # ------------------------------------------------------------------
    # Callback aus dem Watcher-Thread
    # ------------------------------------------------------------------

    def _watch_callback(self, file_path: Path) -> None:
        """Vom Watcher für jede neue Datei aufgerufen."""
        filename = file_path.name
        self.call_from_thread(self._log_new_file, str(file_path), filename)
        try:
            from arkiv.core.engine import Engine

            cfg = self._config  # type: ignore[assignment]
            engine = Engine(cfg)
            result = engine.ingest_file(file_path)
            self._processed += 1
            self.call_from_thread(self._log_success, filename, result)
        except Exception as exc:
            self._errors += 1
            self.call_from_thread(self._log_error, filename, str(exc))
        self.call_from_thread(self._update_stats)

    # ------------------------------------------------------------------
    # Log-Methoden (werden im TUI-Thread aufgerufen via call_from_thread)
    # ------------------------------------------------------------------

    def _log_new_file(self, _file_path: str, filename: str) -> None:
        log = self.query_one("#watch-log", RichLog)
        log.write(f"[{self._now_str()}] Neue Datei: [bold]{filename}[/bold]")

    def _log_success(self, filename: str, result: Any) -> None:
        log = self.query_one("#watch-log", RichLog)
        category = getattr(result, "route_name", "?")
        destination = getattr(result, "destination", "")
        # Confidence aus dem Result-Objekt ist nicht direkt vorhanden — Route-Name reicht
        dest_short = Path(destination).parent.name if destination else "?"
        log.write(
            f"[{self._now_str()}] [green]✓[/green] "
            f"→ Kategorie: [cyan]{category}[/cyan] "
            f"→ [dim]{dest_short}/[/dim]"
        )

    def _log_error(self, filename: str, error: str) -> None:
        log = self.query_one("#watch-log", RichLog)
        log.write(f"[{self._now_str()}] [red]✗ Fehler ({filename}): {error}[/red]")

    def _log_error_msg(self, error: str) -> None:
        log = self.query_one("#watch-log", RichLog)
        log.write(f"[{self._now_str()}] [red]✗ Fehler: {error}[/red]")

    # ------------------------------------------------------------------
    # Stats-Ticker
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Jede Sekunde die Laufzeit aktualisieren."""
        self._update_stats()

    def _update_stats(self) -> None:
        try:
            bar = self.query_one("#watch-stats", Static)
        except NoMatches:
            return

        elapsed = ""
        if self._start_time is not None:
            delta = datetime.now() - self._start_time
            minutes = int(delta.total_seconds() // 60)
            seconds = int(delta.total_seconds() % 60)
            elapsed = f"  |  Laufzeit: {minutes}m {seconds:02d}s"

        status = "[green]läuft[/green]" if self._running else "[dim]gestoppt[/dim]"
        bar.update(
            f"Status: {status}  |  "
            f"Verarbeitet: [bold]{self._processed}[/bold]  |  "
            f"Fehler: [bold red]{self._errors}[/bold red]"
            f"{elapsed}"
        )

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def action_go_home(self) -> None:
        if self._running:
            self._stop_watch()
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# AddFileModal
# ---------------------------------------------------------------------------


class AddFileModal(ModalScreen[None]):
    """Einfaches Modal zum Hinzufügen einer Datei."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Schließen", show=True),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]Datei hinzufügen[/bold]\n\nPfad zur Datei eingeben:",
            id="add-file-title",
        )
        yield Input(placeholder="/Pfad/zur/Datei.pdf", id="add-file-input")
        yield Button("Verarbeiten", id="add-file-btn", variant="primary")
        yield Static("", id="add-file-status")

    def on_mount(self) -> None:
        self.query_one("#add-file-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-file-btn":
            self._process_file()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._process_file()

    def _process_file(self) -> None:
        path_str = self.query_one("#add-file-input", Input).value.strip()
        if not path_str:
            self._set_status("[yellow]Bitte Pfad eingeben.[/yellow]")
            return
        self._set_status("[dim]Verarbeite...[/dim]")
        btn = self.query_one("#add-file-btn", Button)
        btn.disabled = True
        thread = threading.Thread(target=self._run_process, args=(path_str,), daemon=True)
        thread.start()

    def _run_process(self, path_str: str) -> None:
        if self._config is None:
            self.call_from_thread(self._set_status, "[red]Keine Konfiguration geladen.[/red]")
            self.call_from_thread(self._re_enable_btn)
            return
        file_path = Path(path_str).expanduser()
        if not file_path.exists():
            self.call_from_thread(
                self._set_status,
                f"[red]Datei nicht gefunden:[/red] {path_str}",
            )
            self.call_from_thread(self._re_enable_btn)
            return
        try:
            from arkiv.core.engine import Engine

            cfg = self._config  # type: ignore[assignment]
            engine = Engine(cfg)
            result = engine.ingest_file(file_path)
            if result.success:
                self.call_from_thread(
                    self._set_status,
                    f"[green]✓ {result.message}[/green]",
                )
            else:
                self.call_from_thread(
                    self._set_status,
                    f"[red]✗ {result.message}[/red]",
                )
        except Exception as exc:
            self.call_from_thread(self._set_status, f"[red]Fehler: {exc}[/red]")
        self.call_from_thread(self._re_enable_btn)

    def _set_status(self, text: str) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#add-file-status", Static).update(text)

    def _re_enable_btn(self) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#add-file-btn", Button).disabled = False

    def on_click(self, event: object) -> None:
        # Nur außerhalb des Inhalts schließen
        pass


# ---------------------------------------------------------------------------
# DoctorModal
# ---------------------------------------------------------------------------


class DoctorModal(ModalScreen[None]):
    """Zeigt Doctor-Ergebnisse als Modal."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Schließen", show=True),
        Binding("q", "dismiss", "Schließen", show=False),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]Einstellungen prüfen (Doctor)[/bold]", id="doctor-title")
        yield Static("[dim]Prüfe...[/dim]", id="doctor-content")
        yield Static("[dim]ESC zum Schließen[/dim]", id="doctor-footer")

    def on_mount(self) -> None:
        thread = threading.Thread(target=self._run_checks, daemon=True)
        thread.start()

    def _run_checks(self) -> None:
        import json
        import tomllib
        import urllib.request

        from arkiv.core.config import DEFAULT_CONFIG_FILE, ArkivConfig

        config_path = DEFAULT_CONFIG_FILE

        rows: list[tuple[str, str, str]] = []

        def ok(label: str, detail: str = "") -> None:
            rows.append(("[green]✓[/green]", label, detail))

        def warn(label: str, detail: str = "") -> None:
            rows.append(("[yellow]![/yellow]", label, detail))

        def fail(label: str, detail: str = "") -> None:
            rows.append(("[red]✗[/red]", label, detail))

        # Check 1: Config-Datei
        cfg_valid = False
        cfg = None
        if config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    tomllib.load(f)
                ok("Config-Datei", str(config_path))
                cfg_valid = True
            except Exception as e:
                fail("Config-Datei", f"Ungültiges TOML: {e}")
        else:
            fail("Config-Datei", f"Nicht gefunden: {config_path}")

        if cfg_valid:
            try:
                cfg = ArkivConfig.load(config_path)
            except Exception as e:
                fail("Config laden", str(e))
                cfg_valid = False

        # Check 2: Route-Verzeichnisse
        if cfg is not None:
            from pathlib import Path as _Path

            for name, route in cfg.routes.items():
                if route.path:
                    rp = _Path(route.path).expanduser()
                    if rp.exists():
                        ok(f"Route '{name}'", str(rp))
                    else:
                        warn(f"Route '{name}'", f"Verzeichnis fehlt: {rp}")

        # Check 3: LLM erreichbar
        if cfg is not None:
            if cfg.llm.provider == "ollama":
                ollama_url = (cfg.llm.base_url or "http://localhost:11434").rstrip("/")
                try:
                    with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3) as resp:
                        data = json.loads(resp.read())
                        models = [m["name"] for m in data.get("models", [])]
                    model_names_base = [m.split(":")[0] for m in models]
                    if cfg.llm.model in models or cfg.llm.model in model_names_base:
                        ok("LLM erreichbar", f"{cfg.llm.provider}/{cfg.llm.model}")
                    else:
                        available = ", ".join(models[:3]) or "-"
                        warn(
                            "LLM-Modell fehlt",
                            f"'{cfg.llm.model}' nicht gefunden. Verfügbar: {available}",
                        )
                except Exception as e:
                    fail("LLM erreichbar", f"Ollama nicht erreichbar: {str(e)[:60]}")
            else:
                ok("LLM", f"{cfg.llm.provider} (API-Key via Env-Var)")

        # Check 4: DB-Status
        if cfg is not None and cfg.database.path.exists():
            try:
                from arkiv.db.store import Store

                store = Store(cfg.database.path)
                all_items = store.get_all_items()
                pending = sum(1 for it in all_items if it.get("status") == "pending")
                failed_count = sum(1 for it in all_items if it.get("status") == "failed")
                if pending or failed_count:
                    warn(
                        "DB-Status",
                        f"{pending} ausstehend, {failed_count} fehlgeschlagen"
                        f" ({len(all_items)} gesamt)",
                    )
                else:
                    ok("DB-Status", f"{len(all_items)} Einträge, keine Fehler")
            except Exception as e:
                warn("Datenbank", str(e)[:80])
        elif cfg is not None:
            ok("Datenbank", "Noch leer (kein Element verarbeitet)")

        self.call_from_thread(self._render_results, rows)

    def _render_results(self, rows: list[tuple[str, str, str]]) -> None:
        lines = []
        for status, label, detail in rows:
            line = f"{status}  [bold]{label}[/bold]"
            if detail:
                line += f"  [dim]{detail}[/dim]"
            lines.append(line)
        content = "\n".join(lines) if lines else "[dim]Keine Prüfungen verfügbar.[/dim]"
        with contextlib.suppress(NoMatches):
            self.query_one("#doctor-content", Static).update(content)

    def on_click(self) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# AuditScreen
# ---------------------------------------------------------------------------

# Schweregrad-Farben
SEVERITY_COLORS: dict[str, str] = {
    "high": "red",
    "medium": "yellow",
    "low": "dim",
}

# Deutsche Typ-Namen
ISSUE_TYPE_LABELS: dict[str, str] = {
    "duplicate": "Duplikat",
    "misclassified": "Falsch klassifiziert",
    "low_confidence": "Niedrige Confidence",
    "orphaned": "Verwaist",
    "missing": "Fehlt",
}


class AuditDetailModal(ModalScreen[None]):
    """Detail-Ansicht für ein einzelnes Audit-Issue."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Schließen", show=True),
        Binding("q", "dismiss", "Schließen", show=False),
    ]

    def __init__(self, issue: Any) -> None:
        super().__init__()
        self._issue = issue

    def compose(self) -> ComposeResult:
        severity = getattr(self._issue, "severity", "?")
        issue_type = getattr(self._issue, "issue_type", "?")
        message = getattr(self._issue, "message", "")
        suggested = getattr(self._issue, "suggested_action", "")
        item_id = getattr(self._issue, "item_id", None)
        related_id = getattr(self._issue, "related_id", None)

        color = SEVERITY_COLORS.get(severity, "white")
        type_label = ISSUE_TYPE_LABELS.get(issue_type, issue_type)

        lines = [
            "[bold]Audit-Detail[/bold]\n",
            f"[bold #f5a623]Typ:[/bold #f5a623] {type_label}",
            f"[bold #f5a623]Schwere:[/bold #f5a623] [{color}]{severity}[/{color}]",
            f"[bold #f5a623]Beschreibung:[/bold #f5a623] {message}",
        ]
        if suggested:
            lines.append(f"[bold #f5a623]Empfohlene Aktion:[/bold #f5a623] {suggested}")
        if item_id is not None:
            lines.append(f"[bold #f5a623]Item-ID:[/bold #f5a623] {item_id}")
        if related_id is not None:
            lines.append(f"[bold #f5a623]Verwandtes Item:[/bold #f5a623] {related_id}")
        lines.append("\n[dim]ESC zum Schließen[/dim]")

        yield Static("\n".join(lines), id="audit-detail-content")

    def on_click(self) -> None:
        self.dismiss()


class AuditScreen(Screen[None]):
    """Zeigt Audit-Ergebnisse in einer DataTable."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_home", "Zurück", show=True),
        Binding("r", "reload", "Neu laden", show=True),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config
        self._issues: list[Any] = []

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #f5a623]Kurier — Audit[/bold #f5a623]  "
            "[dim]Enter=Detail  r=Neu laden  ESC=Zurück[/dim]",
            id="audit-header",
        )
        yield Static("[dim]Audit läuft...[/dim]", id="audit-empty")
        yield DataTable(id="audit-table", show_cursor=True)
        yield Static("", id="audit-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#audit-table", DataTable)
        table.add_columns("Schwere", "Typ", "Beschreibung", "Empfohlene Aktion")
        table.display = False
        self._run_audit()

    def _run_audit(self) -> None:
        thread = threading.Thread(target=self._audit_thread, daemon=True)
        thread.start()

    def _audit_thread(self) -> None:
        if self._config is None:
            self.call_from_thread(self._show_error, "Keine Konfiguration geladen.")
            return
        try:
            cfg = self._config  # type: ignore[assignment]
            if not cfg.database.path.exists():  # type: ignore[union-attr]
                self.call_from_thread(
                    self._show_empty, "Datenbank leer — noch keine Einträge zum Prüfen."
                )
                return
            from arkiv.core.auditor import Auditor

            auditor = Auditor(cfg)
            report = auditor.run_full_audit(check_misclassified=False)
            self.call_from_thread(self._populate_table, report)
        except Exception as exc:
            self.call_from_thread(self._show_error, f"Audit-Fehler: {exc}")

    def _populate_table(self, report: Any) -> None:
        self._issues = list(getattr(report, "issues", []))
        table = self.query_one("#audit-table", DataTable)
        empty = self.query_one("#audit-empty", Static)
        table.clear()

        if not self._issues:
            empty.update("[green]Keine Probleme gefunden ✓[/green]")
            empty.display = True
            table.display = False
            self._set_status(
                f"[dim]Geprüft: {report.items_checked} Einträge — alles in Ordnung.[/dim]"
            )
            return

        empty.display = False
        table.display = True

        for issue in self._issues:
            severity = getattr(issue, "severity", "?")
            issue_type = getattr(issue, "issue_type", "?")
            message = getattr(issue, "message", "")
            suggested = getattr(issue, "suggested_action", "")

            color = SEVERITY_COLORS.get(severity, "white")
            type_label = ISSUE_TYPE_LABELS.get(issue_type, issue_type)

            table.add_row(
                f"[{color}]{severity}[/{color}]",
                type_label,
                _truncate(message, 55),
                _truncate(suggested, 35),
            )

        high = sum(1 for i in self._issues if getattr(i, "severity", "") == "high")
        medium = sum(1 for i in self._issues if getattr(i, "severity", "") == "medium")
        low = sum(1 for i in self._issues if getattr(i, "severity", "") == "low")
        parts = []
        if high:
            parts.append(f"[red]{high} hoch[/red]")
        if medium:
            parts.append(f"[yellow]{medium} mittel[/yellow]")
        if low:
            parts.append(f"[dim]{low} niedrig[/dim]")
        summary = "  |  ".join(parts) if parts else ""
        self._set_status(
            f"[dim]Geprüft: {report.items_checked} Einträge  |  "
            f"Probleme: {len(self._issues)}  |  {summary}[/dim]"
        )

    def _show_error(self, message: str) -> None:
        empty = self.query_one("#audit-empty", Static)
        empty.update(f"[red]{message}[/red]")
        empty.display = True
        self.query_one("#audit-table", DataTable).display = False

    def _show_empty(self, message: str) -> None:
        empty = self.query_one("#audit-empty", Static)
        empty.update(f"[dim]{message}[/dim]")
        empty.display = True
        self.query_one("#audit-table", DataTable).display = False

    def _set_status(self, text: str) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#audit-status", Static).update(text)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if 0 <= row_index < len(self._issues):
            issue = self._issues[row_index]
            self.app.push_screen(AuditDetailModal(issue))

    def action_go_home(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self._issues = []
        table = self.query_one("#audit-table", DataTable)
        table.clear()
        table.display = False
        empty = self.query_one("#audit-empty", Static)
        empty.update("[dim]Audit läuft...[/dim]")
        empty.display = True
        self._set_status("")
        self._run_audit()


# ---------------------------------------------------------------------------
# SetupWizardScreen
# ---------------------------------------------------------------------------

# Kopiert aus setup_wizard.py — Modell-Hinweise für die TUI-Auswahl
_WIZARD_MODEL_HINTS: dict[str, tuple[str, bool]] = {
    "qwen2.5:7b": ("Schnell & genau — empfohlen für Kurier", True),
    "qwen2.5:3b": ("Leichtgewicht, gut für ältere Rechner", False),
    "qwen2.5:1.5b": ("Minimal, kann ungenau klassifizieren", False),
    "qwen3.5": ("Langsam (Denkpause ~100s pro Datei)", False),
    "llama3.1:8b": ("Solide Alternative zu Qwen", False),
    "llama3.1": ("Solide Alternative zu Qwen", False),
    "mistral": ("Gut für englische Texte", False),
    "gemma": ("Google-Modell, mittlere Qualität", False),
    "phi": ("Microsoft, klein aber fähig", False),
    "nomic-embed": ("Nur für Embeddings — nicht geeignet", False),
    "minimax": ("Cloud-Modell, nicht lokal", False),
}

_WIZARD_RECOMMENDED = "qwen2.5:7b"


def _wizard_model_hint(model_name: str) -> tuple[str, bool]:
    """Gibt Beschreibung und Empfehlung für ein Modell zurück."""
    for prefix, (desc, rec) in _WIZARD_MODEL_HINTS.items():
        if model_name.startswith(prefix):
            return desc, rec
    return "", False


class SetupWizardScreen(Screen[None]):
    """Erster-Start-Wizard: Inbox-Ordner, LLM-Modell, Fertig."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "next_step", "Weiter", show=True),
        Binding("backspace", "prev_step", "Zurück", show=False),
        Binding("escape", "cancel_wizard", "Abbrechen", show=True),
        Binding("q", "cancel_wizard", "Abbrechen", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step: int = 1  # 1, 2 oder 3
        self._inbox_path: Path = Path.home() / "Documents" / "Kurier" / "Eingang"
        self._selected_model: str = _WIZARD_RECOMMENDED
        self._ollama_models: list[str] = []
        self._ollama_running: bool = False
        self._ollama_checked: bool = False

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="wizard-step-indicator")
        yield Static("", id="wizard-content")
        # Schritt 1: Ordner-Button und Pfad-Anzeige
        yield Button("Ordner wählen", id="wizard-pick-folder-btn", variant="primary")
        yield Static("", id="wizard-path-display")
        # Schritt 2: Modell-ListView
        yield ListView(id="wizard-model-list")
        yield Static("", id="wizard-ollama-status")
        # Schritt 3: Zusammenfassung
        yield Static("", id="wizard-summary")
        # Fallback: manuelles Eingabefeld
        yield Input(placeholder="Pfad manuell eingeben...", id="wizard-path-input")
        yield Button("Weiter →", id="wizard-next-btn", variant="success")
        yield Static("", id="wizard-status")
        yield Footer()

    def on_mount(self) -> None:
        self._render_step()
        # Ollama-Check sofort im Hintergrund starten
        threading.Thread(target=self._check_ollama, daemon=True).start()

    # ------------------------------------------------------------------
    # Schritt-Rendering
    # ------------------------------------------------------------------

    def _render_step(self) -> None:
        """Alle Widgets je nach Schritt ein-/ausblenden und befüllen."""
        self._update_indicator()

        pick_btn = self.query_one("#wizard-pick-folder-btn", Button)
        path_display = self.query_one("#wizard-path-display", Static)
        model_list = self.query_one("#wizard-model-list", ListView)
        ollama_status = self.query_one("#wizard-ollama-status", Static)
        summary = self.query_one("#wizard-summary", Static)
        path_input = self.query_one("#wizard-path-input", Input)
        content = self.query_one("#wizard-content", Static)
        status = self.query_one("#wizard-status", Static)
        status.update("")

        next_btn = self.query_one("#wizard-next-btn", Button)

        # Alles ausblenden
        pick_btn.display = False
        path_display.display = False
        model_list.display = False
        ollama_status.display = False
        summary.display = False
        path_input.display = False
        next_btn.display = True

        # Footer-Bindings je Schritt anpassen
        self._update_footer_bindings()

        if self._step == 1:
            content.update(
                "[bold #f5a623]Eingangs-Ordner wählen[/bold #f5a623]\n\n"
                "Wo sollen Dateien zum Sortieren abgelegt werden?\n"
                "[dim]Kurier überwacht diesen Ordner und sortiert neue Dateien automatisch.[/dim]"
            )
            pick_btn.display = True
            path_display.display = True
            path_display.update(f"[dim]Gewählt:[/dim] [bold]{self._inbox_path}[/bold]")
            path_input.display = True
            path_input.value = str(self._inbox_path)
            next_btn.label = "Weiter →"

        elif self._step == 2:
            content.update(
                "[bold #f5a623]LLM-Modell wählen[/bold #f5a623]\n\n"
                "Das Modell klassifiziert deine Dateien automatisch.\n"
                "[#888888]Größere Modelle (7b+) sind genauer, brauchen aber mehr RAM.[/#888888]\n"
            )
            ollama_status.display = True
            model_list.display = True
            if self._ollama_checked:
                self._populate_model_list()
            else:
                ollama_status.update("[dim]Prüfe Ollama...[/dim]")
            next_btn.label = "Weiter →"

        elif self._step == 3:
            content.update(
                "[bold #f5a623]Setup abgeschlossen[/bold #f5a623]\n\n"
                "Alles bereit! Drücke [bold]Enter[/bold] um zu starten."
            )
            summary.display = True
            self._render_summary()
            next_btn.label = "Starten ✓"

    def _update_indicator(self) -> None:
        indicator = self.query_one("#wizard-step-indicator", Static)
        labels = [
            "Eingangs-Ordner",
            "LLM-Modell",
            "Fertig",
        ]
        label = labels[self._step - 1]
        indicator.update(f"[bold]Schritt {self._step} von 3[/bold]  —  [#f5a623]{label}[/#f5a623]")

    def _update_footer_bindings(self) -> None:
        """Footer-Bindings je nach Schritt setzen."""
        # Bindings sind statisch in BINDINGS — Footer zeigt Enter/ESC
        # Wir passen lediglich die show-Eigenschaft dynamisch an
        pass

    def _render_summary(self) -> None:
        from arkiv.core.config import DEFAULT_CONFIG_FILE

        summary = self.query_one("#wizard-summary", Static)
        lines = [
            "[bold]Zusammenfassung:[/bold]\n",
            f"[bold #f5a623]Inbox-Ordner:[/bold #f5a623]  {self._inbox_path}",
            f"[bold #f5a623]LLM-Modell:  [/bold #f5a623]  {self._selected_model}",
            f"[bold #f5a623]Config:      [/bold #f5a623]  {DEFAULT_CONFIG_FILE}",
            "",
            "[dim]Verzeichnisse werden beim Start angelegt.[/dim]",
        ]
        summary.update("\n".join(lines))

    # ------------------------------------------------------------------
    # Ollama-Check (Hintergrund-Thread)
    # ------------------------------------------------------------------

    def _check_ollama(self) -> None:
        """Ollama-Status und verfügbare Modelle ermitteln."""
        running = False
        models: list[str] = []
        try:
            import json
            import urllib.request

            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                running = True
                models = [m["name"] for m in data.get("models", [])]
        except Exception:
            pass

        self._ollama_running = running
        self._ollama_models = models
        self._ollama_checked = True
        self.call_from_thread(self._on_ollama_checked)

    def _on_ollama_checked(self) -> None:
        """Wird im TUI-Thread aufgerufen, sobald Ollama-Check fertig ist."""
        if self._step == 2:
            self._populate_model_list()

    def _populate_model_list(self) -> None:
        """Modell-Liste befüllen."""
        model_list = self.query_one("#wizard-model-list", ListView)
        ollama_status = self.query_one("#wizard-ollama-status", Static)

        model_list.clear()

        if not self._ollama_running:
            ollama_status.update(
                "[yellow]Ollama nicht gefunden.[/yellow]  "
                "Installiere es von [bold]https://ollama.com[/bold]  "
                "[dim]— oder wähle trotzdem ein Modell:[/dim]"
            )
            # Empfehlungsliste aus setup_wizard.py anbieten
            defaults = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b", "mistral"]
            for model in defaults:
                desc, rec = _wizard_model_hint(model)
                star = " [green]★[/green]" if rec else ""
                label = f"{model}{star}  [dim]{desc}[/dim]" if desc else model
                model_list.append(ListItem(Label(label), id=f"model-{model.replace(':', '-')}"))
        else:
            models = self._ollama_models
            if models:
                ollama_status.update(
                    f"[green]Ollama läuft[/green] — {len(models)} Modell(e) verfügbar"
                )
                for model in models[:10]:
                    desc, rec = _wizard_model_hint(model)
                    if rec:
                        label = f"[bold green]{model}[/bold green]  [green]★ {desc}[/green]"
                    elif desc:
                        label = f"[bold]{model}[/bold]  [#888888]{desc}[/#888888]"
                    else:
                        label = f"[bold]{model}[/bold]"
                    safe_id = model.replace(":", "-").replace(".", "-")
                    model_list.append(ListItem(Label(label), id=f"model-{safe_id}"))
                # Empfohlenes Modell vorauswählen
                for i, m in enumerate(models[:10]):
                    if m.startswith(_WIZARD_RECOMMENDED):
                        model_list.index = i
                        break
            else:
                ollama_status.update(
                    "[yellow]Ollama läuft, aber keine Modelle heruntergeladen.[/yellow]\n"
                    "Empfehlung: [bold]ollama pull qwen2.5:7b[/bold]"
                )
                defaults = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b"]
                for model in defaults:
                    desc, rec = _wizard_model_hint(model)
                    if rec:
                        label = f"[bold green]{model}[/bold green]  [green]★ {desc}[/green]"
                    elif desc:
                        label = f"[bold]{model}[/bold]  [#888888]{desc}[/#888888]"
                    else:
                        label = f"[bold]{model}[/bold]"
                    safe_id = model.replace(":", "-").replace(".", "-")
                    model_list.append(ListItem(Label(label), id=f"model-{safe_id}"))

    # ------------------------------------------------------------------
    # Ordner-Picker
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Button-Clicks behandeln."""
        if event.button.id == "wizard-next-btn":
            self.action_next_step()
        elif event.button.id == "wizard-pick-folder-btn":
            threading.Thread(target=self._pick_folder, daemon=True).start()

    def _pick_folder(self) -> None:
        """Native OS-Ordner-Auswahl (nicht-blockierend im Thread)."""
        result: str | None = None
        system = platform.system()
        try:
            if system == "Darwin":
                proc = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'POSIX path of (choose folder with prompt "Eingangs-Ordner wählen:")',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if proc.returncode == 0:
                    result = proc.stdout.strip()
            elif system == "Linux":
                # zenity bevorzugt, kdialog als Fallback
                if shutil.which("zenity"):
                    proc = subprocess.run(
                        ["zenity", "--file-selection", "--directory", "--title=Eingangs-Ordner"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if proc.returncode == 0:
                        result = proc.stdout.strip()
                elif shutil.which("kdialog"):
                    proc = subprocess.run(
                        ["kdialog", "--getexistingdirectory", str(Path.home())],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if proc.returncode == 0:
                        result = proc.stdout.strip()
        except Exception:
            pass

        if result:
            self.call_from_thread(self._set_inbox_path, Path(result))
        # Falls kein Ergebnis: Nutzer kann manuell im Input-Feld eingeben

    def _set_inbox_path(self, path: Path) -> None:
        """Inbox-Pfad aktualisieren (im TUI-Thread)."""
        self._inbox_path = path
        with contextlib.suppress(NoMatches):
            self.query_one("#wizard-path-display", Static).update(
                f"[dim]Gewählt:[/dim] [bold]{path}[/bold]"
            )
            self.query_one("#wizard-path-input", Input).value = str(path)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "wizard-path-input":
            val = event.value.strip()
            if val:
                self._inbox_path = Path(val).expanduser()

    # ------------------------------------------------------------------
    # Schrittnavigation
    # ------------------------------------------------------------------

    def action_next_step(self) -> None:
        """Enter: Nächster Schritt oder Abschluss."""
        status = self.query_one("#wizard-status", Static)

        if self._step == 1:
            # Pfad aus Input-Feld übernehmen
            with contextlib.suppress(NoMatches):
                val = self.query_one("#wizard-path-input", Input).value.strip()
                if val:
                    self._inbox_path = Path(val).expanduser()
            if not str(self._inbox_path).strip():
                status.update("[red]Bitte einen Ordner wählen.[/red]")
                return
            self._step = 2
            self._render_step()

        elif self._step == 2:
            # Gewähltes Modell über den ListView-Index ermitteln
            self._selected_model = self._get_selected_model()
            self._step = 3
            self._render_step()

        elif self._step == 3:
            self._finish_setup()

    def _get_selected_model(self) -> str:
        """Aktuell in der ListView gewähltes Modell ermitteln."""
        try:
            model_list = self.query_one("#wizard-model-list", ListView)
            idx = model_list.index
            if idx is None:
                return _WIZARD_RECOMMENDED

            # Modell-Liste muss identisch zu _populate_model_list sein
            if self._ollama_running and self._ollama_models:
                models_slice = self._ollama_models[:10]
            elif self._ollama_running and not self._ollama_models:
                models_slice = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b"]
            else:
                # Ollama nicht gefunden
                models_slice = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b", "mistral"]

            if 0 <= idx < len(models_slice):
                return models_slice[idx]
        except (NoMatches, Exception):
            pass
        return _WIZARD_RECOMMENDED

    def action_prev_step(self) -> None:
        """Backspace: Schritt zurück."""
        if self._step > 1:
            self._step -= 1
            self._render_step()

    def action_cancel_wizard(self) -> None:
        """Wizard abbrechen — App beenden."""
        self.app.exit()

    # ------------------------------------------------------------------
    # Modell-ListView: Enter-Taste weiterleiten
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter in der Modell-Liste → nächsten Schritt."""
        if event.list_view.id == "wizard-model-list":
            self.action_next_step()

    # ------------------------------------------------------------------
    # Abschluss: Config schreiben
    # ------------------------------------------------------------------

    def _finish_setup(self) -> None:
        """Config schreiben und App beenden."""
        try:
            self._do_write_config()
            with contextlib.suppress(NoMatches):
                self.query_one("#wizard-status", Static).update(
                    "[green]✓ Konfiguration gespeichert![/green]\n"
                    "[bold]Starte kurier erneut für das Hauptmenü.[/bold]"
                )
            self.set_timer(1.5, lambda: self.app.exit())
        except Exception as exc:
            with contextlib.suppress(NoMatches):
                self.query_one("#wizard-status", Static).update(f"[red]Fehler: {exc}[/red]")

    def _do_write_config(self) -> None:
        """Config-Datei schreiben und Verzeichnisse anlegen."""
        from arkiv.core.config import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE

        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        home = Path.home()
        review_dir = home / "Documents" / "Kurier" / "Prüfen"

        lines = [
            "# Kurier Konfiguration",
            "# https://github.com/HerrStolzier/lotse",
            "",
            "[llm]",
            'provider = "ollama"',
            f'model = "{self._selected_model}"',
            'base_url = "http://localhost:11434"',
            "temperature = 0.1",
            "",
            "[embeddings]",
            'model = "BAAI/bge-small-en-v1.5"',
            "",
            "[database]",
            'path = "~/.local/share/arkiv/arkiv.db"',
            "",
            f'inbox_dir = "{self._inbox_path}"',
            f'review_dir = "{review_dir}"',
            "",
            "[routes.archiv]",
            'type = "folder"',
            f'path = "{home / "Documents" / "Kurier" / "Archiv"}"',
            'categories = ["rechnung", "vertrag", "brief", "bescheid"]',
            "confidence_threshold = 0.7",
            "",
        ]

        DEFAULT_CONFIG_FILE.write_text("\n".join(lines) + "\n")

        # Verzeichnisse anlegen
        for d in (self._inbox_path, review_dir, home / "Documents" / "Kurier" / "Archiv"):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# HomeScreen
# ---------------------------------------------------------------------------


class HomeScreen(App[None]):
    """Kurier Hauptmenü."""

    CSS_PATH: ClassVar[Path] = Path(__file__).parent / "styles.css"
    SHOW_HEADER: ClassVar[bool] = False
    ENABLE_COMMAND_PALETTE: ClassVar[bool] = False

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Beenden", show=True),
        Binding("u", "undo_action", "Undo", show=True),
        Binding("1", "select_item('1')", show=False),
        Binding("2", "select_item('2')", show=False),
        Binding("3", "select_item('3')", show=False),
        Binding("4", "select_item('4')", show=False),
        Binding("5", "select_item('5')", show=False),
        Binding("6", "select_item('6')", show=False),
        Binding("7", "select_item('7')", show=False),
    ]

    def __init__(self, config: object = None) -> None:
        super().__init__()
        self._config = config
        self._stats_text = "Lade Statistiken..."

    def compose(self) -> ComposeResult:
        yield Static(LOGO, id="logo")
        list_items = [
            ListItem(Label(f"  [{key}] {label}"), id=f"item-{key}") for key, label in MENU_ITEMS
        ]
        yield ListView(*list_items, id="menu-list")
        yield Static(self._stats_text, id="stats-bar")
        yield Footer()

    def on_mount(self) -> None:
        from arkiv.core.config import DEFAULT_CONFIG_FILE

        if not DEFAULT_CONFIG_FILE.exists():
            self.push_screen(SetupWizardScreen(), self._on_wizard_done)
        else:
            self.load_stats()

    def _on_wizard_done(self, _result: object = None) -> None:
        """Wird aufgerufen, nachdem der Setup-Wizard geschlossen wurde."""
        self.load_stats()

    def load_stats(self) -> None:
        """Statistiken aus der Datenbank laden (nicht-blockierend)."""
        if self._config is None:
            self._update_stats("Keine Konfiguration geladen.")
            return

        try:
            from arkiv.db.store import Store

            cfg = self._config  # type: ignore[assignment]
            if not cfg.database.path.exists():  # type: ignore[union-attr]
                self._update_stats("Datenbank leer — noch keine Einträge.")
                return

            store = Store(cfg.database.path)  # type: ignore[union-attr]
            s = store.stats()
            total = s.get("total_items", 0)
            all_items = store.get_all_items()
            pending = sum(1 for it in all_items if it.get("status") == "pending")
            failed = sum(1 for it in all_items if it.get("status") == "failed")
            self._update_stats(
                f"Einträge: {total}  |  Ausstehend: {pending}  |  Fehlgeschlagen: {failed}"
            )
        except Exception as exc:
            self._update_stats(f"Stats nicht verfügbar: {exc}")

    def _build_stats_text(self) -> str:
        """Ausführliche Statistiken als formatierten Text aufbauen."""
        if self._config is None:
            return "Keine Konfiguration geladen."
        try:
            from arkiv.db.store import Store

            cfg = self._config  # type: ignore[assignment]
            if not cfg.database.path.exists():  # type: ignore[union-attr]
                return "Datenbank leer — noch keine Einträge."

            store = Store(cfg.database.path)  # type: ignore[union-attr]
            s = store.stats()
            total = s.get("total_items", 0)
            all_items = store.get_all_items()

            # Status-Zählungen
            status_counts: dict[str, int] = {}
            for it in all_items:
                st = it.get("status") or "unknown"
                status_counts[st] = status_counts.get(st, 0) + 1

            lines = [f"[bold]Gesamt:[/bold] {total} Einträge\n"]

            lines.append("[bold]Nach Status:[/bold]")
            for st, count in sorted(status_counts.items()):
                color = STATUS_COLORS.get(st, "white")
                lines.append(f"  [{color}]{st}[/{color}]: {count}")

            categories = s.get("categories", {})
            if categories:
                lines.append("\n[bold]Nach Kategorie (Top 10):[/bold]")
                for cat, count in list(categories.items())[:10]:
                    lines.append(f"  {cat}: {count}")

            if s.get("vec_enabled"):
                emb_count = s.get("embeddings", 0)
                lines.append(f"\n[dim]Embeddings: {emb_count}  |  Vektorsuche: aktiv[/dim]")
            else:
                lines.append("\n[dim]Vektorsuche: nicht verfügbar[/dim]")

            return "\n".join(lines)
        except Exception as exc:
            return f"[red]Fehler beim Laden der Statistiken: {exc}[/red]"

    def _update_stats(self, text: str) -> None:
        try:
            bar = self.query_one("#stats-bar", Static)
            bar.update(text)
        except NoMatches:
            self._stats_text = text

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        key = item_id.replace("item-", "")
        self._handle_selection(key)

    def action_select_item(self, key: str) -> None:
        self._handle_selection(key)

    def action_undo_action(self) -> None:
        """Globaler Undo: letzten Eintrag rückgängig machen."""
        if self._config is None:
            self._show_message("[red]Keine Konfiguration geladen.[/red]")
            return

        try:
            from arkiv.db.store import Store

            cfg = self._config  # type: ignore[assignment]
            if not cfg.database.path.exists():  # type: ignore[union-attr]
                self._show_message("[yellow]Datenbank leer — nichts rückgängig zu machen.[/yellow]")
                return

            store = Store(cfg.database.path)  # type: ignore[union-attr]
            recent = store.get_recent(limit=1)
            if not recent:
                self._show_message("[yellow]Keine Einträge vorhanden.[/yellow]")
                return

            item = recent[0]
            if item.get("status") == "undone":
                self._show_message("[dim]Letzter Eintrag wurde bereits rückgängig gemacht.[/dim]")
                return

            filename = Path(item.get("destination") or item.get("original_path") or "?").name

            def _after_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self._do_global_undo(item)

            self.push_screen(UndoConfirmModal(filename), _after_confirm)
        except Exception as exc:
            self._show_message(f"[red]Fehler: {exc}[/red]")

    def _do_global_undo(self, item: dict[str, Any]) -> None:
        """Undo im Hintergrund-Thread ausführen (aus HomeScreen)."""
        thread = threading.Thread(target=self._run_global_undo, args=(item,), daemon=True)
        thread.start()

    def _run_global_undo(self, item: dict[str, Any]) -> None:
        item_id = item.get("id")
        destination = item.get("destination")
        original_path = item.get("original_path")

        if not destination or not original_path:
            self.call_from_thread(
                self._show_message,
                "[red]Fehler: Pfadinformationen fehlen.[/red]",
            )
            return

        dest_path = Path(destination)
        orig_path = Path(original_path)

        if not dest_path.exists():
            self.call_from_thread(
                self._show_message,
                "[red]Datei nicht mehr vorhanden.[/red]",
            )
            return

        if orig_path.exists():
            self.call_from_thread(
                self._show_message,
                "[red]Originalpfad bereits belegt.[/red]",
            )
            return

        try:
            orig_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest_path), str(orig_path))

            if self._config is not None:
                from arkiv.db.store import Store

                cfg = self._config  # type: ignore[assignment]
                store = Store(cfg.database.path)  # type: ignore[union-attr]
                store.update_status(item_id, "undone")

            self.call_from_thread(
                self._show_message,
                f"[green]Rückgängig gemacht: {orig_path.name}[/green]",
            )
        except Exception as exc:
            self.call_from_thread(
                self._show_message,
                f"[red]Fehler beim Zurückbewegen: {exc}[/red]",
            )

    def _handle_selection(self, key: str) -> None:
        if key == "1":
            self.push_screen(AddFileModal(config=self._config))
        elif key == "2":
            self.push_screen(WatchScreen(config=self._config))
        elif key == "3":
            self.push_screen(SearchScreen(config=self._config))
        elif key == "4":
            stats_text = self._build_stats_text()
            self.push_screen(StatsModal(stats_text))
        elif key == "5":
            self.push_screen(RecentScreen(config=self._config))
        elif key == "6":
            self.push_screen(AuditScreen(config=self._config))
        elif key == "7":
            self.push_screen(DoctorModal(config=self._config))
        else:
            labels = {k: v for k, v in MENU_ITEMS}
            label = labels.get(key, "?")
            self._show_message(f"[{key}] {label} — nicht verfügbar.")

    def _show_message(self, text: str) -> None:
        """Meldung in der Stats-Leiste anzeigen."""
        self._update_stats(text)


# Alias für den CLI-Import
ArkivApp = HomeScreen
