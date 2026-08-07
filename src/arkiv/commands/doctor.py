"""Doctor/diagnostics CLI commands."""

from __future__ import annotations

import json
import tomllib
import urllib.request
from pathlib import Path

import typer
from rich.table import Table

from arkiv.commands.common import DEFAULT_CONFIG_FILE, ArkivConfig, console
from arkiv.core.hardware import detect_ram_gb, model_fits_ram


def _count_visible_inbox_files(inbox_dir: Path) -> int:
    from arkiv.inlets.watch import list_inbox_files

    return len(list_inbox_files(inbox_dir))


def _count_phrase(count: int, verb_singular: str, verb_plural: str) -> str:
    noun = "Datei" if count == 1 else "Dateien"
    verb = verb_singular if count == 1 else verb_plural
    return f"{count} {noun} {verb}"


def _doctor_directory_targets(cfg: ArkivConfig) -> list[tuple[str, Path]]:
    targets = [
        ("Datenbank-Ordner", cfg.database.path.parent),
        ("Eingang (Ordner)", cfg.inbox_dir),
        ("Prüfen (Ordner)", cfg.review_dir),
    ]
    for name, route in cfg.routes.items():
        if route.path:
            targets.append((f"Route '{name}'", Path(route.path).expanduser()))
    return targets


def doctor(
    config: Path | None = typer.Option(None, "--config", "-c"),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Lege fehlende Ordner aus den Einstellungen direkt an",
    ),
) -> None:
    """Prüfe, ob Kurier startklar ist und was noch Aufmerksamkeit braucht."""
    config_path = config or DEFAULT_CONFIG_FILE

    check_table = Table(title="Kurier Gesundheitscheck", show_header=True, border_style="dim")
    check_table.add_column("OK", width=4)
    check_table.add_column("Prüfung")
    check_table.add_column("Details", style="dim")

    def ok(label: str, detail: str = "") -> None:
        check_table.add_row("[green]✓[/green]", label, detail)

    def warn(label: str, detail: str = "") -> None:
        check_table.add_row("[yellow]![/yellow]", label, detail)

    def fail(label: str, detail: str = "") -> None:
        check_table.add_row("[red]✗[/red]", label, detail)

    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                tomllib.load(f)
            ok("Einstellungen", str(config_path))
            cfg_valid = True
        except Exception as e:
            fail("Einstellungen", f"Die Datei ist nicht lesbar: {e}")
            cfg_valid = False
    else:
        fail("Einstellungen", f"Nicht gefunden: {config_path}")
        cfg_valid = False

    cfg = None
    if cfg_valid:
        try:
            cfg = ArkivConfig.load(config_path)
        except Exception as e:
            fail("Einstellungen laden", str(e))
            cfg_valid = False

    if cfg is not None:
        from arkiv import service

        try:
            info = service.status()
            backlog = _count_visible_inbox_files(cfg.inbox_dir)
            review_backlog = _count_visible_inbox_files(cfg.review_dir)

            if info.get("running"):
                detail = "Kurier sortiert neue Dateien automatisch"
                if backlog:
                    detail += f"; {_count_phrase(backlog, 'wartet', 'warten')} gerade im Eingang"
                ok("Auto-Sortierung", detail)
            else:
                detail = "Automatische Sortierung ist aus. Starte sie mit: kurier service on"
                if backlog:
                    detail += f" ({_count_phrase(backlog, 'wartet', 'warten')} im Eingang)"
                warn("Auto-Sortierung", detail)

            if backlog:
                warn(
                    "Eingang (wartende Dateien)",
                    f"{_count_phrase(backlog, 'liegt', 'liegen')} im Eingang",
                )
            else:
                ok("Eingang (wartende Dateien)", "Leer")

            if review_backlog:
                warn(
                    "Prüfen (wartende Dateien)",
                    f"{_count_phrase(review_backlog, 'wartet', 'warten')} auf Sichtung",
                )
            else:
                ok("Prüfen (wartende Dateien)", "Leer")
        except Exception as e:
            warn("Auto-Sortierung", f"Status konnte nicht geprüft werden: {e}")

    if cfg is not None:
        for label, directory in _doctor_directory_targets(cfg):
            if directory.exists():
                ok(label, str(directory))
                continue

            if fix:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    ok(label, f"Angelegt: {directory}")
                except OSError as e:
                    fail(label, f"Konnte nicht angelegt werden: {e}")
            else:
                warn(
                    label,
                    "Fehlt noch: "
                    f"{directory}  (kurier doctor --fix legt den Ordner automatisch an)",
                )

    if cfg is not None:
        if cfg.llm.provider == "ollama":
            ollama_url = (cfg.llm.base_url or "http://localhost:11434").rstrip("/")
            try:
                with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3) as resp:
                    data = json.loads(resp.read())
                    models = [m["name"] for m in data.get("models", [])]
                model_names_base = [m.split(":")[0] for m in models]
                if cfg.llm.model in models or cfg.llm.model in model_names_base:
                    ok("KI-Modell erreichbar", f"{cfg.llm.provider}/{cfg.llm.model}")
                else:
                    available = ", ".join(models[:3]) or "-"
                    warn(
                        "KI-Modell fehlt",
                        f"{cfg.llm.model} ist noch nicht installiert. Verfügbar: {available}",
                    )
            except Exception as e:
                from arkiv.core.errors import friendly_error

                fail("KI-Modell erreichbar", friendly_error(e))

            ram_gb = detect_ram_gb()
            fits, detail = model_fits_ram(cfg.llm.model, ram_gb)
            if fits is True:
                ok("Arbeitsspeicher passt zum KI-Modell", detail)
            elif fits is False:
                warn("Arbeitsspeicher passt zum KI-Modell", detail)
            else:
                warn("Arbeitsspeicher passt zum KI-Modell", detail)
        else:
            ok("KI-Modell", f"{cfg.llm.provider} (API-Key über Umgebungsvariable)")

    if cfg is not None and cfg.categories:
        empty_cats = [name for name, desc in cfg.categories.items() if not desc or not desc.strip()]
        if empty_cats:
            warn("Beschreibungen für Dokumentarten", f"Leer bei: {', '.join(empty_cats)}")
        else:
            ok("Beschreibungen für Dokumentarten", "Alle vorhanden")

    if cfg is not None and cfg.database.path.exists():
        try:
            from arkiv.db.store import Store

            store = Store(cfg.database.path)
            all_items = store.get_all_items()
            pending = sum(1 for it in all_items if it.get("status") == "pending")
            failed = sum(1 for it in all_items if it.get("status") == "failed")
            if pending or failed:
                warn(
                    "Ablage-Verarbeitung",
                    f"{pending} noch in Arbeit, {failed} fehlgeschlagen. "
                    "Öffne das Dashboard oder prüfe die betroffenen Dateien.",
                )
            else:
                ok("Ablage-Daten", f"{len(all_items)} Einträge, keine Fehler")

            stats = store.stats()
            open_webhooks = int(stats.get("webhooks_open", 0))
            if open_webhooks:
                warn(
                    "Integrationen",
                    f"{open_webhooks} Webhook-Zustellung offen. "
                    "Nächster Schritt: kurier webhooks retry",
                )
            else:
                ok("Integrationen", "Keine offenen Webhook-Zustellungen")
        except Exception as e:
            warn("Datenbank", str(e))
    elif cfg is not None:
        ok("Ablage-Daten", "Noch leer (kein Dokument verarbeitet)")

    console.print(check_table)


def register(app: typer.Typer) -> None:
    """Register doctor commands."""
    app.command()(doctor)
