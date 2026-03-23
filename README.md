# Lotse

**Universal capture → classify → route. Your AI-powered data pilot.**

Lotse takes any digital input — files, URLs, text — classifies it using AI, and routes it to the right destination. Think of it as an intelligent mail sorting facility for your digital life.

```
              ┌─────────┐
  File ──────►│         │──► Archiv/Rechnungen
  URL  ──────►│  LOTSE  │──► Leseliste/Artikel
  Text ──────►│         │──► Code/Snippets
  Mail ──────►│ classify │──► Review (unsicher)
              │  route   │──► Plugin: Webhook
              └─────────┘──► Plugin: Custom
```

## Features

- **Universal Intake** — Drop any file, paste text, pipe from stdin
- **AI Classification** — LLM-powered content understanding (Ollama, OpenAI, Anthropic, HuggingFace)
- **Smart Routing** — Category-based rules route items to folders, webhooks, or custom destinations
- **Full-Text Search** — SQLite FTS5 search across all processed items
- **Plugin System** — Extend with pip-installable plugins (powered by pluggy)
- **Local-First** — Your data stays on your machine. No cloud required.
- **Filesystem Watcher** — Auto-process files dropped into your inbox directory

## Quick Start

```bash
# Install
pipx install "lotse @ git+https://github.com/HerrStolzier/lotse.git"

# Make sure Ollama is running with a model
ollama pull qwen2.5:7b

# Start Lotse
lotse
```

That's it. `lotse` launches the interactive TUI where you can classify files, search, monitor your inbox, and more — all from one interface.

> **Alternative install methods:**
> ```bash
> # With pip (requires a virtual environment)
> pip install "lotse @ git+https://github.com/HerrStolzier/lotse.git"
>
> # With uv
> uv pip install "lotse @ git+https://github.com/HerrStolzier/lotse.git"
> ```

### CLI Commands

All features are also available as individual commands:

```bash
lotse                         # Interactive TUI (default)
lotse add invoice.pdf         # Classify and route a file
lotse watch                   # Auto-process files in inbox
lotse search "Rechnung"       # Hybrid keyword + semantic search
lotse status                  # Processing statistics
lotse undo                    # Undo last routing action
lotse export --format csv     # Export all items as CSV
lotse doctor                  # Check system health
lotse init                    # Interactive setup wizard
```

## Configuration

Lotse uses a TOML config file at `~/.config/lotse/config.toml`:

```toml
[llm]
provider = "ollama"
model = "mistral"
base_url = "http://localhost:11434"

[embeddings]
model = "BAAI/bge-small-en-v1.5"

[routes.archiv]
type = "folder"
path = "~/Documents/Lotse/Archiv"
categories = ["rechnung", "vertrag", "brief"]
confidence_threshold = 0.7

[routes.artikel]
type = "folder"
path = "~/Documents/Lotse/Artikel"
categories = ["artikel", "paper", "tutorial"]
confidence_threshold = 0.6
```

## LLM Providers

Lotse supports any LLM provider via [LiteLLM](https://github.com/BerriAI/litellm):

| Provider | Config |
|----------|--------|
| Ollama (local) | `provider = "ollama"`, `model = "qwen2.5:7b"` |
| OpenAI | `provider = "openai"`, `model = "gpt-4o-mini"` |
| Anthropic | `provider = "anthropic"`, `model = "claude-sonnet-4-5-20250514"` |
| HuggingFace | `provider = "huggingface"`, `model = "meta-llama/..."` |

## REST API

Start the API server for external integrations, webhooks, and mobile capture:

```bash
# Install API dependencies
uv pip install "lotse[api] @ git+https://github.com/HerrStolzier/lotse.git"

# Start the server
lotse serve
# → http://127.0.0.1:8790/docs (Swagger UI)
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/ingest/file` | Upload a file for classification |
| `POST` | `/ingest/text` | Submit text for classification |
| `GET` | `/search?q=...` | Hybrid keyword + semantic search |
| `GET` | `/status` | Processing statistics |
| `GET` | `/recent` | Recently processed items |

```bash
# Example: ingest a file via curl
curl -X POST http://localhost:8790/ingest/file -F "file=@invoice.pdf"

# Example: search
curl "http://localhost:8790/search?q=Telefonkosten&mode=auto"
```

## Plugins

Lotse is built to be extended. Plugins can:

- **Pre-process** content before classification
- **Post-process** classification results
- **Add custom routes** (Slack, Notion, webhooks, ...)
- **React to routing events** (notifications, logging, ...)

### Writing a Plugin

```python
# my_lotse_plugin.py
from lotse.plugins.spec import hookimpl

@hookimpl
def on_routed(path: str, destination: str, route_name: str) -> None:
    """Send a notification when a file is routed."""
    print(f"Routed {path} → {destination}")
```

```toml
# pyproject.toml
[project.entry-points."lotse.plugins"]
my-plugin = "my_lotse_plugin"
```

See the [Plugin Guide](docs/plugins.md) for details.

## Architecture

```
src/lotse/
├── cli.py              # Typer CLI interface
├── core/
│   ├── config.py       # TOML configuration
│   ├── classifier.py   # LLM classification engine
│   ├── router.py       # Route matching & execution
│   └── engine.py       # Main pipeline orchestrator
├── db/
│   └── store.py        # SQLite + FTS5 storage
├── inlets/
│   └── watch.py        # Filesystem watcher
├── plugins/
│   ├── spec.py         # Plugin hook specifications
│   └── manager.py      # Plugin discovery (pluggy)
└── routes/             # Built-in route handlers
```

## Roadmap

- [x] Core pipeline: capture → classify → route
- [x] CLI interface
- [x] SQLite + FTS5 search
- [x] Plugin system (pluggy)
- [x] Filesystem watcher
- [x] REST API inlet (FastAPI with auto-docs)
- [x] Semantic search (FastEmbed + sqlite-vec hybrid search with RRF)
- [x] Web dashboard (HTMX + Tailwind, no build step)
- [x] Email inlet (IMAP fetch + .eml/.mbox import)
- [x] Webhook route plugin (Slack, Discord, generic)
- [x] OCR support (PyMuPDF + Tesseract)
- [x] Self-audit system (duplicates, misclassifications, orphaned files)
- [x] Interactive TUI (Textual)
- [x] Undo & Export commands
- [x] Retry logic with exponential backoff
- [x] Transaction safety (pending → routed/failed)
- [x] Custom categories via config
- [ ] Browser extension

## Development

```bash
# Clone
git clone https://github.com/HerrStolzier/lotse.git
cd lotse

# Create venv and install in development mode
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,api,ocr,tui]"

# Run tests
pytest

# Lint + type check
ruff check src/
mypy src/
```

## License

MIT — see [LICENSE](LICENSE).
