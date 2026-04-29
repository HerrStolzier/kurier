# Arkiv

**Universal capture → classify → route. Your AI-powered data pilot.**

Arkiv takes any digital input — files, URLs, text — classifies it using AI, and routes it to the right destination. Think of it as an intelligent mail sorting facility for your digital life.

```
              ┌─────────┐
  File ──────►│         │──► Archiv/Rechnungen
  URL  ──────►│  ARKIV  │──► Leseliste/Artikel
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
pipx install "kurier @ git+https://github.com/HerrStolzier/kurier.git"

# Make sure Ollama is running with a model
ollama pull qwen2.5:7b

# One-time setup
kurier init

# Optional first-run check
kurier doctor --fix

# Start Kurier
kurier
```

That's it. `kurier init` writes a starter config and creates the default folders. `kurier doctor --fix` is a safe first-run helper: it creates any still-missing directories from your config and shows whether your local model setup looks reachable.

After that, `kurier` launches the interactive TUI where you can classify files, search, monitor your inbox, and more — all from one interface.

> **Alternative install methods:**
> ```bash
> # With pip (requires a virtual environment)
> pip install "kurier @ git+https://github.com/HerrStolzier/kurier.git"
>
> # With uv
> uv pip install "kurier @ git+https://github.com/HerrStolzier/kurier.git"
> ```

### CLI Commands

All features are also available as individual commands:

```bash
kurier                         # Interactive TUI (default)
kurier add invoice.pdf         # Classify and route a file
kurier watch                   # Auto-process files in inbox
kurier search "Rechnung"       # Hybrid keyword + semantic search
kurier status                  # Processing statistics
kurier undo                    # Undo last routing action
kurier export --format csv     # Export all items as CSV
kurier doctor                  # Check system health
kurier doctor --fix            # Create missing config directories
kurier doctor --repair-db      # Back up DB and rebuild derived search indexes
kurier init                    # Interactive setup wizard
```

## Configuration

Kurier uses a TOML config file at `~/.config/kurier/config.toml`:

```toml
[llm]
provider = "ollama"
model = "mistral"
base_url = "http://localhost:11434"

[embeddings]
model = "BAAI/bge-small-en-v1.5"

[routes.archiv]
type = "folder"
path = "~/Documents/Arkiv/Archiv"
categories = ["rechnung", "vertrag", "brief"]
confidence_threshold = 0.7

[routes.artikel]
type = "folder"
path = "~/Documents/Arkiv/Artikel"
categories = ["artikel", "paper", "tutorial"]
confidence_threshold = 0.6
```

## LLM Providers

Arkiv supports any LLM provider via direct HTTP calls (`core/llm.py`):

| Provider | Config |
|----------|--------|
| Ollama (local) | `provider = "ollama"`, `model = "qwen2.5:7b"` |
| OpenAI | `provider = "openai"`, `model = "gpt-4o-mini"` |
| Anthropic | `provider = "anthropic"`, `model = "claude-sonnet-4-5-20250514"` |
| HuggingFace | `provider = "huggingface"`, `model = "meta-llama/..."` |

## REST API

The API server ships with the main package, so if `kurier` is already installed you can start it directly:

```bash
kurier serve
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

Arkiv is built to be extended. Plugins can:

- **Pre-process** content before classification
- **Post-process** classification results
- **Add custom routes** (Slack, Notion, webhooks, ...)
- **React to routing events** (notifications, logging, ...)

### Writing a Plugin

```python
# my_arkiv_plugin.py
from arkiv.plugins.spec import hookimpl

@hookimpl
def on_routed(path: str, destination: str, route_name: str) -> None:
    """Send a notification when a file is routed."""
    print(f"Routed {path} → {destination}")
```

```toml
# pyproject.toml
[project.entry-points."arkiv.plugins"]
my-plugin = "my_arkiv_plugin"
```

See the [Plugin Guide](docs/plugins.md) for details.

## Architecture

```
src/arkiv/
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

## Current Product Status

This is the honest status snapshot as of **2026-04-29**:

| Status | What it means in practice |
|--------|----------------------------|
| **Stable** | Fresh install, `kurier init`, `kurier doctor --fix`, file intake via `kurier add`, folder routing, watcher flow, API server, dashboard review fixes, undo/export, and the basic local-first archive flow have been exercised end-to-end. |
| **Usable** | AI-assisted memory search is integrated and works in the product, but it still depends heavily on model quality and has not yet gone through deeper comparative benchmarking. Webhook routing/plugin delivery has been exercised against a live local endpoint, and the private Raspberry Pi n8n demo path is prepared. The n8n claim should only be promoted after a real Pi POST has been captured. |
| **Experimental** | TUI support is present and starts cleanly, but deeper interactive coverage is still lighter than the core CLI/dashboard path. |
| **Deferred** | Browser extension work is intentionally out of scope for now. Email inlet support remains an optional-later item rather than part of the current core experience. |

If you want the longer rationale behind this snapshot, see [docs/product-maturity.md](docs/product-maturity.md).

## Roadmap

- [x] Core pipeline: capture → classify → route
- [x] CLI interface
- [x] SQLite + FTS5 search
- [x] Plugin system (pluggy)
- [x] Filesystem watcher
- [x] REST API inlet (FastAPI with auto-docs)
- [x] Semantic search (FastEmbed + sqlite-vec hybrid search with RRF)
- [x] Web dashboard (HTMX + Tailwind, no build step)
- [ ] Optional later: Email inlet (IMAP fetch + .eml/.mbox import)
- [x] Webhook route plugin (Slack, Discord, generic)
- [x] OCR support (PyMuPDF + Tesseract)
- [x] Self-audit system (duplicates, misclassifications, orphaned files)
- [x] Interactive TUI (Textual)
- [x] Undo & Export commands
- [x] Retry logic with exponential backoff
- [x] Transaction safety (pending → routed/failed)
- [x] Custom categories via config

## Development

```bash
# Clone
git clone https://github.com/HerrStolzier/kurier.git
cd kurier

# Create venv and install in development mode
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Run the full local release check
scripts/release-check

# Fresh install smoke tests
scripts/smoke-editable-install
scripts/smoke-wheel-install

# Optional private n8n demo receiver on a Raspberry Pi
PI_HOST=raspberrypi.local PI_USER=pi scripts/pi-n8n-setup
```

## Troubleshooting

If local development feels "almost working" but commands fail in strange ways, these are the most common fixes:

- **`pytest` points to an old path**: recreate the virtualenv with `rm -rf .venv && uv venv .venv`, then reinstall with `uv pip install -e ".[dev]"`.
- **`ModuleNotFoundError: arkiv` during tests**: the repo is not installed in editable mode yet. Run `uv pip install -e ".[dev]"`.
- **Plugin tests cannot import `arkiv_webhook`**: install the plugin locally first with `uv pip install -e plugins/arkiv-webhook`.
- **Plugin tests fail when mixed with core tests**: run them separately with `pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/`.
- **`kurier doctor` warns about missing folders on a fresh setup**: that is often just first-run state, not a broken install. Run `kurier doctor --fix` once to create the configured directories.
- **`kurier status` says the database image is malformed**: if `sqlite3 integrity_check` is still ok, this is often a broken derived FTS search index, not lost item data. Stop the service, then run `kurier doctor --repair-db`; it creates a backup before rebuilding the derived index.
- **Classification changes look fine in unit tests but fail in real usage**: mocked tests do not prove provider wiring. Run at least one real-provider smoke check after touching classification, provider integration, or plugin hooks.

For plugin-specific details, see the [Plugin Guide](docs/plugins.md). For the private n8n demo receiver, see [docs/n8n-raspberry-pi-demo.md](docs/n8n-raspberry-pi-demo.md).

## License

MIT — see [LICENSE](LICENSE).
