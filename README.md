# Kurier

**Dokumente hineinlegen. Kurier versteht sie, sortiert sie und macht sie wieder auffindbar.**

Kurier ist ein lokaler Dokumentenhelfer: Du legst Dateien in einen Eingangs-Ordner,
Kurier erkennt den Inhalt mit KI-Unterstützung und legt die Dokumente passend ab. Die Daten bleiben
auf deinem Rechner; Cloud-Anbieter sind optional.

```
              ┌─────────┐
  Datei ─────►│          │──► Archiv/Rechnungen
  Text  ─────►│  KURIER  │──► Leseliste/Artikel
  Scan  ─────►│          │──► Code/Snippets
  Mail  ─────►│ erkennen │──► Prüfen (unsicher)
              │ ablegen  │──► Webhook / n8n
              └──────────┘──► Weitere Erweiterungen
```

## Was Kurier kann

- **Eingang überwachen** — neue Dateien im Eingangs-Ordner automatisch verarbeiten
- **Erledigt sehen** — das Dashboard zeigt automatisch, was aus dem Eingang verarbeitet wurde
- **Dokumente verstehen** — lokale KI per Ollama, optional OpenAI, Anthropic oder Hugging Face
- **Passend ablegen** — nach Dokumentart in Ordner, Webhooks oder spätere Erweiterungen routen
- **Wiederfinden** — Wortsuche plus intelligente Suche nach Bedeutung
- **Prüfen statt raten** — unsichere Fälle landen im Ordner `Prüfen`
- **Lokal zuerst** — private Dokumente bleiben auf deinem Rechner

## Schnellstart

```bash
# Installieren
pipx install "kurier @ git+https://github.com/HerrStolzier/kurier.git"

# Lokales KI-Modell vorbereiten
ollama pull qwen2.5:7b

# Einmal einrichten
kurier init

# Prüfen, ob alles startklar ist
kurier doctor --fix

# Kurier öffnen
kurier
```

`kurier init` erstellt die Einstellungen und die Standard-Ordner. `kurier doctor --fix` ist ein
sicherer Startcheck: fehlende Ordner werden angelegt und Kurier zeigt, ob das lokale KI-Modell
erreichbar ist.

Danach startet `kurier` die interaktive Oberfläche. Dort kannst du Dateien hinzufügen, den Eingang
überwachen, suchen und den Gesundheitscheck ausführen.

### Alltag: Datei in den Eingang legen

Der normale Kurier-Flow ist:

1. Datei in `~/Documents/Kurier/Eingang` legen.
2. Kurier verarbeitet sie automatisch, wenn die Auto-Sortierung laeuft.
3. Im Dashboard unter **Letzte Dokumente** sehen, was erledigt wurde.

Dieser Bereich aktualisiert sich automatisch. Du siehst dort Quelle, erkannte Art, Sicherheit,
sprechenden Namen und Ablage. Wenn etwas falsch wirkt, wechselst du in die Pruefliste und
korrigierst die Kategorie. Auch `kurier status` zeigt das zuletzt erledigte Dokument.

### Befehle

Alle wichtigen Funktionen gibt es auch einzeln:

```bash
kurier                         # interaktive Oberfläche öffnen
kurier add rechnung.pdf        # ein Dokument verarbeiten
kurier watch                   # den Eingangs-Ordner beobachten
kurier search "Rechnung"       # Dokumente suchen
kurier status                  # zeigen, was verarbeitet wurde
kurier undo                    # letzte Ablage rückgängig machen
kurier export --format csv     # Dokumentliste exportieren
kurier doctor                  # Gesundheitscheck ausführen
kurier doctor --fix            # fehlende Ordner automatisch anlegen
kurier init                    # Einrichtung starten
```

## Einstellungen

Kurier speichert seine Einstellungen in `~/.config/kurier/config.toml`:

```toml
[llm]
provider = "ollama"
model = "mistral"
base_url = "http://localhost:11434"

[embeddings]
model = "BAAI/bge-small-en-v1.5"

[routes.archiv]
type = "folder"
path = "~/Documents/Kurier/Archiv"
categories = ["rechnung", "vertrag", "brief"]
confidence_threshold = 0.7

[routes.artikel]
type = "folder"
path = "~/Documents/Kurier/Artikel"
categories = ["artikel", "paper", "tutorial"]
confidence_threshold = 0.6
```

## KI-Anbieter

Kurier ruft KI-Anbieter zentral über `src/arkiv/core/llm.py` auf:

| Anbieter | Einstellung |
|----------|--------|
| Ollama (lokal) | `provider = "ollama"`, `model = "qwen2.5:7b"` |
| OpenAI | `provider = "openai"`, `model = "gpt-4o-mini"` |
| Anthropic | `provider = "anthropic"`, `model = "claude-sonnet-4-5-20250514"` |
| HuggingFace | `provider = "huggingface"`, `model = "openai/gpt-oss-20b:fastest"` plus `HF_TOKEN` |

Hugging Face nutzt standardmäßig den Inference-Providers-Router:

```toml
[llm]
provider = "huggingface"
model = "openai/gpt-oss-20b:fastest"
base_url = "https://router.huggingface.co/v1"  # optional default
```

Für lokale Ollama-Modelle prüft Kurier beim Setup und im Gesundheitscheck, ob der verfügbare
Arbeitsspeicher ungefähr zum Modell passt. Die Modelltests wählen ebenfalls ein konservatives
Standardmodell für den erkannten Rechner.

## Erweiterungen

Kurier kann später erweitert werden. Erweiterungen können zum Beispiel:

- Inhalte vor der Erkennung vorbereiten
- Ergebnisse nachbearbeiten
- eigene Ablageziele ergänzen, etwa Slack, Notion oder Webhooks
- auf Ablage-Ereignisse reagieren, etwa mit Benachrichtigungen

### Erweiterung schreiben

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

Details stehen im [Plugin Guide](docs/plugins.md).

## Aktueller Produktstatus

Stand: **2026-05-18**.

| Status | Was das praktisch bedeutet |
|--------|----------------------------|
| **Stabil** | Installation, Einrichtung, Gesundheitscheck, Datei-Verarbeitung, Ordner-Ablage, Undo/Export und der lokale Grundfluss sind end-to-end geprüft. |
| **Nutzbar** | KI-gestützte Suche, Benchmarksystem, n8n/Webhook-Anbindung und RAM-bewusste Modellwahl sind integriert. Die Qualität hängt weiter vom gewählten Modell ab. |
| **Im Feinschliff** | Der Eingangsordner-Flow zeigt Ergebnisse jetzt im Dashboard unter “Letzte Dokumente” und in `kurier status`; der echte 5-Tage-Alltagstest steht noch aus. |
| **Später** | Browser-Erweiterung und voll ausgebaute E-Mail-Zuführung bleiben bewusst optional und gehören nicht zum aktuellen Kern. |

Die ausführliche Begründung zu diesem Stand steht in
[docs/product-maturity.md](docs/product-maturity.md).

---

## Für Entwickler (English)

Everything below targets developers and stays in English.

### Alternative install methods

```bash
# With pip (requires a virtual environment)
pip install "kurier @ git+https://github.com/HerrStolzier/kurier.git"

# With uv
uv pip install "kurier @ git+https://github.com/HerrStolzier/kurier.git"
```

### Model Quality Checks

Kurier can test which AI model works best for your documents. The check answers three practical
questions:

- Can the model recognize what kind of document it is?
- Can it understand search questions well enough to improve search?
- Does it actually find the right document, not just sound confident?

For everyday use, start with the full check:

```bash
kurier eval llm --all --output eval-results/latest.json
```

You can also test specific models:

```bash
kurier eval llm --task retrieval --models baseline --models ollama:qwen2.5:7b
kurier eval llm --task search --models huggingface:openai/gpt-oss-20b:fastest
```

The screen output is a readable summary. The JSON report keeps the detailed numbers for later
comparison, including quality score, runtime, errors, and task-specific metrics. `baseline` means
"no AI help" and shows whether a model really improves the search compared with plain keyword
retrieval.

### REST API

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

### Architecture

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

### Roadmap

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

### Development

```bash
# Clone
git clone https://github.com/HerrStolzier/kurier.git
cd kurier

# Create venv and install in development mode
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Run tests
pytest tests/ -x -q

# Plugin tests
uv pip install -e plugins/arkiv-webhook
pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/

# Lint + type check
ruff check src/
mypy src/arkiv/ --ignore-missing-imports
```

### Troubleshooting

If local development feels "almost working" but commands fail in strange ways, these are the most common fixes:

- **`pytest` points to an old path**: recreate the virtualenv with `rm -rf .venv && uv venv .venv`, then reinstall with `uv pip install -e ".[dev]"`.
- **`ModuleNotFoundError: arkiv` during tests**: the repo is not installed in editable mode yet. Run `uv pip install -e ".[dev]"`.
- **Plugin tests cannot import `arkiv_webhook`**: install the plugin locally first with `uv pip install -e plugins/arkiv-webhook`.
- **Plugin tests fail when mixed with core tests**: run them separately with `pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/`.
- **`kurier doctor` warns about missing folders on a fresh setup**: that is often just first-run state, not a broken install. Run `kurier doctor --fix` once to create the configured directories.
- **Classification changes look fine in unit tests but fail in real usage**: mocked tests do not prove provider wiring. Run at least one real-provider smoke check after touching classification, provider integration, or plugin hooks.

For plugin-specific details, see the [Plugin Guide](docs/plugins.md).

## License

MIT — see [LICENSE](LICENSE).
