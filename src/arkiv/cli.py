"""Arkiv CLI — your AI-powered data pilot."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from arkiv.core.auditor import AuditReport

import typer
from rich.console import Console
from rich.table import Table

from arkiv import __version__
from arkiv.core.config import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE, ArkivConfig

app = typer.Typer(
    name="arkiv",
    help="Universal capture → classify → route. Your AI-powered data pilot.",
    no_args_is_help=False,
)
console = Console()


def _get_config(config: Path | None = None) -> ArkivConfig:
    cfg = ArkivConfig.load(config)
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

    from arkiv.core.engine import Engine

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

    from arkiv.core.engine import Engine
    from arkiv.inlets.watch import Watcher

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

    from arkiv.core.engine import Engine
    from arkiv.inlets.email import parse_eml, parse_mbox, save_attachments

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

    from arkiv.core.engine import Engine
    from arkiv.inlets.email import fetch_imap, save_attachments

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
        "auto",
        "--mode",
        "-m",
        help="Search mode: 'auto' (hybrid), 'fts' (keyword), 'vec' (semantic)",
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Search processed items. Uses hybrid keyword + semantic search by default."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = _get_config(config)

    from arkiv.core.engine import Engine

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

    from arkiv.db.store import Store

    console.print(f"\n[bold]Arkiv[/bold] v{__version__}\n")

    # Config info
    config_display = config or DEFAULT_CONFIG_FILE
    console.print(f"[dim]Config:[/dim]  {config_display}")
    console.print(f"[dim]Database:[/dim] {cfg.database.path}")
    console.print(f"[dim]Inbox:[/dim]   {cfg.inbox_dir}")
    console.print(f"[dim]LLM:[/dim]     {cfg.llm.provider}/{cfg.llm.model}")
    console.print(f"[dim]Routes:[/dim]  {len(cfg.routes)} configured")
    console.print(f"[dim]Embed:[/dim]   {cfg.embeddings.model}")

    from arkiv.core.ocr import ocr_available

    ocr = ocr_available()
    if ocr["tesseract_bin"]:
        console.print("[dim]OCR:[/dim]     [green]available[/green] (Tesseract + PyMuPDF)")
    elif ocr["pymupdf"]:
        console.print("[dim]OCR:[/dim]     [yellow]partial[/yellow] (PyMuPDF only, no Tesseract)")
    else:
        console.print("[dim]OCR:[/dim]     [dim]not installed[/dim] (pip install arkiv[ocr])")
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
            "[dim]Semantic search:[/dim] [yellow]disabled[/yellow] (pip install sqlite-vec)\n"
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
    """Initialize Arkiv — interactive setup wizard."""
    path = config or DEFAULT_CONFIG_FILE
    if path.exists():
        console.print(f"[yellow]Config already exists:[/yellow] {path}")
        console.print("[dim]Delete it first or edit manually.[/dim]")
        raise typer.Exit(1)

    # Ask for inbox directory
    default_inbox = str(Path.home() / "Documents" / "Kurier" / "Eingang")
    if quick:
        inbox_dir = default_inbox
    else:
        inbox_dir = typer.prompt(
            "Eingangs-Ordner (hier Dateien ablegen zum Sortieren)",
            default=default_inbox,
        )

    inbox_path = Path(inbox_dir).expanduser()
    inbox_path.mkdir(parents=True, exist_ok=True)

    base_dir = inbox_path.parent  # ~/Documents/Kurier/
    review_dir = base_dir / "Prüfen"
    review_dir.mkdir(parents=True, exist_ok=True)

    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    default_config = (
        "# Kurier Configuration\n"
        "# https://github.com/HerrStolzier/lotse\n\n"
        '[llm]\nprovider = "ollama"\nmodel = "qwen2.5:7b"\n'
        'base_url = "http://localhost:11434"\ntemperature = 0.1\n\n'
        '[embeddings]\nmodel = "BAAI/bge-small-en-v1.5"\n\n'
        '[database]\npath = "~/.local/share/arkiv/arkiv.db"\n\n'
        f'inbox_dir = "{inbox_dir}"\n'
        f'review_dir = "{review_dir}"\n\n'
        '[routes.archiv]\ntype = "folder"\n'
        f'path = "{base_dir}/Archiv"\n'
        'categories = ["rechnung", "vertrag", "brief", "bescheid"]\n'
        "confidence_threshold = 0.7\n\n"
        '[routes.artikel]\ntype = "folder"\n'
        f'path = "{base_dir}/Artikel"\n'
        'categories = ["artikel", "paper", "tutorial", "dokumentation"]\n'
        "confidence_threshold = 0.6\n\n"
        '[routes.code]\ntype = "folder"\n'
        f'path = "{base_dir}/Code"\n'
        'categories = ["code", "config", "script"]\n'
        "confidence_threshold = 0.6\n"
    )
    path.write_text(default_config)
    console.print(f"\n[green]✓[/green] Config erstellt: {path}")
    console.print(f"[green]✓[/green] Eingangs-Ordner: {inbox_path}")

    if not quick:
        _post_init_checks(path)
    return

    _post_init_checks(path)


def _post_init_checks(config_path: Path) -> None:
    """Run post-init checks: Ollama, route dirs, test classification."""
    import urllib.error
    import urllib.request

    console.print()

    # Load config for route dirs and LLM settings
    try:
        cfg = ArkivConfig.load(config_path)
    except Exception:
        return

    # Create route directories
    for _name, route in cfg.routes.items():
        if route.path:
            route_path = Path(route.path).expanduser()
            route_path.mkdir(parents=True, exist_ok=True)
            console.print(f"[dim]Route-Verzeichnis erstellt:[/dim] {route_path}")

    # Check Ollama
    ollama_url = (cfg.llm.base_url or "http://localhost:11434").rstrip("/")
    ollama_running = False
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            ollama_running = True
    except Exception:
        models = []

    if ollama_running:
        console.print(f"[green]✓[/green] Ollama läuft ({ollama_url})")
        if models:
            console.print(f"[dim]Verfügbare Modelle:[/dim] {', '.join(models[:5])}")
        else:
            console.print(
                "[yellow]Keine Modelle gefunden. Lade eines: ollama pull qwen2.5:7b[/yellow]"
            )

        # Test classification with sample text
        console.print("\n[dim]Teste Klassifikation...[/dim]")
        try:
            from arkiv.core.engine import Engine

            engine = Engine(cfg)
            sample = "Dies ist eine Rechnung über 42,00 EUR von der Stadtwerke GmbH."
            result = engine.ingest_text(sample, name="init-test")
            if result.success:
                console.print(f"[green]✓[/green] Test-Klassifikation: {result.message}")
            else:
                console.print(f"[yellow]Test-Klassifikation:[/yellow] {result.message}")
        except Exception as e:
            console.print(f"[yellow]Test-Klassifikation fehlgeschlagen:[/yellow] {e}")
    else:
        console.print(
            "[yellow]Ollama nicht gefunden.[/yellow] "
            "Installiere es von [link]https://ollama.com[/link]"
        )


@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Systemzustand prüfen — Config, Routen, LLM, Datenbank."""
    import tomllib
    import urllib.error
    import urllib.request

    config_path = config or DEFAULT_CONFIG_FILE

    check_table = Table(title="Arkiv Doctor", show_header=True, border_style="dim")
    check_table.add_column("Status", width=4)
    check_table.add_column("Prüfung")
    check_table.add_column("Details", style="dim")

    def ok(label: str, detail: str = "") -> None:
        check_table.add_row("[green]✓[/green]", label, detail)

    def warn(label: str, detail: str = "") -> None:
        check_table.add_row("[yellow]![/yellow]", label, detail)

    def fail(label: str, detail: str = "") -> None:
        check_table.add_row("[red]✗[/red]", label, detail)

    # Check 1: Config file exists and is valid TOML
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                tomllib.load(f)
            ok("Config-Datei", str(config_path))
            cfg_valid = True
        except Exception as e:
            fail("Config-Datei", f"Ungültiges TOML: {e}")
            cfg_valid = False
    else:
        fail("Config-Datei", f"Nicht gefunden: {config_path}")
        cfg_valid = False

    cfg = None
    if cfg_valid:
        try:
            cfg = ArkivConfig.load(config_path)
        except Exception as e:
            fail("Config laden", str(e))
            cfg_valid = False

    # Check 2: Route paths exist
    if cfg is not None:
        for name, route in cfg.routes.items():
            if route.path:
                rp = Path(route.path).expanduser()
                if rp.exists():
                    ok(f"Route '{name}'", str(rp))
                else:
                    warn(f"Route '{name}'", f"Verzeichnis fehlt: {rp}  (arkiv init erstellt es)")

    # Check 3: LLM reachable
    if cfg is not None:
        if cfg.llm.provider == "ollama":
            ollama_url = (cfg.llm.base_url or "http://localhost:11434").rstrip("/")
            try:
                with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3) as resp:
                    data = json.loads(resp.read())
                    models = [m["name"] for m in data.get("models", [])]
                model_names_base = [m.split(":")[0] for m in models]
                if cfg.llm.model in models or cfg.llm.model in model_names_base:
                    ok("LLM erreichbar", f"{cfg.llm.provider}/{cfg.llm.model}")
                else:
                    available = ", ".join(models[:3]) or "-"
                    warn(
                        "LLM-Modell fehlt",
                        f"'{cfg.llm.model}' nicht gefunden. Verfügbar: {available}",
                    )
            except Exception as e:
                fail("LLM erreichbar", f"Ollama nicht erreichbar: {e}")
        else:
            ok("LLM", f"{cfg.llm.provider} (API-Key via Env-Var)")

    # Check 4: Custom categories have descriptions (if defined)
    if cfg is not None and cfg.categories:
        empty_cats = [name for name, desc in cfg.categories.items() if not desc or not desc.strip()]
        if empty_cats:
            warn("Kategorie-Beschreibungen", f"Leer bei: {', '.join(empty_cats)}")
        else:
            ok("Kategorie-Beschreibungen", "Alle vorhanden")

    # Check 5: Pending/failed items in DB
    if cfg is not None and cfg.database.path.exists():
        try:
            from arkiv.db.store import Store

            store = Store(cfg.database.path)
            all_items = store.get_all_items()
            pending = sum(1 for it in all_items if it.get("status") == "pending")
            failed = sum(1 for it in all_items if it.get("status") == "failed")
            if pending or failed:
                warn(
                    "DB-Status",
                    f"{pending} ausstehend, {failed} fehlgeschlagen (von {len(all_items)} gesamt)",
                )
            else:
                ok("DB-Status", f"{len(all_items)} Einträge, keine Fehler")
        except Exception as e:
            warn("Datenbank", str(e))
    elif cfg is not None:
        ok("Datenbank", "Noch leer (kein Element verarbeitet)")

    console.print(check_table)


@app.command()
def audit(
    fix: bool = typer.Option(False, "--fix", help="Interactive fix mode"),
    skip_reclassify: bool = typer.Option(
        False, "--skip-reclassify", help="Skip LLM re-classification (faster)"
    ),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Audit routing decisions — find duplicates, errors, and orphaned files."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = _get_config(config)

    if not cfg.database.path.exists():
        console.print("[dim]No items to audit yet.[/dim]")
        return

    from arkiv.core.auditor import Auditor

    console.print("[blue]Running audit...[/blue]\n")

    auditor = Auditor(cfg)
    report = auditor.run_full_audit(
        check_misclassified=not skip_reclassify,
    )

    # Summary
    console.print(f"[bold]Audit Report[/bold]  ({report.items_checked} items checked)\n")

    if not report.has_issues:
        console.print("[green]No issues found. Everything looks good.[/green]")
        return

    # Group by severity
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

    # Detail table
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

    # Interactive fix mode
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
                    "[dim]  (DB updated. File was already moved by "
                    "original routing — manual move may be needed.)[/dim]"
                )
                fixed += 1
            elif answer == "skip all":
                break

    console.print(f"\n[green]Done.[/green] Fixed {fixed} issue(s).")


def _fix_reclassify_orphan(cfg: ArkivConfig, message: str) -> bool:
    """Re-classify an orphaned file from the review directory."""
    from arkiv.core.engine import Engine

    # Extract filename from message: "Unreviewed file: filename.pdf"
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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    port: int = typer.Option(8790, "--port", "-p"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the REST API server (requires: pip install arkiv[api])."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    try:
        import uvicorn
    except ImportError:
        console.print("[red]Missing dependency.[/red] Install with:")
        console.print("  pip install arkiv[api]")
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

    from arkiv.inlets.api import create_app

    api = create_app(cfg)

    console.print(f"\n[bold]Arkiv API[/bold] v{__version__}")
    console.print(f"[dim]Docs:[/dim]    http://{host}:{port}/docs")
    console.print(f"[dim]Health:[/dim]  http://{host}:{port}/health\n")

    uvicorn.run(api, host=host, port=port, log_level="info")


@app.command()
def plugins() -> None:
    """List installed plugins."""
    from arkiv.plugins.manager import PluginManager

    pm = PluginManager()
    plugin_list = pm.list_plugins()

    if not plugin_list:
        console.print("[dim]No plugins installed.[/dim]")
        console.print("[dim]Install plugins with: pip install arkiv-<plugin-name>[/dim]")
        return

    for name in plugin_list:
        console.print(f"  [green]●[/green] {name}")


@app.command()
def undo(
    item_id: Annotated[int | None, typer.Option("--id", help="Specific item ID to undo")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Routing einer Datei rückgängig machen — verschiebt sie zurück zum Ursprungsort."""
    from arkiv.db.store import Store

    cfg = _get_config(config)

    if not cfg.database.path.exists():
        console.print("[dim]Keine verarbeiteten Einträge gefunden.[/dim]")
        raise typer.Exit(1)

    store = Store(cfg.database.path)

    if item_id is not None:
        item = store.undo_item(item_id)
        if item is None:
            console.print(f"[red]Kein Eintrag mit ID {item_id} gefunden.[/red]")
            raise typer.Exit(1)
        items = [item]
    else:
        recent = store.get_recent(limit=1)
        if not recent:
            console.print("[dim]Keine Einträge vorhanden.[/dim]")
            raise typer.Exit(1)
        item = store.undo_item(recent[0]["id"])
        if item is None:
            console.print("[red]Letzten Eintrag konnte nicht geladen werden.[/red]")
            raise typer.Exit(1)
        items = [item]

    for it in items:
        iid = it["id"]
        dest = Path(it["destination"]) if it["destination"] else None
        orig = Path(it["original_path"])

        if dest is None or not dest.exists():
            console.print(f"[yellow]Datei nicht mehr vorhanden:[/yellow] {dest}")
            store.update_status(iid, "undone")
            console.print(f"[dim]Status auf 'undone' gesetzt (ID {iid}).[/dim]")
            continue

        if orig.exists():
            console.print(f"[yellow]Zielpfad bereits belegt:[/yellow] {orig}")
            console.print("[dim]Datei nicht verschoben. Status bleibt unverändert.[/dim]")
            continue

        orig.parent.mkdir(parents=True, exist_ok=True)
        dest.rename(orig)
        store.update_status(iid, "undone")
        console.print(f"[green]✓[/green] Zurückverschoben: {dest.name} → {orig}")


@app.command()
def export(
    format: Annotated[str, typer.Option("--format", "-f", help="Output format")] = "csv",
    category: Annotated[str | None, typer.Option("--category", "-c")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Verarbeitete Einträge als CSV oder JSON exportieren."""
    from arkiv.db.store import Store

    cfg = _get_config(config)

    if not cfg.database.path.exists():
        console.print("[dim]Keine verarbeiteten Einträge gefunden.[/dim]")
        raise typer.Exit(1)

    store = Store(cfg.database.path)
    items = store.get_all_items(category=category)

    if not items:
        console.print("[dim]Keine Einträge gefunden.[/dim]")
        return

    fields = [
        "id",
        "category",
        "confidence",
        "original_path",
        "destination",
        "created_at",
        "status",
    ]

    if format.lower() == "json":
        rows = [{f: item.get(f) for f in fields} for item in items]
        data = json.dumps(rows, indent=2, ensure_ascii=False)
        if output:
            output.write_text(data, encoding="utf-8")
            console.print(f"[green]✓[/green] {len(rows)} Einträge exportiert nach {output}")
        else:
            console.print(data)
    elif format.lower() == "csv":
        import io
        import sys

        if output:
            f_out = output.open("w", newline="", encoding="utf-8")
        else:
            f_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")  # type: ignore[assignment]

        try:
            writer = csv.DictWriter(f_out, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for item in items:
                writer.writerow({f: item.get(f) for f in fields})
        finally:
            if output:
                f_out.close()

        if output:
            console.print(f"[green]✓[/green] {len(items)} Einträge exportiert nach {output}")
    else:
        console.print(f"[red]Unbekanntes Format:[/red] {format} (verwende 'csv' oder 'json')")
        raise typer.Exit(1)


@app.command()
def tui(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Interaktive Terminal-Oberfläche starten."""
    try:
        from arkiv.tui.app import ArkivApp
    except ImportError:
        console.print("[red]Textual nicht installiert.[/red]")
        console.print("Installiere mit: pip install 'arkiv[tui]'")
        raise typer.Exit(1) from None
    cfg = _get_config(config)
    arkiv_app = ArkivApp(cfg)
    arkiv_app.run()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Show version"),
) -> None:
    """Arkiv — your AI-powered data pilot."""
    if version:
        console.print(f"arkiv {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        # Kein Subcommand → TUI starten
        from arkiv.tui.app import ArkivApp

        cfg = _get_config(None)
        ArkivApp(cfg).run()
