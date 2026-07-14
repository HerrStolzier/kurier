# Workflow Register

> **Zweck:** Register der lokalen Kurier-Entwicklung: CLI, API, Dashboard, Routing, Suche, OCR.
> **Scope:** Standard-Workflow, bevor Agenten Codeaenderungen als fertig melden.
> **Suchbegriffe:** cli, api, dashboard, routing, suche, ocr, uv, python
> **Stand:** 2026-07-14

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
- Nach Abschluss ein Eintrag in `.agents/finish_runs.jsonl` durch `python3 scripts/agent_finish.py --auto-claims`.

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
python3 scripts/agent_finish.py --auto-claims
```

### Letzter Review

2026-05-21
