# Arkiv

Universal capture → classify → route platform. Python 3.11+, local-first.

## Commands

```bash
# Install (development)
pip install -e ".[dev,api,ocr]"

# Run tests
pytest                    # all 78 tests
pytest tests/test_api.py  # specific module
pytest -m smoke           # smoke tests only (require Ollama)

# Lint + format
ruff check src/           # lint
ruff format src/          # format
ruff check src/ --fix     # auto-fix

# Type check
mypy src/arkiv/ --ignore-missing-imports

# Start API + dashboard
kurier serve               # http://127.0.0.1:8790/dashboard/

# Audit routing decisions
kurier audit               # report only
kurier audit --fix         # interactive fix mode

# System health
kurier doctor              # check Ollama, RAM, model availability

# Plugin tests (separate rootdir to avoid import conflicts)
pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/
```

## Architecture

```
src/arkiv/
├── cli.py                 # Typer CLI (11 commands)
├── setup_wizard.py        # Interactive first-run setup (system detection, model selection)
├── core/
│   ├── config.py          # TOML config via pydantic-settings (XDG paths)
│   ├── classifier.py      # LLM classification via LiteLLM (provider-agnostic)
│   ├── router.py          # Category-based routing with fan-out (folder + webhook)
│   ├── engine.py          # Pipeline orchestrator: extract → classify → embed → route → store
│   ├── embeddings.py      # FastEmbed wrapper (BAAI/bge-small-en-v1.5, 384-dim)
│   ├── ocr.py             # PyMuPDF native + Tesseract fallback
│   └── auditor.py         # Self-audit: duplicates, misclassifications, orphaned files, missing destinations
├── db/
│   └── store.py           # SQLite + FTS5 + sqlite-vec (hybrid search with RRF)
├── dashboard/
│   ├── routes.py          # FastAPI routes serving Jinja2/HTMX partials
│   ├── static/            # HTMX bundled locally (no CDN)
│   └── templates/         # base.html + partials/ (stats, search, recent, upload)
├── inlets/
│   ├── api.py             # FastAPI REST endpoints + dashboard mount
│   ├── watch.py           # Filesystem watcher (watchdog)
│   └── email.py           # IMAP fetch + .eml/.mbox parsing (stdlib only)
└── plugins/
    ├── spec.py            # pluggy hookspecs (pre_classify, post_classify, custom_route, on_routed)
    └── manager.py         # Plugin discovery via entry_points("arkiv.plugins")

plugins/arkiv-webhook/     # First-party plugin: webhook routes (Slack, Discord, generic)
```

## Key Patterns

- **LLM calls go through LiteLLM** (`core/classifier.py`). Ollama uses `ollama_chat/` prefix (NOT `ollama/`) — the plain prefix uses legacy `/api/generate` which drops message content. Needs explicit `api_base`.
- **Qwen 3.5 has "thinking mode"** that adds ~100s overhead per call. Use Qwen 2.5 for classification (no thinking, fast, accurate). Models below 7B tend to misclassify.
- **Pluggy hook returns are lists, not values**: `hook.pre_classify()` returns `[]` when no plugin implements it. Original content must be preserved when the list is empty — otherwise the LLM receives empty input and hallucinates. This was a critical bug (March 2026).
- **Embedding model is lazy-loaded** (`engine.py:embedder` property). FastEmbed loads ~33MB model on first use — don't import at module level.
- **SQLite connection uses `check_same_thread=False`** — required for FastAPI async endpoints. WAL mode protects concurrent writes.
- **sqlite-vec loads as extension** (`store.py:_load_sqlite_vec`). If not installed, vector search degrades gracefully to FTS-only.
- **sqlite-vec MATCH doesn't support WHERE filters**: Duplicate detection fetches extra rows and filters self-matches in Python, not SQL.
- **Router supports fan-out**: One classification can match multiple routes. Folder routes move the file, webhook routes fire without moving. Empty `categories = []` acts as wildcard.
- **OCR is two-stage**: PyMuPDF tries native text extraction first. Only if <50 chars found, Tesseract runs at 300 DPI. This avoids OCR overhead for 90% of PDFs.
- **Dashboard uses HTMX partials**: Server returns HTML fragments, not JSON. Routes under `/dashboard/partials/*`. No JS build step. HTMX served locally, Tailwind via CDN.
- **Plugin tests can't run in same pytest invocation** as core tests due to `tests/` package name collision. Use separate `--rootdir`.

## Config

TOML at `~/.config/arkiv/config.toml`. Run `kurier init` for interactive wizard, `kurier init --quick` for defaults.

Key sections:
- `[llm]` — provider, model, base_url, temperature
- `[embeddings]` — model name, cache_dir
- `[database]` — path, store_content (disable for max privacy)
- `[audit]` — similarity_threshold (0.92), confidence_threshold (0.6), reclassify_sample (10)
- `[routes.*]` — type (folder/webhook), path/url, categories, confidence_threshold

## Optional Dependencies

| Extra | What it enables | Install |
|-------|----------------|---------|
| `api` | REST API + Dashboard | `pip install kurier[api]` |
| `ocr` | PDF/image text extraction | `pip install kurier[ocr]` + `brew install tesseract` |
| `dev` | Testing + linting | `pip install kurier[dev]` |

## Ruff

B008 is ignored globally — `typer.Argument()`/`typer.Option()` in function defaults is the standard Typer pattern, not a bug.

## Testing

- Unit tests mock LLM calls via `patch("arkiv.core.classifier.completion")`.
- API tests use `fastapi.testclient.TestClient`.
- Store/auditor tests use `tmp_path` fixture for disposable SQLite DBs.
- **Mock gap warning**: Unit tests with mocked LLM don't test the real integration path (LiteLLM → Ollama). Always verify with at least one smoke test using a real LLM. The pluggy empty-list bug was invisible to 78 green unit tests.
