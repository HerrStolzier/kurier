# Lotse

Universal capture → classify → route platform. Python 3.11+, local-first.

## Commands

```bash
# Install (development)
pip install -e ".[dev,api,ocr]"

# Run tests
pytest                    # all 60 tests
pytest tests/test_api.py  # specific module

# Lint + format
ruff check src/           # lint
ruff format src/          # format
ruff check src/ --fix     # auto-fix

# Type check
mypy src/lotse/ --ignore-missing-imports

# Start API + dashboard
lotse serve               # http://127.0.0.1:8790/dashboard/

# Plugin tests (separate rootdir to avoid import conflicts)
pytest --rootdir=plugins/lotse-webhook --override-ini="testpaths=plugins/lotse-webhook/tests" plugins/lotse-webhook/tests/
```

## Architecture

```
src/lotse/
├── cli.py                 # Typer CLI (10 commands: add, watch, search, status, init, serve, plugins, import-email, fetch-email)
├── core/
│   ├── config.py          # TOML config via pydantic-settings (XDG paths)
│   ├── classifier.py      # LLM classification via LiteLLM (provider-agnostic)
│   ├── router.py          # Category-based routing with fan-out (folder + webhook)
│   ├── engine.py          # Pipeline orchestrator: extract → classify → embed → route → store
│   ├── embeddings.py      # FastEmbed wrapper (BAAI/bge-small-en-v1.5, 384-dim)
│   └── ocr.py             # PyMuPDF native + Tesseract fallback
├── db/
│   └── store.py           # SQLite + FTS5 + sqlite-vec (hybrid search with RRF)
├── dashboard/
│   ├── routes.py          # FastAPI routes serving Jinja2/HTMX partials
│   └── templates/         # base.html + partials/ (stats, search, recent, upload)
├── inlets/
│   ├── api.py             # FastAPI REST endpoints + dashboard mount
│   ├── watch.py           # Filesystem watcher (watchdog)
│   └── email.py           # IMAP fetch + .eml/.mbox parsing (stdlib only)
└── plugins/
    ├── spec.py            # pluggy hookspecs (pre_classify, post_classify, custom_route, on_routed)
    └── manager.py         # Plugin discovery via entry_points("lotse.plugins")

plugins/lotse-webhook/     # First-party plugin: webhook routes (Slack, Discord, generic)
```

## Key Patterns

- **LLM calls go through LiteLLM** (`core/classifier.py`). Model ID format: `provider/model` (e.g., `ollama/mistral`). Ollama needs explicit `api_base`.
- **Embedding model is lazy-loaded** (`engine.py:embedder` property). FastEmbed loads ~33MB model on first use — don't import at module level.
- **SQLite connection uses `check_same_thread=False`** — required for FastAPI async endpoints. WAL mode protects concurrent writes.
- **sqlite-vec loads as extension** (`store.py:_load_sqlite_vec`). If not installed, vector search degrades gracefully to FTS-only.
- **Router supports fan-out**: One classification can match multiple routes. Folder routes move the file, webhook routes fire without moving. Empty `categories = []` acts as wildcard.
- **OCR is two-stage**: PyMuPDF tries native text extraction first. Only if <50 chars found, Tesseract runs at 300 DPI. This avoids OCR overhead for 90% of PDFs.
- **Dashboard uses HTMX partials**: Server returns HTML fragments, not JSON. Routes under `/dashboard/partials/*`. No JS build step.
- **Plugin tests can't run in same pytest invocation** as core tests due to `tests/` package name collision. Use separate `--rootdir`.

## Optional Dependencies

| Extra | What it enables | Install |
|-------|----------------|---------|
| `api` | REST API + Dashboard | `pip install lotse[api]` |
| `ocr` | PDF/image text extraction | `pip install lotse[ocr]` + `brew install tesseract` |
| `dev` | Testing + linting | `pip install lotse[dev]` |

## Config

TOML at `~/.config/lotse/config.toml`. Key sections: `[llm]`, `[embeddings]`, `[database]`, `[routes.*]`. Run `lotse init` to generate defaults.

Route types: `folder` (moves file) and `webhook` (POST to URL, requires lotse-webhook plugin).

## Ruff

B008 is ignored globally — `typer.Argument()`/`typer.Option()` in function defaults is the standard Typer pattern, not a bug.

## Testing

Tests use `unittest.mock.patch` for LLM calls (mock `lotse.core.classifier.completion`). API tests use `fastapi.testclient.TestClient`. Store tests use `tmp_path` fixture for disposable SQLite DBs.
