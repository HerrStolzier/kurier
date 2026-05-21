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
        help="Deprecated. Non-localhost bindings always require --api-key.",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="KURIER_API_KEY",
        help="API key required for non-localhost access (header: x-api-key).",
    ),
) -> None:
    """Start the REST API server."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    try:
        import uvicorn
    except ImportError:
        console.print("[red]Missing dependency.[/red] Reinstall Kurier to restore API packages:")
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
                f"\n[red bold]Fehler:[/red bold] Binding to [bold]{host}[/bold] "
                "exposes the API to your network.\n"
                "Use [bold]--api-key <key>[/bold] to require authentication.\n"
                f"{force_note}"
            )
            raise typer.Exit(1)

        console.print(
            f"\n[yellow]Non-localhost binding:[/yellow] "
            f"[bold]{host}:{port}[/bold] — API key authentication active.\n"
        )

    from arkiv.inlets.api import create_app

    cfg = get_config(config)
    localhost_only = not is_loopback
    api = create_app(cfg, api_key=api_key, localhost_only=localhost_only)

    console.print(f"\n[bold]Kurier API[/bold] v{__version__}")
    console.print(f"[dim]Docs:[/dim]    http://{host}:{port}/docs")
    console.print(f"[dim]Health:[/dim]  http://{host}:{port}/health\n")

    uvicorn.run(api, host=host, port=port, log_level="info")


def register(app: typer.Typer) -> None:
    """Register API server commands."""
    app.command()(serve)
