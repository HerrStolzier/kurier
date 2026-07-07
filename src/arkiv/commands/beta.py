"""Beta feedback commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from arkiv.application.beta import get_beta_report
from arkiv.commands.common import console, get_context

app = typer.Typer(help="Beta-Hinweise aus echter Nutzung ansehen.")


@app.command()
def report(
    days: int = typer.Option(7, "--days", help="Wie viele Tage zusammenfassen."),
    limit: int = typer.Option(30, "--limit", help="Wie viele einzelne Hinweise anzeigen."),
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Zeige lokale Stolperer, die Kurier automatisch und manuell gesammelt hat."""
    ctx = get_context(config)
    data = get_beta_report(ctx, days=days, limit=limit)
    summary = data["summary"]
    events = data["events"]
    recommendations = data["recommendations"]

    console.print(f"\n[bold]Kurier Beta-Bericht[/bold]  [dim]letzte {days} Tage[/dim]\n")
    console.print(
        "[dim]Diese Hinweise bleiben lokal. Sie zeigen, wo Kurier im Alltag noch hakt.[/dim]\n"
    )

    if summary["total"] == 0:
        console.print("[green]Noch keine Stolperer notiert.[/green]")
        console.print(
            "[dim]Starte den 5-Tage-Alltagstest: jeden Tag ein echtes Dokument "
            "oder eine echte Suche nutzen.[/dim]"
        )
        console.print("[dim]Bei Frust im Dashboard kurz “Problem melden” nutzen.[/dim]")
        return

    console.print(f"[bold]Gesammelte Hinweise:[/bold] {summary['total']}\n")

    type_table = Table(title="Was ist aufgefallen?", show_header=True, border_style="dim")
    type_table.add_column("Bereich", style="cyan")
    type_table.add_column("Schwere")
    type_table.add_column("Anzahl", justify="right")
    for row in summary["by_type"]:
        type_table.add_row(
            _event_label(row["event_type"]),
            _severity_label(row["severity"]),
            str(row["count"]),
        )
    console.print(type_table)

    if recommendations:
        console.print()
        console.print("[bold]Nächste sinnvolle Produktaufgaben:[/bold]")
        for index, recommendation in enumerate(recommendations, 1):
            console.print(
                f"{index}. {recommendation['title']} ({recommendation['count']} Signal(e))"
            )
        console.print()
        action_table = Table(
            title="Was sollte als Nächstes verbessert werden?",
            show_header=True,
            border_style="dim",
        )
        action_table.add_column("Priorität", justify="right", width=8)
        action_table.add_column("Aufgabe", style="cyan")
        action_table.add_column("Warum")
        action_table.add_column("Signale", justify="right")
        for index, recommendation in enumerate(recommendations, 1):
            action_table.add_row(
                str(index),
                recommendation["title"],
                recommendation["action"],
                str(recommendation["count"]),
            )
        console.print(action_table)

    if events:
        console.print()
        event_table = Table(title="Letzte Hinweise", show_header=True, border_style="dim")
        event_table.add_column("Wann", style="dim", width=19)
        event_table.add_column("Bereich", style="cyan")
        event_table.add_column("Hinweis")
        event_table.add_column("Dokument", style="dim")
        for event in events:
            event_table.add_row(
                event["created_at"][:19],
                _event_label(event["event_type"]),
                event["message"],
                event.get("display_title") or "",
            )
        console.print(event_table)


def _event_label(event_type: str) -> str:
    labels = {
        "manual_feedback": "Manuell gemeldet",
        "search_no_results": "Suche ohne Treffer",
        "upload_failed": "Upload fehlgeschlagen",
        "low_confidence_review": "Unsichere Einordnung",
        "category_corrected": "Kategorie korrigiert",
        "classification_confirmed": "Einordnung bestätigt",
    }
    return labels.get(event_type, event_type)


def _severity_label(severity: str) -> str:
    labels = {
        "info": "Hinweis",
        "warn": "Achtung",
        "error": "Fehler",
    }
    return labels.get(severity, severity)


def register(root: typer.Typer) -> None:
    """Register beta commands."""
    root.add_typer(app, name="beta")
