"""Status-related CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from arkiv.commands.common import DEFAULT_CONFIG_FILE, __version__, console, get_config


def status(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Zeige, ob Kurier eingerichtet ist und was bereits verarbeitet wurde."""
    from arkiv.core.ocr import ocr_available
    from arkiv.db.store import Store

    cfg = get_config(config)

    console.print(f"\n[bold]Kurier ist eingerichtet[/bold]  [dim]Version {__version__}[/dim]\n")

    config_display = config or DEFAULT_CONFIG_FILE
    console.print(f"[dim]Einstellungen:[/dim] {config_display}")
    console.print(f"[dim]Ablage-Daten:[/dim]  {cfg.database.path}")
    console.print(f"[dim]Eingang:[/dim]       {cfg.inbox_dir}")
    console.print(f"[dim]KI-Modell:[/dim]     {cfg.llm.provider}/{cfg.llm.model}")
    console.print(f"[dim]Sortierregeln:[/dim] {len(cfg.routes)} eingerichtet")
    console.print(f"[dim]Suchmodell:[/dim]    {cfg.embeddings.model}")

    ocr = ocr_available()
    if ocr["tesseract_bin"]:
        console.print("[dim]Texterkennung:[/dim] [green]bereit[/green] (PDFs und Scans)")
    elif ocr["pymupdf"]:
        console.print(
            "[dim]Texterkennung:[/dim] [yellow]teilweise bereit[/yellow] "
            "(PDF-Text ja, gescannte Bilder noch nicht)"
        )
    else:
        console.print(
            "[dim]Texterkennung:[/dim] [dim]nicht eingerichtet[/dim] "
            "(für gescannte Dokumente fehlen noch die OCR-Bausteine)"
        )
    console.print()

    if not cfg.database.path.exists():
        console.print("[dim]Noch keine Dokumente verarbeitet.[/dim]")
        return

    store = Store(cfg.database.path)
    s = store.stats()

    console.print(f"[bold]Verarbeitete Dokumente:[/bold] {s['total_items']}\n")

    if s.get("vec_enabled"):
        console.print(
            f"[dim]Intelligente Suche:[/dim] [green]bereit[/green]"
            f" ({s.get('embeddings', 0)} Suchsignale gespeichert)\n"
        )
    else:
        console.print(
            "[dim]Intelligente Suche:[/dim] [yellow]nicht aktiv[/yellow] (sqlite-vec fehlt)\n"
        )

    open_webhooks = int(s.get("webhooks_open", 0))
    if open_webhooks:
        console.print(
            f"[dim]Integrationen:[/dim] [yellow]{open_webhooks} Zustellung offen[/yellow] "
            "(erneut versuchen mit: kurier webhooks retry)\n"
        )
    else:
        console.print("[dim]Integrationen:[/dim] [green]keine offenen Webhooks[/green]\n")

    console.print(
        "[dim]Nächster Schritt:[/dim] Dokumente in den Eingang legen. "
        "Ansehen und suchen kannst du alles im Dashboard: "
        "[bold]kurier serve[/bold] starten, dann "
        "http://127.0.0.1:8790/dashboard/ im Browser öffnen.\n"
    )

    if s["categories"]:
        cat_table = Table(title="Dokumentarten")
        cat_table.add_column("Art", style="cyan")
        cat_table.add_column("Anzahl", justify="right")
        for cat, count in s["categories"].items():
            cat_table.add_row(cat, str(count))
        console.print(cat_table)

    latest = store.get_recent(limit=1)
    if latest:
        item = latest[0]
        title = item.get("display_title") or item.get("destination_name") or "Unbenannt"
        category = item.get("category", "unbekannt")
        confidence = float(item.get("confidence") or 0) * 100
        from arkiv.core.router import display_route

        destination = (
            item.get("destination_name") or display_route(item.get("route_name")) or "keine Ablage"
        )
        console.print(
            f"\n[dim]Zuletzt erledigt:[/dim] {title} "
            f"({category}, {confidence:.0f}% sicher) → {destination}"
        )


def register(app: typer.Typer) -> None:
    """Register status commands."""
    app.command()(status)
