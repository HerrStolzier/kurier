"""Setup-related CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from arkiv.commands.common import DEFAULT_CONFIG_FILE, ArkivConfig, console


def _pick_folder(default: str) -> str:
    """Open a native folder picker, with terminal fallback."""
    import platform
    import subprocess

    system = platform.system()
    picked: str | None = None

    console.print("\n[bold]Wo soll der Kurier-Eingang sein?[/bold]")
    console.print(f"[dim]Standard: {default}[/dim]\n")

    if system == "Darwin":
        try:
            console.print("[dim]Öffne Ordner-Auswahl...[/dim]")
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Kurier Eingangs-Ordner wählen")',
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                picked = result.stdout.strip().rstrip("/")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    elif system == "Linux":
        for cmd in (
            ["zenity", "--file-selection", "--directory", "--title=Kurier Eingangs-Ordner wählen"],
            [
                "kdialog",
                "--getexistingdirectory",
                str(Path.home()),
                "--title",
                "Kurier Eingangs-Ordner wählen",
            ],
        ):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0 and result.stdout.strip():
                    picked = result.stdout.strip()
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
    elif system == "Windows":
        try:
            ps_cmd = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$f.Description = 'Kurier Eingangs-Ordner wählen';"
                "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                picked = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if picked:
        console.print(f"[green]✓[/green] Gewählt: {picked}")
        return picked

    fallback: str = typer.prompt(
        "Eingangs-Ordner (Pfad eingeben)",
        default=default,
    )
    return fallback


def init(
    config: Path | None = typer.Option(None, "--config", "-c"),
    quick: bool = typer.Option(False, "--quick", "-q", help="Skip wizard, use defaults"),
) -> None:
    """Kurier einrichten — mit Ordnerauswahl und kurzen Startprüfungen."""
    path = config or DEFAULT_CONFIG_FILE
    if path.exists():
        console.print(f"[yellow]Kurier ist hier schon eingerichtet:[/yellow] {path}")
        console.print("[dim]Wenn du neu starten willst, benenne diese Datei vorher um.[/dim]")
        raise typer.Exit(1)

    default_inbox = str(Path.home() / "Documents" / "Kurier" / "Eingang")
    inbox_dir = default_inbox if quick else _pick_folder(default_inbox)

    inbox_path = Path(inbox_dir).expanduser()
    inbox_path.mkdir(parents=True, exist_ok=True)

    base_dir = inbox_path.parent
    review_dir = base_dir / "Prüfen"
    review_dir.mkdir(parents=True, exist_ok=True)

    # path.parent statt DEFAULT_CONFIG_DIR: deckt auch --config in ein
    # noch nicht existierendes Verzeichnis ab (Default-Fall ist identisch).
    path.parent.mkdir(parents=True, exist_ok=True)
    default_config = (
        "# Kurier Einstellungen\n"
        "# https://github.com/HerrStolzier/kurier\n\n"
        '[llm]\nprovider = "ollama"\nmodel = "qwen2.5:7b"\n'
        'base_url = "http://localhost:11434"\ntemperature = 0.1\n\n'
        '[embeddings]\nmodel = "BAAI/bge-small-en-v1.5"\n\n'
        '[database]\npath = "~/.local/share/kurier/kurier.db"\n\n'
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
        "confidence_threshold = 0.6\n\n"
        '[routes.notizen]\ntype = "folder"\n'
        f'path = "{base_dir}/Notizen"\n'
        'categories = ["notiz"]\n'
        "confidence_threshold = 0.5\n"
    )
    path.write_text(default_config)
    console.print(f"\n[green]✓[/green] Einstellungen gespeichert: {path}")
    console.print(f"[green]✓[/green] Eingangs-Ordner: {inbox_path}")

    for route_name in ["Archiv", "Artikel", "Code", "Notizen"]:
        route_dir = base_dir / route_name
        route_dir.mkdir(parents=True, exist_ok=True)

    if quick:
        console.print("[yellow]Auto-Sortierung ist noch aus.[/yellow]")
        console.print("[dim]Starte sie, sobald alles passt: kurier service on[/dim]")
    else:
        _post_init_checks(path)


def _post_init_checks(config_path: Path) -> None:
    """Run post-init checks: Ollama, route dirs, test classification."""
    import urllib.request

    console.print()

    try:
        cfg = ArkivConfig.load(config_path)
    except Exception:
        return

    for _name, route in cfg.routes.items():
        if route.path:
            route_path = Path(route.path).expanduser()
            route_path.mkdir(parents=True, exist_ok=True)
            console.print(f"[dim]Ablage-Ordner vorbereitet:[/dim] {route_path}")

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
        console.print(f"[green]✓[/green] Lokale KI ist erreichbar ({ollama_url})")
        if models:
            console.print(f"[dim]Gefundene KI-Modelle:[/dim] {', '.join(models[:5])}")
        else:
            console.print(
                "[yellow]Noch kein KI-Modell heruntergeladen.[/yellow] "
                "Empfehlung: ollama pull qwen2.5:7b"
            )

        console.print("\n[dim]Teste kurz, ob Kurier ein Beispieldokument versteht...[/dim]")
        try:
            from arkiv.core.engine import Engine

            engine = Engine(cfg)
            sample = "Dies ist eine Rechnung über 42,00 EUR von der Stadtwerke GmbH."
            result = engine.ingest_text(sample, name="init-test")
            if result.success:
                console.print(f"[green]✓[/green] Test erfolgreich: {result.message}")
            else:
                console.print(f"[yellow]Test braucht Aufmerksamkeit:[/yellow] {result.message}")
        except Exception as e:
            console.print(f"[yellow]Test konnte nicht abgeschlossen werden:[/yellow] {e}")
    else:
        console.print(
            "[yellow]Lokale KI nicht gefunden.[/yellow] "
            "Installiere Ollama von [link]https://ollama.com[/link], "
            "wenn Kurier lokal klassifizieren soll."
        )


def register(app: typer.Typer) -> None:
    """Register setup commands."""
    app.command()(init)
