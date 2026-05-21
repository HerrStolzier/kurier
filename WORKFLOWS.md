# Workflow Register

## Kurier Local Development Workflow

### Zweck

Lokale Kurier-Entwicklung fuer CLI, API, Dashboard, Routing, Suche, OCR und Tests. Dieser Workflow ist der Standard fuer Agenten, bevor sie Codeaenderungen als fertig melden.

### Start

```bash
uv run kurier --help
uv run kurier status
```

### Input

- Lokaler Checkout dieses Repos.
- Python 3.11+ mit `uv`.
- Projektabhaengigkeiten aus `pyproject.toml` und `uv.lock`.
- Optional: lokale Konfiguration fuer echte Demo-Laeufe, zum Beispiel `~/.config/kurier/config.toml`.

### Output

- Geaenderter, getesteter Projektstand.
- Aktualisierte Guard-Dokumentation, wenn Workflows, Checks oder bekannte Fehler betroffen sind.
- Nach Abschluss ein Eintrag in `.agents/workflow_guard_runs.md` durch `uv run python scripts/agent_finish.py`.

### Wichtige Dateien

- `AGENTS.md`
- `CHECKS.md`
- `KNOWN_ERRORS.md`
- `WORKFLOWS.md`
- `pyproject.toml`
- `src/arkiv/`
- `tests/`
- `plugins/arkiv-webhook/`
- `scripts/agent_finish.py`
- `scripts/workflow_check.py`

### Abhaengigkeiten

- `uv`
- Python 3.11+
- `ruff`
- `mypy`
- `pytest`
- optionale lokale Dienste fuer echte Smokes: Ollama, n8n, Docker, Raspberry Pi

### Bekannte Fehlerfaelle

- `ruff format --check src/ tests/` kann auf bestehenden Altdateien fehlschlagen, obwohl die beruehrten Dateien formatiert sind. Details in `KNOWN_ERRORS.md`.
- Plugin-Aenderungen brauchen den separaten Webhook-Plugin-Test aus `CHECKS.md`.
- Provider-, Klassifizierungs- und Hook-Aenderungen brauchen mindestens einen echten Provider-Smoke, nicht nur Mock-Tests.
- Packaging-, Install-Flow- oder CLI-Entry-Point-Aenderungen brauchen frische Editable- und Wheel-Smokes.

### Pruefung

```bash
uv run python scripts/agent_finish.py
```

### Letzter Review

2026-05-21
