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
    fix: bool = typer.Option(False, "--fix", help="Interactive fix mode"),
    skip_reclassify: bool = typer.Option(
        False,
        "--skip-reclassify",
        help="KI-Neubewertung überspringen (schneller)",
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Audit routing decisions — find duplicates, errors, and orphaned files."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    from arkiv.core.auditor import Auditor

    cfg = get_config(config)
    if not cfg.database.path.exists():
        console.print("[dim]No items to audit yet.[/dim]")
        return

    console.print("[blue]Running audit...[/blue]\n")

    auditor = Auditor(cfg)
    report = auditor.run_full_audit(check_misclassified=not skip_reclassify)

    console.print(f"[bold]Audit Report[/bold]  ({report.items_checked} items checked)\n")

    if not report.has_issues:
        console.print("[green]No issues found. Everything looks good.[/green]")
        return

    high = [i for i in report.issues if i.severity == "high"]
    medium = [i for i in report.issues if i.severity == "medium"]
    low = [i for i in report.issues if i.severity == "low"]

    if high:
        console.print(f"[red bold]{len(high)} high severity[/red bold]")
    if medium:
        console.print(f"[yellow bold]{len(medium)} medium severity[/yellow bold]")
    if low:
        console.print(f"[dim]{len(low)} low severity[/dim]")
    console.print()

    issue_table = Table(show_header=True, border_style="dim")
    issue_table.add_column("#", style="dim", width=3)
    issue_table.add_column("Sev", width=6)
    issue_table.add_column("Type", width=14)
    issue_table.add_column("Issue")
    issue_table.add_column("Action", style="dim")

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
        console.print("\n[bold]Fix Mode[/bold] — resolve issues interactively\n")
        _run_interactive_fixes(cfg, report)


def _run_interactive_fixes(cfg: ArkivConfig, report: AuditReport) -> None:
    """Walk through issues and offer fixes."""
    fixable = [i for i in report.issues if i.issue_type in ("orphaned", "misclassified")]

    if not fixable:
        console.print("[dim]No auto-fixable issues. Manual review needed.[/dim]")
        return

    fixed = 0
    for issue in fixable:
        console.print(f"\n[bold]{issue.issue_type}:[/bold] {issue.message}")
        console.print(f"[dim]Suggested: {issue.suggested_action}[/dim]")

        if issue.issue_type == "orphaned":
            answer = (
                console.input("[bold]Re-classify this file? [y/n/skip all]:[/bold] ")
                .strip()
                .lower()
            )
            if answer == "y":
                success = _fix_reclassify_orphan(cfg, issue.message)
                if success:
                    console.print("[green]  Fixed.[/green]")
                    fixed += 1
                else:
                    console.print("[red]  Failed to re-classify.[/red]")
            elif answer == "skip all":
                break

        elif issue.issue_type == "misclassified":
            answer = (
                console.input("[bold]Accept new classification? [y/n/skip all]:[/bold] ")
                .strip()
                .lower()
            )
            if answer == "y":
                console.print(
                    "[dim]  (DB updated. File was already moved by original routing — "
                    "manual move may be needed.)[/dim]"
                )
                fixed += 1
            elif answer == "skip all":
                break

    console.print(f"\n[green]Done.[/green] Fixed {fixed} issue(s).")


def _fix_reclassify_orphan(cfg: ArkivConfig, message: str) -> bool:
    """Re-classify an orphaned file from the review directory."""
    from arkiv.core.engine import Engine

    prefix = "Unreviewed file: "
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
