"""Audit-related CLI commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from arkiv.commands.common import ArkivConfig, console, get_config

if TYPE_CHECKING:
    from arkiv.core.auditor import AuditReport


def audit(
    fix: bool = typer.Option(False, "--fix", help="Probleme direkt beheben (interaktiv)"),
    skip_reclassify: bool = typer.Option(
        False,
        "--skip-reclassify",
        help="KI-Neubewertung überspringen (schneller)",
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Selbstprüfung: Duplikate, Fehler und liegengebliebene Dateien finden."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    from arkiv.core.auditor import Auditor

    cfg = get_config(config)
    if not cfg.database.path.exists():
        console.print(
            "[dim]Noch nichts zu prüfen — es wurden noch keine Dokumente verarbeitet.[/dim]"
        )
        return

    console.print("[blue]Selbstprüfung läuft...[/blue]\n")

    auditor = Auditor(cfg)
    report = auditor.run_full_audit(check_misclassified=not skip_reclassify)

    console.print(f"[bold]Prüfbericht[/bold]  ({report.items_checked} Einträge geprüft)\n")

    if not report.has_issues:
        console.print("[green]Keine Probleme gefunden. Alles in Ordnung.[/green]")
        return

    high = [i for i in report.issues if i.severity == "high"]
    medium = [i for i in report.issues if i.severity == "medium"]
    low = [i for i in report.issues if i.severity == "low"]

    if high:
        console.print(f"[red bold]{len(high)} wichtig[/red bold]")
    if medium:
        console.print(f"[yellow bold]{len(medium)} mittel[/yellow bold]")
    if low:
        console.print(f"[dim]{len(low)} gering[/dim]")
    console.print()

    issue_table = Table(show_header=True, border_style="dim")
    issue_table.add_column("#", style="dim", width=3)
    issue_table.add_column("Stufe", width=8)
    issue_table.add_column("Art", width=22)
    issue_table.add_column("Problem")
    issue_table.add_column("Empfehlung", style="dim")

    severity_style = {"high": "red", "medium": "yellow", "low": "dim"}

    for idx, issue in enumerate(report.issues, 1):
        style = severity_style.get(issue.severity, "dim")
        issue_table.add_row(
            str(idx),
            f"[{style}]{issue.severity}[/{style}]",
            issue.issue_type,
            issue.message,
            issue.suggested_action,
        )

    console.print(issue_table)

    if fix:
        console.print("\n[bold]Behebungs-Modus[/bold] — Probleme einzeln durchgehen\n")
        _run_interactive_fixes(cfg, report)


def _run_interactive_fixes(cfg: ArkivConfig, report: AuditReport) -> None:
    """Walk through issues and offer fixes."""
    fixable = [i for i in report.issues if i.issue_type in ("orphaned", "misclassified")]

    if not fixable:
        console.print("[dim]Nichts automatisch behebbar. Bitte von Hand prüfen.[/dim]")
        return

    fixed = 0
    for issue in fixable:
        console.print(f"\n[bold]{issue.issue_type}:[/bold] {issue.message}")
        console.print(f"[dim]Empfehlung: {issue.suggested_action}[/dim]")

        if issue.issue_type == "orphaned":
            answer = (
                console.input(
                    "[bold]Datei neu einsortieren lassen? [j/n/alle überspringen]:[/bold] "
                )
                .strip()
                .lower()
            )
            if answer in ("j", "y"):
                success = _fix_reclassify_orphan(cfg, issue.message)
                if success:
                    console.print("[green]  Behoben.[/green]")
                    fixed += 1
                else:
                    console.print("[red]  Neu-Einsortieren hat nicht geklappt.[/red]")
            elif answer in ("alle überspringen", "skip all"):
                break

        elif issue.issue_type == "misclassified":
            answer = (
                console.input("[bold]Neue Einordnung übernehmen? [j/n/alle überspringen]:[/bold] ")
                .strip()
                .lower()
            )
            if answer in ("j", "y"):
                console.print(
                    "[dim]  (Eintrag aktualisiert. Die Datei liegt noch am alten Ort — "
                    "bei Bedarf von Hand verschieben.)[/dim]"
                )
                fixed += 1
            elif answer in ("alle überspringen", "skip all"):
                break

    console.print(f"\n[green]Fertig.[/green] {fixed} Problem(e) behoben.")


def _fix_reclassify_orphan(cfg: ArkivConfig, message: str) -> bool:
    """Re-classify an orphaned file from the review directory."""
    from arkiv.core.engine import Engine

    prefix = "Ungeprüfte Datei: "
    if prefix not in message:
        return False
    filename = message.split(prefix, 1)[1].strip()
    file_path = cfg.review_dir / filename

    if not file_path.exists():
        return False

    engine = Engine(cfg)
    result = engine.ingest_file(file_path)
    return result.success


def register(app: typer.Typer) -> None:
    """Register audit commands."""
    app.command()(audit)
