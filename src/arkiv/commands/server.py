"""API server CLI commands."""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path

import typer

from arkiv.commands.common import __version__, console, get_config


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    port: int = typer.Option(8790, "--port", "-p"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Veraltet. Zugriff von außen braucht immer --api-key.",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="KURIER_API_KEY",
        help="Zugangsschlüssel für Zugriff von anderen Geräten (Header: x-api-key).",
    ),
) -> None:
    """Web-Oberfläche und Schnittstelle starten (Dashboard im Browser)."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Ein Baustein fehlt.[/red] Installiere Kurier neu, dann ist er wieder da:"
        )
        console.print('  pip install "kurier @ git+https://github.com/HerrStolzier/kurier.git"')
        raise typer.Exit(1) from None

    is_loopback = _is_loopback_host(host)
    if not is_loopback:
        if not api_key:
            force_note = (
                " [dim]Hinweis: --force deaktiviert die API-Key-Pflicht nicht mehr.[/dim]\n"
                if force
                else ""
            )
            console.print(
                f"\n[red bold]Fehler:[/red bold] Mit [bold]{host}[/bold] wäre Kurier "
                "für andere Geräte im Netzwerk erreichbar.\n"
                "Setze [bold]--api-key <schlüssel>[/bold], damit nur Berechtigte zugreifen.\n"
                f"{force_note}"
            )
            raise typer.Exit(1)

        console.print(
            f"\n[yellow]Erreichbar im Netzwerk:[/yellow] "
            f"[bold]{host}:{port}[/bold] — Zugangsschlüssel ist Pflicht.\n"
        )

    from arkiv.inlets.api import create_app

    cfg = get_config(config)
    localhost_only = not is_loopback
    api = create_app(cfg, api_key=api_key, localhost_only=localhost_only)

    console.print(f"\n[bold]Kurier API[/bold] v{__version__}")
    console.print(f"[dim]Dashboard:[/dim] http://{host}:{port}/dashboard/")
    console.print(f"[dim]API-Doku:[/dim]  http://{host}:{port}/docs\n")

    uvicorn.run(api, host=host, port=port, log_level="info")


def register(app: typer.Typer) -> None:
    """Register API server commands."""
    app.command()(serve)
