"""Interactive setup wizard — guides first-time users through configuration."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lotse.core.config import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE

logger = logging.getLogger(__name__)
console = Console()

# Model recommendations based on available RAM.
# Qwen 2.5 is preferred over 3.5 for classification tasks:
# - Qwen 3.5 has "thinking mode" which adds ~100s overhead per call
# - Qwen 2.5 is direct, fast, and excellent at structured JSON output
# - Models below 7B tend to misclassify (everything → same category)
# - Minimum 7B recommended for reliable German/English classification
MODEL_RECOMMENDATIONS = [
    # (min_ram_gb, model_id, display_name, size_note)
    (16, "qwen2.5:14b", "Qwen 2.5 14B", "~9 GB, best quality"),
    (8, "qwen2.5:7b", "Qwen 2.5 7B", "~4.7 GB, recommended default"),
    (4, "qwen2.5:3b", "Qwen 2.5 3B", "~2 GB, fast but less accurate"),
    (4, "qwen2.5:1.5b", "Qwen 2.5 1.5B", "~1 GB, minimal (may misclassify)"),
]


def run_wizard() -> bool:
    """Run the interactive setup wizard. Returns True if setup completed."""
    console.print(
        Panel(
            "[bold]Lotse Setup Wizard[/bold]\n\n"
            "This will check your system, configure an LLM backend,\n"
            "and set up your first routes.",
            border_style="blue",
        )
    )

    # Step 1: System check
    sys_info = _check_system()
    _print_system_info(sys_info)

    if sys_info["ram_gb"] < 4:
        console.print(
            "\n[yellow]Warning:[/yellow] Less than 4 GB RAM detected. "
            "Local LLM models may not run well.\n"
            "Consider using a cloud provider (OpenAI, Anthropic) instead."
        )

    if not sys_info["tesseract_installed"]:
        system = platform.system()
        install_cmd = (
            "brew install tesseract"
            if system == "Darwin"
            else "sudo apt install tesseract-ocr"
            if system == "Linux"
            else "https://github.com/tesseract-ocr/tesseract#installing-tesseract"
        )
        console.print(
            "\n[yellow]Tesseract (OCR) is not installed.[/yellow]\n"
            "Without it, Lotse cannot extract text from scanned PDFs or images.\n"
            f"Install with: [bold]{install_cmd}[/bold]\n"
        )

    # Step 2: LLM backend selection
    llm_config = _configure_llm(sys_info)
    if llm_config is None:
        return False

    # Step 3: Route configuration
    routes = _configure_routes()

    # Step 4: Write config
    _write_config(llm_config, routes)

    # Step 5: Test classification
    _run_test(llm_config)

    console.print(
        Panel(
            "[green bold]Setup complete![/green bold]\n\n"
            f"Config: {DEFAULT_CONFIG_FILE}\n"
            "Start with: [bold]lotse add <file>[/bold]\n"
            "Or watch a folder: [bold]lotse watch[/bold]\n"
            "Web dashboard: [bold]lotse serve[/bold]",
            border_style="green",
        )
    )
    return True


# --- System Detection ---


def _check_system() -> dict[str, Any]:
    """Detect OS, RAM, and available tools."""

    info: dict[str, Any] = {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "ram_gb": 0,
        "ollama_installed": False,
        "ollama_running": False,
        "ollama_models": [],
        "tesseract_installed": False,
        "python_version": platform.python_version(),
    }

    # RAM detection
    info["ram_gb"] = _detect_ram()

    # Ollama check
    info["ollama_installed"] = shutil.which("ollama") is not None
    if info["ollama_installed"]:
        info["ollama_running"] = _check_ollama_running()
        if info["ollama_running"]:
            info["ollama_models"] = _list_ollama_models()

    # Tesseract check
    info["tesseract_installed"] = shutil.which("tesseract") is not None

    return info


def _detect_ram() -> int:
    """Detect total system RAM in GB."""
    system = platform.system()

    try:
        if system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return int(result.stdout.strip()) // (1024**3)

        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // (1024 * 1024)

        elif system == "Windows":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            c_ulong = ctypes.c_ulong

            class MEMORYSTATUS(ctypes.Structure):
                _fields_ = [  # noqa: RUF012
                    ("dwLength", c_ulong),
                    ("dwMemoryLoad", c_ulong),
                    ("dwTotalPhys", ctypes.c_uint64),
                    ("dwAvailPhys", ctypes.c_uint64),
                    ("dwTotalPageFile", ctypes.c_uint64),
                    ("dwAvailPageFile", ctypes.c_uint64),
                    ("dwTotalVirtual", ctypes.c_uint64),
                    ("dwAvailVirtual", ctypes.c_uint64),
                    ("dwAvailExtendedVirtual", ctypes.c_uint64),
                ]

            mem = MEMORYSTATUS()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUS)
            kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return int(mem.dwTotalPhys) // (1024**3)

    except Exception as e:
        logger.debug("RAM detection failed: %s", e)

    return 0


def _check_ollama_running() -> bool:
    """Check if Ollama server is responding."""
    try:
        import urllib.request

        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


def _list_ollama_models() -> list[str]:
    """Get list of locally available Ollama models."""
    try:
        import json
        import urllib.request

        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _print_system_info(info: dict[str, Any]) -> None:
    """Display system info in a nice table."""
    table = Table(title="System Check", show_header=False, border_style="blue")
    table.add_column("Property", style="dim")
    table.add_column("Value")

    table.add_row("OS", f"{info['os']} ({info['arch']})")
    table.add_row("Python", info["python_version"])

    ram = info["ram_gb"]
    ram_style = "green" if ram >= 16 else "yellow" if ram >= 8 else "red"
    table.add_row("RAM", f"[{ram_style}]{ram} GB[/{ram_style}]")

    ollama_status = (
        "[green]running[/green]"
        if info["ollama_running"]
        else "[yellow]installed but not running[/yellow]"
        if info["ollama_installed"]
        else "[red]not installed[/red]"
    )
    table.add_row("Ollama", ollama_status)

    if info["ollama_models"]:
        table.add_row("Models", ", ".join(info["ollama_models"][:5]))

    tesseract = (
        "[green]installed[/green]" if info["tesseract_installed"] else "[dim]not installed[/dim]"
    )
    table.add_row("Tesseract (OCR)", tesseract)

    console.print()
    console.print(table)
    console.print()


# --- LLM Configuration ---


def _configure_llm(sys_info: dict[str, Any]) -> dict[str, Any] | None:
    """Guide user through LLM backend selection."""
    console.print("[bold]Step 1: LLM Backend[/bold]\n")

    # If Ollama is running with models, offer those first
    if sys_info["ollama_running"] and sys_info["ollama_models"]:
        return _configure_ollama_existing(sys_info)

    # If Ollama is installed but not running
    if sys_info["ollama_installed"] and not sys_info["ollama_running"]:
        console.print(
            "[yellow]Ollama is installed but not running.[/yellow]\n"
            "Start it with: [bold]ollama serve[/bold]\n"
        )

    # Show all options
    console.print("Choose your LLM backend:\n")
    console.print("  [bold]1.[/bold] Ollama (local, free, private)")
    console.print("  [bold]2.[/bold] OpenAI (cloud, paid, fast)")
    console.print("  [bold]3.[/bold] Anthropic (cloud, paid, high quality)")
    console.print("  [bold]4.[/bold] Skip for now (configure later)\n")

    choice = console.input("[bold]Your choice [1-4]:[/bold] ").strip()

    if choice == "1":
        return _configure_ollama_new(sys_info)
    elif choice == "2":
        return _configure_cloud("openai", "gpt-4o-mini")
    elif choice == "3":
        return _configure_cloud("anthropic", "claude-sonnet-4-5-20250514")
    elif choice == "4":
        console.print("[dim]Skipped. Edit config later at: {DEFAULT_CONFIG_FILE}[/dim]")
        return {"provider": "ollama", "model": "mistral", "base_url": "http://localhost:11434"}
    else:
        console.print("[red]Invalid choice.[/red]")
        return None


def _configure_ollama_existing(sys_info: dict[str, Any]) -> dict[str, Any]:
    """Configure Ollama when models are already available."""
    models = sys_info["ollama_models"]

    console.print(f"[green]Ollama is running with {len(models)} model(s).[/green]\n")

    for i, model in enumerate(models[:8], 1):
        console.print(f"  [bold]{i}.[/bold] {model}")

    console.print(f"  [bold]{len(models[:8]) + 1}.[/bold] Pull a new model")

    choice = console.input(f"\n[bold]Choose model [1-{len(models[:8]) + 1}]:[/bold] ").strip()

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(models[:8]):
            selected = models[idx]
            console.print(f"\n[green]Using:[/green] {selected}")
            return {
                "provider": "ollama",
                "model": selected,
                "base_url": "http://localhost:11434",
            }
    except ValueError:
        pass

    return _configure_ollama_new(sys_info)


def _configure_ollama_new(sys_info: dict[str, Any]) -> dict[str, Any]:
    """Guide user to pull a new Ollama model based on their RAM."""
    ram = sys_info["ram_gb"]

    console.print("\n[bold]Recommended models for your system:[/bold]\n")

    suitable = [m for m in MODEL_RECOMMENDATIONS if ram >= m[0]]
    if not suitable:
        suitable = MODEL_RECOMMENDATIONS[-1:]  # Smallest model as fallback

    for i, (_, _model_id, name, note) in enumerate(suitable, 1):
        console.print(f"  [bold]{i}.[/bold] {name} ({note})")

    choice = console.input(f"\n[bold]Choose model [1-{len(suitable)}]:[/bold] ").strip()

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(suitable):
            _, model_id, name, _ = suitable[idx]
        else:
            model_id = suitable[0][1]
            name = suitable[0][2]
    except ValueError:
        model_id = suitable[0][1]
        name = suitable[0][2]

    # Pull the model
    if sys_info["ollama_running"]:
        console.print(f"\n[blue]Pulling {name}... (this may take a few minutes)[/blue]")
        try:
            subprocess.run(
                ["ollama", "pull", model_id],
                check=True,
                timeout=600,
            )
            console.print(f"[green]Model {name} ready.[/green]")
        except subprocess.TimeoutExpired:
            console.print(
                "[yellow]Download is taking long. It will continue in the background.[/yellow]"
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Pull failed:[/red] {e}")
            console.print("[dim]You can pull it manually: ollama pull {model_id}[/dim]")
    else:
        console.print("\n[yellow]Ollama not running.[/yellow] Pull the model manually:")
        console.print("  ollama serve")
        console.print(f"  ollama pull {model_id}")

    return {
        "provider": "ollama",
        "model": model_id,
        "base_url": "http://localhost:11434",
    }


def _configure_cloud(provider: str, default_model: str) -> dict[str, Any]:
    """Configure a cloud LLM provider."""
    console.print(f"\n[bold]{provider.title()} Configuration[/bold]\n")
    console.print(
        Panel(
            "[yellow bold]Privacy Notice[/yellow bold]\n\n"
            "When using a cloud provider, document content (up to 4000 chars)\n"
            "is sent to their servers for classification.\n\n"
            "This includes text from files, emails, and OCR-extracted content.\n"
            "For maximum privacy, use Ollama (local, offline).",
            border_style="yellow",
        )
    )
    console.print(
        f"You'll need an API key from {provider.title()}.\n"
        f"Set it as environment variable before running Lotse:\n"
    )

    if provider == "openai":
        console.print("  export OPENAI_API_KEY='sk-...'")
    elif provider == "anthropic":
        console.print("  export ANTHROPIC_API_KEY='sk-ant-...'")

    console.print(f"\n[dim]Using model: {default_model}[/dim]")

    return {
        "provider": provider,
        "model": default_model,
        "base_url": None,
    }


# --- Route Configuration ---


def _configure_routes() -> dict[str, Any]:
    """Guide user through basic route setup."""
    console.print("\n[bold]Step 2: Routes[/bold]\n")
    console.print(
        "Routes define where classified items go.\n"
        "You can always add more later in the config file.\n"
    )

    routes = {}
    home = Path.home()

    # Default suggestions
    suggestions = [
        ("archiv", "Invoices, contracts, letters", ["rechnung", "vertrag", "brief", "bescheid"]),
        (
            "artikel",
            "Articles, tutorials, papers",
            ["artikel", "paper", "tutorial", "dokumentation"],
        ),
        ("code", "Code snippets, configs", ["code", "config", "script"]),
    ]

    for route_name, description, categories in suggestions:
        default_path = home / "Documents" / "Lotse" / route_name.title()
        console.print(f"  [bold]{route_name}[/bold] — {description}")
        console.print(f"  Default path: [dim]{default_path}[/dim]")

        answer = console.input("  [bold]Accept? [Y/n/custom path]:[/bold] ").strip()

        if answer.lower() == "n":
            continue
        elif answer and answer.lower() != "y":
            # Custom path
            custom = Path(answer).expanduser()
            routes[route_name] = {"path": str(custom), "categories": categories}
        else:
            routes[route_name] = {"path": str(default_path), "categories": categories}

        console.print()

    if not routes:
        # At least add archiv as default
        routes["archiv"] = {
            "path": str(home / "Documents" / "Lotse" / "Archiv"),
            "categories": ["rechnung", "vertrag", "brief", "bescheid"],
        }

    return routes


# --- Config Writing ---


def _write_config(llm_config: dict[str, Any], routes: dict[str, Any]) -> None:
    """Write the configuration file."""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Lotse Configuration",
        "# https://github.com/HerrStolzier/lotse",
        "",
        "[llm]",
        f'provider = "{llm_config["provider"]}"',
        f'model = "{llm_config["model"]}"',
    ]

    if llm_config.get("base_url"):
        lines.append(f'base_url = "{llm_config["base_url"]}"')

    lines.extend(
        [
            "temperature = 0.1",
            "",
            "[embeddings]",
            'model = "BAAI/bge-small-en-v1.5"',
            "",
            "[database]",
            'path = "~/.local/share/lotse/lotse.db"',
            "",
        ]
    )

    for name, route_data in routes.items():
        cats = ", ".join(f'"{c}"' for c in route_data["categories"])
        lines.extend(
            [
                f"[routes.{name}]",
                'type = "folder"',
                f'path = "{route_data["path"]}"',
                f"categories = [{cats}]",
                "confidence_threshold = 0.7",
                "",
            ]
        )

    DEFAULT_CONFIG_FILE.write_text("\n".join(lines) + "\n")
    console.print(f"\n[green]Config written:[/green] {DEFAULT_CONFIG_FILE}")


# --- Test Classification ---


def _run_test(llm_config: dict[str, Any]) -> None:
    """Run a quick test classification to verify everything works."""
    console.print("\n[bold]Step 3: Verification[/bold]\n")
    console.print("[blue]Running test classification...[/blue]")

    try:
        from lotse.core.config import LotseConfig
        from lotse.core.engine import Engine

        config = LotseConfig.load()
        config.ensure_dirs()
        engine = Engine(config)

        test_text = (
            "Sehr geehrter Kunde, anbei erhalten Sie Ihre Rechnung "
            "für den Monat März 2026. Rechnungsbetrag: 39,99 EUR. "
            "Bitte überweisen Sie den Betrag bis zum 15.04.2026."
        )

        result = engine.ingest_text(test_text, name="setup_test")

        if result.success:
            # Show what the LLM classified
            recent = engine.store.recent(limit=1)
            if recent:
                item = recent[0]
                console.print(
                    f"\n[green]Test passed![/green]\n"
                    f"  Category:   [cyan]{item['category']}[/cyan]\n"
                    f"  Confidence: {item['confidence']:.0%}\n"
                    f"  Summary:    {item['summary']}\n"
                )
            else:
                console.print("[green]Test passed![/green] (classification successful)")
        else:
            console.print(f"[red]Test failed:[/red] {result.message}")
            console.print("[dim]Check your LLM configuration and try again.[/dim]")

    except Exception as e:
        console.print(f"[red]Test failed:[/red] {e}")
        console.print(
            "\n[dim]Common issues:\n"
            "  - Ollama not running: ollama serve\n"
            "  - Model not pulled: ollama pull mistral\n"
            "  - API key not set: export OPENAI_API_KEY='...'\n"
            "[/dim]"
        )
