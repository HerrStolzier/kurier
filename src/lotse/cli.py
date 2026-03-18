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

    watcher = Watcher(cfg.inbox_dir, engine.ingest_file)
    watcher.start()


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
    console.print(f"[dim]Embed:[/dim]   {cfg.embeddings.model}\n")

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
) -> None:
    """Initialize Lotse with a default configuration."""
    path = config or DEFAULT_CONFIG_FILE
    if path.exists():
        console.print(f"[yellow]Config already exists:[/yellow] {path}")
        raise typer.Exit(1)

    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    default_config = '''\
# Lotse Configuration
# https://github.com/HerrStolzier/lotse

[llm]
provider = "ollama"
model = "mistral"
base_url = "http://localhost:11434"
temperature = 0.1

[embeddings]
model = "BAAI/bge-small-en-v1.5"

[database]
path = "~/.local/share/lotse/lotse.db"

# Define your routes below.
# Each route matches categories from LLM classification.

[routes.archiv]
type = "folder"
path = "~/Documents/Lotse/Archiv"
categories = ["rechnung", "vertrag", "brief", "bescheid"]
confidence_threshold = 0.7

[routes.artikel]
type = "folder"
path = "~/Documents/Lotse/Artikel"
categories = ["artikel", "paper", "tutorial", "dokumentation"]
confidence_threshold = 0.6

[routes.code]
type = "folder"
path = "~/Documents/Lotse/Code"
categories = ["code", "config", "script"]
confidence_threshold = 0.6
'''
    path.write_text(default_config)
    console.print(f"[green]✓[/green] Config created: {path}")
    console.print("[dim]Edit the config to customize your routes.[/dim]")


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
