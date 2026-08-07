"""Evaluation and benchmark CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from arkiv.commands.common import console
from arkiv.evals.llm_benchmark import (
    TaskName,
    default_models,
    parse_model_spec,
    run_benchmark,
    write_report,
)

eval_app = typer.Typer(help="KI-Modelle für Kurier testen und vergleichen.")


_TASK_LABELS = {
    "classifier": "Dokumente erkennen",
    "search": "Suchanfragen verstehen",
    "retrieval": "Richtige Treffer finden",
}

_STATUS_LABELS = {
    "ok": "[green]fertig[/green]",
    "skipped_missing_credentials": "[yellow]übersprungen: Zugang fehlt[/yellow]",
    "error": "[red]fehlgeschlagen[/red]",
}


def _tasks_from_options(task: str, run_all: bool) -> list[TaskName]:
    if run_all:
        return ["classifier", "search", "retrieval"]
    if task not in ("classifier", "search", "retrieval"):
        raise typer.BadParameter("task must be classifier, search, or retrieval")
    return [task]  # type: ignore[list-item]


@eval_app.command("llm")
def llm(
    task: Annotated[
        str,
        typer.Option(
            "--task",
            help="Testaufgabe: classifier, search oder retrieval. Bei --all ohne Wirkung.",
        ),
    ] = "classifier",
    models: Annotated[
        list[str] | None,
        typer.Option("--models", help="Modell als provider:model. Mehrfach angebbar."),
    ] = None,
    run_all: Annotated[bool, typer.Option("--all", help="Alle Testaufgaben ausführen.")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Auswahl prüfen, ohne KI-Modelle aufzurufen."),
    ] = False,
) -> None:
    """KI-Modelle für Kurier testen und vergleichen."""
    selected_tasks = _tasks_from_options(task, run_all)
    selected_models = models or default_models()

    # Parse early so CLI users get immediate feedback on invalid specs.
    parsed_models = [parse_model_spec(model) for model in selected_models]

    if dry_run:
        task_labels = [
            _TASK_LABELS.get(selected_task, selected_task) for selected_task in selected_tasks
        ]
        console.print("[bold]Kurier Modell-Test: Probelauf[/bold]")
        console.print(f"Geprüfte Aufgaben: {', '.join(task_labels)}")
        console.print(f"Geprüfte Modelle: {', '.join(model.label for model in parsed_models)}")
        console.print("[dim]Es wurden noch keine KI-Modelle aufgerufen.[/dim]")
        return

    report = run_benchmark(tasks=selected_tasks, model_specs=selected_models)

    table = Table(title="Kurier Modell-Test")
    table.add_column("Was wurde geprüft?")
    table.add_column("Modell")
    table.add_column("Ergebnis")
    table.add_column("Fälle", justify="right")
    table.add_column("Qualität", justify="right")
    table.add_column("Zeit pro Fall", justify="right")

    for result in report.results:
        if result.avg_latency_ms is None:
            latency = "nicht gemessen"
        else:
            latency = f"{result.avg_latency_ms:.0f} ms"
        table.add_row(
            _TASK_LABELS.get(result.task, result.task),
            result.model,
            _STATUS_LABELS.get(result.status, result.status),
            str(result.cases),
            f"{result.overall_score:.0%}",
            latency,
        )

    console.print(table)

    if report.recommendation is not None:
        rec = report.recommendation
        latency = (
            f", im Schnitt {rec.avg_latency_ms:.0f} ms pro Fall"
            if rec.avg_latency_ms is not None
            else ""
        )
        console.print(
            "[green]Empfehlung:[/green] "
            f"{rec.model} ist aktuell die beste Wahl "
            f"({rec.overall_score:.0%} Qualität{latency})."
        )
        console.print(f"[dim]{rec.reason}[/dim]")
    else:
        console.print(
            "[yellow]Keine Modell-Empfehlung möglich.[/yellow] "
            "Es wurde kein nutzbares KI-Modell vollständig getestet."
        )

    report_path = write_report(report, output)
    console.print(f"[green]✓[/green] Ausführlicher Bericht gespeichert: {report_path}")
    console.print(
        "[dim]Kurz gelesen: 100% wäre perfekt. Niedrigere Werte zeigen, wo ein Modell "
        "bei Kurier noch unzuverlässig ist.[/dim]"
    )


def register(app: typer.Typer) -> None:
    """Register eval commands."""
    app.add_typer(eval_app, name="eval")
