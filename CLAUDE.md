# Kurier

Universal capture → classify → route. Python 3.11+, local-first.
PyPI: `pipx install kurier` | CLI: `kurier` | Internal package: `arkiv`

## Commands

```bash
# Install (development)
uv pip install -e ".[dev]"
uv pip install -e plugins/arkiv-webhook

# Run tests
pytest tests/ -x -q       # all 92 core tests
pytest tests/test_api.py  # specific module
pytest -m smoke           # smoke tests only (require Ollama)

# Lint + format
ruff check src/           # lint
ruff format src/          # format
ruff check src/ --fix     # auto-fix

# Type check
mypy src/arkiv/ --ignore-missing-imports

# TUI (default wenn ohne Subcommand)
kurier                     # Interaktive Oberfläche
kurier tui                 # Explizit

# Background service
kurier service on          # Auto-start bei Login (launchd/systemd)
kurier service off         # Service stoppen
kurier service status      # Status + letzte Logs

# Klassifizierung
kurier add invoice.pdf     # Einzelne Datei
kurier watch               # Inbox überwachen (blocking)
kurier undo                # Letzte Sortierung rückgängig
kurier export --format csv # Alle Einträge exportieren

# API + Dashboard
kurier serve               # http://127.0.0.1:8790/dashboard/

# Audit + Health
kurier audit               # report only
kurier audit --fix         # interactive fix mode
kurier doctor              # check Config, Ollama, Routes, DB

# Plugin tests (separate rootdir to avoid import conflicts)
pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/
```

## Architecture

```
src/arkiv/
├── cli.py                 # Typer CLI (16+ commands, inkl. service subapp)
├── setup_wizard.py        # Interactive first-run setup (system detection, model selection)
├── service.py             # OS-native background service (launchd/systemd)
├── notifications.py       # Cross-platform desktop notifications
├── tui/
│   ├── app.py             # Textual TUI (Home, Search, Recent, Watch, Audit, Wizard)
│   └── styles.css         # Dark theme with amber accent
├── core/
│   ├── config.py          # TOML config via pydantic-settings (XDG paths)
│   ├── llm.py             # Direct HTTP LLM calls (Ollama, OpenAI, Anthropic) — NO litellm
│   ├── classifier.py      # LLM classification via llm.py (provider-agnostic)
│   ├── router.py          # Category-based routing with fan-out (folder + webhook)
│   ├── engine.py          # Pipeline orchestrator: extract → classify → embed → route → store
│   ├── embeddings.py      # FastEmbed wrapper (BAAI/bge-small-en-v1.5, 384-dim)
│   ├── ocr.py             # PyMuPDF native + Tesseract fallback
│   ├── auditor.py         # Self-audit: duplicates, misclassifications, orphaned files
│   ├── auth.py            # API-Key-Authentifizierung (x-api-key Header, KURIER_API_KEY env)
│   └── upload.py          # Multipart-Upload-Handling für /ingest/file Endpoint
├── db/
│   └── store.py           # SQLite + FTS5 + sqlite-vec (hybrid search with RRF)
├── dashboard/
│   ├── routes.py          # FastAPI routes serving Jinja2/HTMX partials
│   ├── static/            # HTMX bundled locally (no CDN)
│   └── templates/         # base.html + partials/ (stats, search, recent, upload)
├── inlets/
│   ├── api.py             # FastAPI REST endpoints + dashboard mount
│   ├── watch.py           # Filesystem watcher (watchdog) with Ollama health-check
│   └── email.py           # IMAP fetch + .eml/.mbox parsing (stdlib only)
└── plugins/
    ├── spec.py            # pluggy hookspecs (pre_classify, post_classify, custom_route, on_routed)
    └── manager.py         # Plugin discovery via entry_points("arkiv.plugins")

plugins/arkiv-webhook/     # First-party plugin: webhook routes (Slack, Discord, generic)
```

## Key Patterns

- **LLM calls go through `core/llm.py`** (eigene Implementierung, kein litellm). Drei Provider: Ollama (HTTP POST /api/chat), OpenAI-kompatibel (/v1/chat/completions), Anthropic (/v1/messages). Provider wird automatisch erkannt via model-Name und api_base.
- **litellm wurde entfernt** (Supply-Chain-Attack März 2026). NICHT wieder hinzufügen. Alternative: `core/llm.py` mit ~130 Zeilen direkten httpx-Calls.
- **Retry-Logic**: Classifier hat 3 Retries mit exponential Backoff (1s, 3s, 9s). Timeout 30s pro Call.
- **Transaction-Safety**: `ingest_file()` speichert erst als `pending`, dann `routed`/`failed`. Kein Datenverlust bei Routing-Fehler.
- **Qwen 3.5 has "thinking mode"** that adds ~100s overhead per call. Use Qwen 2.5 for classification (no thinking, fast, accurate). Models below 7B tend to misclassify.
- **Pluggy hook returns are lists, not values**: `hook.pre_classify()` returns `[]` when no plugin implements it. Original content must be preserved when the list is empty — otherwise the LLM receives empty input and hallucinates. This was a critical bug (March 2026).
- **Embedding model is lazy-loaded** (`engine.py:embedder` property). FastEmbed loads ~33MB model on first use — don't import at module level.
- **SQLite connection uses `check_same_thread=False`** — required for FastAPI async endpoints. WAL mode protects concurrent writes.
- **sqlite-vec loads as extension** (`store.py:_load_sqlite_vec`). If not installed, vector search degrades gracefully to FTS-only.
- **sqlite-vec MATCH doesn't support WHERE filters**: Duplicate detection fetches extra rows and filters self-matches in Python, not SQL.
- **Router supports fan-out**: One classification can match multiple routes. Folder routes move the file, webhook routes fire without moving. Empty `categories = []` acts as wildcard.
- **OCR is two-stage**: PyMuPDF tries native text extraction first. Only if <50 chars found, Tesseract runs at 300 DPI. This avoids OCR overhead for 90% of PDFs.
- **Dashboard uses HTMX partials**: Server returns HTML fragments, not JSON. Routes under `/dashboard/partials/*`. No JS build step. HTMX served locally, Tailwind pre-built locally (no CDN). CSS is at `src/arkiv/dashboard/static/styles.css` and is committed to git.
- **Plugin tests can't run in same pytest invocation** as core tests due to `tests/` package name collision. Use separate `--rootdir`.
- **Watcher Ollama-Polling**: Wenn Ollama nicht läuft, pollt der Watcher alle 30s statt Dateien blind zu verarbeiten. Nur bei `provider = "ollama"`.
- **Service Plist-Pfad**: macOS: `~/Library/LaunchAgents/local.kurier.watch.plist`, Logs: `~/Library/Logs/kurier.log`
- **Custom Categories**: Optional via `[categories]` in config.toml — werden mit Defaults gemerged (Config gewinnt bei Konflikten)

## Config

TOML at `~/.config/kurier/config.toml`. Run `kurier init` for interactive wizard (TUI), `kurier init --quick` for defaults.

Key sections:
- `[llm]` — provider, model, base_url, temperature
- `[embeddings]` — model name, cache_dir
- `[database]` — path, store_content (disable for max privacy)
- `[audit]` — similarity_threshold (0.92), confidence_threshold (0.6), reclassify_sample (10)
- `[routes.*]` — type (folder/webhook), path/url, categories, confidence_threshold
- `inbox_dir` — Eingangs-Ordner (default: ~/Documents/Kurier/Eingang)
- `review_dir` — Prüf-Ordner für unsichere Klassifizierungen
- `notifications` — Desktop-Notifications ein/aus (default: true)
- `[categories]` — Optional: eigene Kategorien als key=description Paare

## Optional Dependencies

Alle Dependencies in main (kein litellm!): httpx, typer, rich, textual, fastapi, etc.
Nur `dev` ist optional: `uv pip install -e ".[dev]"`
**httpx** statt litellm für LLM-Calls — ~130 Zeilen eigener Code in `core/llm.py`

## Ruff

B008 and RUF012 are ignored globally — B008: typer.Argument/Option in defaults is the standard pattern. RUF012: ctypes _fields_ false positive.

## Dashboard CSS Rebuild

Tailwind CSS is pre-built and committed to git. Rebuild after template changes:
```bash
cd src/arkiv/dashboard
./node_modules/.bin/tailwindcss -i input.css -o static/styles.css --minify
```
The `node_modules/` and `package.json` live in `src/arkiv/dashboard/`. Do NOT commit `node_modules/`.

## API Server Flags (kurier serve)

- `--api-key <key>` or env `KURIER_API_KEY` — require `x-api-key` header for non-localhost
- `--force` — allow unauthenticated non-localhost binding (insecure, explicit opt-in)
- Without `--force` or `--api-key`, non-localhost binding exits with an error

## Code Quality — Pflicht vor jedem Commit/Push

Builders und Agents MÜSSEN nach Code-Änderungen diese 3 Checks laufen lassen:
```bash
ruff check src/                              # Lint
mypy src/arkiv/ --ignore-missing-imports     # Type check (strict!)
pytest tests/ -x -q                          # Tests
```
**mypy --strict ist aktiv** — alle Funktionen brauchen Return-Type-Annotations, generische Typen brauchen Parameter (`dict[str, object]` nicht `dict`), und `subprocess.run()` muss `text=True` haben wenn `.stdout` als `str` verwendet wird.

## Testing

- Unit tests mock LLM calls via `patch("arkiv.core.classifier.completion")`.
- API tests use `fastapi.testclient.TestClient`.
- Store/auditor tests use `tmp_path` fixture for disposable SQLite DBs.
- **Mock gap warning**: Unit tests with mocked LLM don't test the real provider integration path. Always verify with at least one smoke test using a real LLM. The pluggy empty-list bug was invisible to a fully green mocked test suite.
