"""Undo-related CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from arkiv.commands.common import console, get_config


def undo(
    item_id: Annotated[int | None, typer.Option("--id", help="Specific item ID to undo")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Routing einer Datei rückgängig machen — verschiebt sie zurück zum Ursprungsort."""
    from arkiv.db.store import Store

    cfg = get_config(config)

    if not cfg.database.path.exists():
        console.print("[dim]Keine verarbeiteten Einträge gefunden.[/dim]")
        raise typer.Exit(1)

    store = Store(cfg.database.path)

    if item_id is not None:
        item = store.undo_item(item_id)
        if item is None:
            console.print(f"[red]Kein Dokument mit ID {item_id} gefunden.[/red]")
            raise typer.Exit(1)
        items = [item]
    else:
        recent = store.get_recent(limit=1)
        if not recent:
            console.print("[dim]Keine Einträge vorhanden.[/dim]")
            raise typer.Exit(1)
        item = store.undo_item(recent[0]["id"])
        if item is None:
            console.print("[red]Das letzte Dokument konnte nicht geladen werden.[/red]")
            raise typer.Exit(1)
        items = [item]

    for it in items:
        iid = it["id"]
        dest = Path(it["destination"]) if it["destination"] else None
        orig = Path(it["original_path"])

        if dest is None or not dest.exists():
            console.print(f"[yellow]Datei nicht mehr vorhanden:[/yellow] {dest}")
            store.update_status(iid, "undone")
            console.print(f"[dim]In Kurier als rückgängig markiert (ID {iid}).[/dim]")
            continue

        if orig.exists():
            console.print(f"[yellow]Zielpfad bereits belegt:[/yellow] {orig}")
            console.print(
                "[dim]Datei nicht verschoben. Benenne die Datei am Zielort um oder lösche "
                "sie, dann noch einmal versuchen.[/dim]"
            )
            continue

        orig.parent.mkdir(parents=True, exist_ok=True)
        dest.rename(orig)
        store.update_status(iid, "undone")
        console.print(f"[green]✓[/green] Zurückverschoben: {dest.name} → {orig}")


def register(app: typer.Typer) -> None:
    """Register undo commands."""
    app.command()(undo)
