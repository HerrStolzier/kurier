"""Lotse CLI — your AI-powered data pilot."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from lotse import __version__
from lotse.core.config import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE, LotseConfig

app = typer.Typer(
    name="lotse",
    help="Universal capture → classify → route. Your AI-powered data pilot.",
    no_args_is_help=True,
)
console = Console()


def _get_config(config: Path | None = None) -> LotseConfig:
    cfg = LotseConfig.load(config)
    cfg.ensure_dirs()
    return cfg


@app.command()
def add(
    path: Path = typer.Argument(..., help="File path, URL, or '-' for stdin"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Add a file or URL to be classified and routed."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = _get_config(config)

    from lotse.core.engine import Engine

    engine = Engine(cfg)

    if str(path) == "-":
        import sys

        text = sys.stdin.read()
        result = engine.ingest_text(text)
    elif path.exists():
        result = engine.ingest_file(path)
    else:
        console.print(f"[red]Not found:[/red] {path}")
        raise typer.Exit(1)

    if result.success:
        console.print(f"[green]✓[/green] {result.message}")
    else:
        console.print(f"[red]✗[/red] {result.message}")
        raise typer.Exit(1)


@app.command()
def watch(
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Watch the inbox directory and auto-process new files."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    cfg = _get_config(config)

    from lotse.core.engine import Engine
    from lotse.inlets.watch import Watcher

    engine = Engine(cfg)

    console.print(f"[blue]Watching:[/blue] {cfg.inbox_dir}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    def _ingest_and_discard(p: Path) -> None:
        engine.ingest_file(p)

    watcher = Watcher(cfg.inbox_dir, _ingest_and_discard)
    watcher.start()


@app.command("import-email")
def import_email(
    path: Path = typer.Argument(..., help="Path to .eml or .mbox file"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Import and classify emails from .eml or .mbox files."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = _get_config(config)

    from lotse.core.engine import Engine
    from lotse.inlets.email import parse_eml, parse_mbox, save_attachments

    engine = Engine(cfg)

    if not path.exists():
        console.print(f"[red]Not found:[/red] {path}")
        raise typer.Exit(1)

    suffix = path.suffix.lower()

    if suffix == ".eml":
        emails = [parse_eml(path)]
    elif suffix == ".mbox":
        emails = parse_mbox(path)
    else:
        console.print(f"[red]Unsupported format:[/red] {suffix} (use .eml or .mbox)")
        raise typer.Exit(1)

    console.print(f"[blue]Processing {len(emails)} email(s)...[/blue]\n")

    for i, parsed in enumerate(emails, 1):
        console.print(f"[dim]#{i}[/dim] {parsed.subject}")

        # Classify the email body
        result = engine.ingest_text(
            parsed.text_for_classification,
            name=f"email:{parsed.subject[:50]}",
        )

        if result.success:
            console.print(f"  [green]✓[/green] {result.message}")
        else:
            console.print(f"  [red]✗[/red] {result.message}")

        # Process attachments
        if parsed.attachments:
            att_dir = cfg.inbox_dir / "email_attachments"
            saved = save_attachments(parsed, att_dir)
            for att_path in saved:
                att_result = engine.ingest_file(att_path)
                if att_result.success:
                    console.print(f"  [green]  ↳[/green] {att_path.name}: {att_result.message}")
                else:
                    console.print(f"  [red]  ↳[/red] {att_path.name}: {att_result.message}")
                # Clean up temp attachment if still present
                att_path.unlink(missing_ok=True)

    console.print(f"\n[green]Done.[/green] Processed {len(emails)} email(s).")


@app.command("fetch-email")
def fetch_email(
    host: str = typer.Option(..., "--host", help="IMAP server (e.g. imap.gmail.com)"),
    username: str = typer.Option(..., "--user", "-u", help="Email address"),
    password: str = typer.Option(
        ..., "--password", "-p", prompt=True, hide_input=True, help="Password/app password"
    ),
    folder: str = typer.Option("INBOX", "--folder", "-f"),
    limit: int = typer.Option(20, "--limit", "-n"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch and classify unread emails from an IMAP server."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = _get_config(config)

    from lotse.core.engine import Engine
    from lotse.inlets.email import fetch_imap, save_attachments

    engine = Engine(cfg)

    console.print(f"[blue]Connecting to {host}...[/blue]")

    try:
        emails = fetch_imap(host, username, password, folder=folder, limit=limit)
    except Exception as e:
        console.print(f"[red]Connection failed:[/red] {e}")
        raise typer.Exit(1) from None

    if not emails:
        console.print("[dim]No unread emails.[/dim]")
        return

    console.print(f"[blue]Processing {len(emails)} email(s)...[/blue]\n")

    for i, parsed in enumerate(emails, 1):
        console.print(f"[dim]#{i}[/dim] {parsed.subject}")

        result = engine.ingest_text(
            parsed.text_for_classification,
            name=f"email:{parsed.subject[:50]}",
        )

        if result.success:
            console.print(f"  [green]✓[/green] {result.message}")
        else:
            console.print(f"  [red]✗[/red] {result.message}")

        if parsed.attachments:
            att_dir = cfg.inbox_dir / "email_attachments"
            saved = save_attachments(parsed, att_dir)
            for att_path in saved:
                att_result = engine.ingest_file(att_path)
                if att_result.success:
                    console.print(f"  [green]  ↳[/green] {att_path.name}: {att_result.message}")
                att_path.unlink(missing_ok=True)

    console.print(f"\n[green]Done.[/green] Processed {len(emails)} email(s).")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query (natural language)"),
    limit: int = typer.Option(20, "--limit", "-n"),
    mode: str = typer.Option(
        "auto", "--mode", "-m",
        help="Search mode: 'auto' (hybrid), 'fts' (keyword), 'vec' (semantic)",
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Search processed items. Uses hybrid keyword + semantic search by default."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = _get_config(config)

    from lotse.core.engine import Engine

    engine = Engine(cfg)
    results = engine.search(query, limit=limit, mode=mode)

    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    # Show search mode info
    if engine.store.vec_enabled and mode in ("auto", "vec"):
        console.print("[dim]Search mode: hybrid (keyword + semantic)[/dim]\n")
    else:
        console.print("[dim]Search mode: keyword (FTS5)[/dim]\n")

    table = Table(title=f"Results for '{query}'")
    table.add_column("ID", style="dim")
    table.add_column("Category", style="cyan")
    table.add_column("Summary")
    table.add_column("Route", style="green")
    table.add_column("Date", style="dim")

    for item in results:
        table.add_row(
            str(item["id"]),
            item["category"],
            (item["summary"] or "")[:60],
            item["route_name"],
            item["created_at"][:10],
        )

    console.print(table)


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Show processing statistics and system status."""
    cfg = _get_config(config)

    from lotse.db.store import Store

    console.print(f"\n[bold]Lotse[/bold] v{__version__}\n")

    # Config info
    console.print(f"[dim]Config:[/dim]  {DEFAULT_CONFIG_FILE}")
    console.print(f"[dim]Database:[/dim] {cfg.database.path}")
    console.print(f"[dim]Inbox:[/dim]   {cfg.inbox_dir}")
    console.print(f"[dim]LLM:[/dim]     {cfg.llm.provider}/{cfg.llm.model}")
    console.print(f"[dim]Routes:[/dim]  {len(cfg.routes)} configured")
    console.print(f"[dim]Embed:[/dim]   {cfg.embeddings.model}")

    from lotse.core.ocr import ocr_available

    ocr = ocr_available()
    if ocr["tesseract_bin"]:
        console.print("[dim]OCR:[/dim]     [green]available[/green] (Tesseract + PyMuPDF)")
    elif ocr["pymupdf"]:
        console.print("[dim]OCR:[/dim]     [yellow]partial[/yellow] (PyMuPDF only, no Tesseract)")
    else:
        console.print("[dim]OCR:[/dim]     [dim]not installed[/dim] (pip install lotse[ocr])")
    console.print()

    if not cfg.database.path.exists():
        console.print("[dim]No items processed yet.[/dim]")
        return

    store = Store(cfg.database.path)
    s = store.stats()

    console.print(f"[bold]Total items:[/bold] {s['total_items']}\n")

    if s.get("vec_enabled"):
        console.print(
            f"[dim]Semantic search:[/dim] [green]enabled[/green]"
            f" ({s.get('embeddings', 0)} embeddings)\n"
        )
    else:
        console.print(
            "[dim]Semantic search:[/dim] [yellow]disabled[/yellow]"
            " (pip install sqlite-vec)\n"
        )

    if s["categories"]:
        cat_table = Table(title="Categories")
        cat_table.add_column("Category", style="cyan")
        cat_table.add_column("Count", justify="right")
        for cat, count in s["categories"].items():
            cat_table.add_row(cat, str(count))
        console.print(cat_table)


@app.command()
def init(
    config: Path | None = typer.Option(None, "--config", "-c"),
    quick: bool = typer.Option(False, "--quick", "-q", help="Skip wizard, use defaults"),
) -> None:
    """Initialize Lotse — interactive setup wizard."""
    path = config or DEFAULT_CONFIG_FILE
    if path.exists():
        console.print(f"[yellow]Config already exists:[/yellow] {path}")
        console.print("[dim]Delete it first or edit manually.[/dim]")
        raise typer.Exit(1)

    if quick:
        # Quick mode: write defaults without wizard
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        default_config = (
            '# Lotse Configuration\n'
            '# https://github.com/HerrStolzier/lotse\n\n'
            '[llm]\nprovider = "ollama"\nmodel = "qwen3.5:4b"\n'
            'base_url = "http://localhost:11434"\ntemperature = 0.1\n\n'
            '[embeddings]\nmodel = "BAAI/bge-small-en-v1.5"\n\n'
            '[database]\npath = "~/.local/share/lotse/lotse.db"\n\n'
            '[routes.archiv]\ntype = "folder"\n'
            'path = "~/Documents/Lotse/Archiv"\n'
            'categories = ["rechnung", "vertrag", "brief", "bescheid"]\n'
            'confidence_threshold = 0.7\n\n'
            '[routes.artikel]\ntype = "folder"\n'
            'path = "~/Documents/Lotse/Artikel"\n'
            'categories = ["artikel", "paper", "tutorial", "dokumentation"]\n'
            'confidence_threshold = 0.6\n\n'
            '[routes.code]\ntype = "folder"\n'
            'path = "~/Documents/Lotse/Code"\n'
            'categories = ["code", "config", "script"]\n'
            'confidence_threshold = 0.6\n'
        )
        path.write_text(default_config)
        console.print(f"[green]✓[/green] Config created: {path}")
        return

    from lotse.setup_wizard import run_wizard

    success = run_wizard()
    if not success:
        console.print("[red]Setup cancelled.[/red]")
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Check system health and LLM availability."""
    from lotse.setup_wizard import _check_system, _print_system_info

    sys_info = _check_system()
    _print_system_info(sys_info)

    # Check config
    if DEFAULT_CONFIG_FILE.exists():
        console.print(f"[green]Config:[/green] {DEFAULT_CONFIG_FILE}")
        cfg = _get_config()

        # Check LLM connectivity
        console.print(f"[dim]LLM:[/dim] {cfg.llm.provider}/{cfg.llm.model}")
        if cfg.llm.provider == "ollama":
            if sys_info["ollama_running"]:
                if cfg.llm.model in [m.split(":")[0] for m in sys_info["ollama_models"]]:
                    console.print(f"[green]Model '{cfg.llm.model}' available.[/green]")
                else:
                    available = ", ".join(sys_info["ollama_models"][:5]) or "none"
                    console.print(
                        f"[red]Model '{cfg.llm.model}' not found.[/red]\n"
                        f"[dim]Available: {available}[/dim]\n"
                        f"[dim]Pull it: ollama pull {cfg.llm.model}[/dim]"
                    )
            else:
                console.print("[red]Ollama not running.[/red] Start with: ollama serve")
        else:
            console.print("[dim](Cloud provider — API key must be set as env var)[/dim]")
    else:
        console.print("[yellow]No config found.[/yellow] Run: lotse init")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    port: int = typer.Option(8790, "--port", "-p"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the REST API server (requires: pip install lotse[api])."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    try:
        import uvicorn
    except ImportError:
        console.print("[red]Missing dependency.[/red] Install with:")
        console.print("  pip install lotse[api]")
        raise typer.Exit(1) from None

    if host != "127.0.0.1":
        console.print(
            f"\n[yellow bold]Security Warning:[/yellow bold] Binding to "
            f"[bold]{host}[/bold] exposes the API to your network.\n"
            "[yellow]All endpoints are unauthenticated. Anyone on your "
            "network can search, upload, and read your classified documents."
            "[/yellow]\n"
        )

    cfg = _get_config(config)

    from lotse.inlets.api import create_app

    api = create_app(cfg)

    console.print(f"\n[bold]Lotse API[/bold] v{__version__}")
    console.print(f"[dim]Docs:[/dim]    http://{host}:{port}/docs")
    console.print(f"[dim]Health:[/dim]  http://{host}:{port}/health\n")

    uvicorn.run(api, host=host, port=port, log_level="info")


@app.command()
def plugins() -> None:
    """List installed plugins."""
    from lotse.plugins.manager import PluginManager

    pm = PluginManager()
    plugin_list = pm.list_plugins()

    if not plugin_list:
        console.print("[dim]No plugins installed.[/dim]")
        console.print("[dim]Install plugins with: pip install lotse-<plugin-name>[/dim]")
        return

    for name in plugin_list:
        console.print(f"  [green]●[/green] {name}")


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", help="Show version"),
) -> None:
    """Lotse — your AI-powered data pilot."""
    if version:
        console.print(f"lotse {__version__}")
        raise typer.Exit()
