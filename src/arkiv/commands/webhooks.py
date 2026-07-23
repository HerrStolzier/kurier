"""Webhook outbox CLI commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.table import Table

from arkiv.commands.common import console, get_context

webhooks_app = typer.Typer(name="webhooks", help="Fehlgeschlagene Webhooks verwalten.")


def _next_retry_at(attempt_count: int) -> str:
    """Return the next retry time using a small local backoff schedule."""
    minutes = [1, 5, 15, 60]
    delay = minutes[min(max(attempt_count - 1, 0), len(minutes) - 1)]
    return (datetime.now(UTC) + timedelta(minutes=delay)).isoformat()


@webhooks_app.command("list")
def list_webhooks(
    config: Path | None = typer.Option(None, "--config", "-c"),
    all_statuses: bool = typer.Option(
        False,
        "--all",
        help="Auch bereits zugestellte Webhooks anzeigen.",
    ),
    limit: int = typer.Option(20, "--limit", "-n", min=1),
) -> None:
    """Show pending and failed webhook deliveries."""
    ctx = get_context(config)
    statuses = ("pending", "failed", "delivered") if all_statuses else ("pending", "failed")
    rows = ctx.engine.store.list_webhook_outbox(statuses=statuses, limit=limit)

    if not rows:
        console.print("[green]Keine offenen Webhooks.[/green]")
        return

    table = Table(show_header=True, border_style="dim")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Status", width=10)
    table.add_column("Versuche", justify="right", width=8)
    table.add_column("Route", width=16)
    table.add_column("Naechster Versuch", width=24)
    table.add_column("Fehler")

    for row in rows:
        table.add_row(
            str(row["id"]),
            row["status"],
            str(row["attempt_count"]),
            row["route_name"],
            row["next_attempt_at"] or "-",
            row["last_error"] or "",
        )

    console.print(table)


@webhooks_app.command("retry")
def retry_webhooks(
    config: Path | None = typer.Option(None, "--config", "-c"),
    limit: int = typer.Option(20, "--limit", "-n", min=1),
    force: bool = typer.Option(
        False,
        "--force",
        help="Auch Webhooks erneut versuchen, deren Retry-Zeit noch nicht erreicht ist.",
    ),
    max_attempts: int = typer.Option(5, "--max-attempts", min=1),
) -> None:
    """Retry due webhook deliveries from the local outbox."""
    ctx = get_context(config)
    rows = ctx.engine.store.list_webhook_outbox(
        statuses=("pending", "failed"),
        due_only=not force,
        limit=limit,
    )

    if not rows:
        console.print("[green]Keine faelligen Webhooks zum erneuten Senden.[/green]")
        return

    try:
        from arkiv_webhook import send_webhook
    except ImportError:
        console.print("[red]arkiv-webhook ist nicht installiert.[/red]")
        raise typer.Exit(code=1) from None

    delivered = 0
    failed = 0
    for row in rows:
        ok = send_webhook(row["url"], row["payload"])
        if ok:
            ctx.engine.store.mark_webhook_delivered(row["id"])
            if row.get("item_id"):
                ctx.engine.store.reconcile_item_after_webhook_delivery(row["item_id"])
            delivered += 1
            continue

        failed += 1
        next_attempt_count = int(row["attempt_count"]) + 1
        terminal = next_attempt_count >= max_attempts
        ctx.engine.store.mark_webhook_failed(
            row["id"],
            error="Webhook retry failed",
            next_attempt_at=None if terminal else _next_retry_at(next_attempt_count),
            terminal=terminal,
        )

    console.print(f"[green]{delivered} zugestellt[/green], [yellow]{failed} weiter offen[/yellow]")


def register(app: typer.Typer) -> None:
    """Register webhook outbox commands."""
    app.add_typer(webhooks_app)
