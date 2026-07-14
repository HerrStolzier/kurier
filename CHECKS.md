# Workflow Checks

> **Zweck:** Checks fuer Kurier: Standardabschluss, Direktkommandos, Format-, Plugin- und Smoke-Checks.
> **Scope:** uv run ruff / mypy / pytest plus gezielte Zusatzchecks.
> **Suchbegriffe:** ruff, mypy, pytest, uv, format, plugin, smoke
> **Stand:** 2026-07-14

## Standard Check

```bash
python3 scripts/agent_finish.py --auto-claims
```

Der Standard-Check prueft zuerst die Guard-Dateien und fuehrt dann die normalen Kurier-Qualitaetschecks aus.

## Direktkommandos

```bash
uv run ruff check src/ tests/
uv run mypy src/arkiv/ --ignore-missing-imports
uv run pytest tests/ -x -q
```

## Format Check

```bash
uv run ruff format --check src/ tests/
```

Hinweis: Der globale Format-Check kann wegen bestehenden Altdateien fehlschlagen. Bei gezielten Aenderungen mindestens die beruehrten Python-Dateien formatpruefen oder formatieren.

## Plugin Check

Nur wenn `plugins/arkiv-webhook/` beruehrt wurde:

```bash
uv run pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/
```

## Smoke Checks

Bei Klassifizierung, Provider-Wiring oder Plugin-Hooks:

```bash
uv run kurier add <demo-file> --config <demo-config>
```

Bei Packaging, Install-Flow oder CLI-Entry-Points:

```bash
uv pip install -e . --python .venv/bin/python
uv build
```

## Pflichtdateien

- `AGENTS.md`
- `WORKFLOWS.md`
- `KNOWN_ERRORS.md`
- `CHECKS.md`
- `scripts/workflow_check.py`
- `scripts/agent_finish.py`

## Erwartung

Agenten fuehren den Standard-Check nach nicht-trivialen Aenderungen aus und aktualisieren die Guard-Dokumentation, wenn sich Workflows, Befehle, Abhaengigkeiten oder bekannte Fehler veraendern.

## Standardabschluss

```bash
python3 scripts/agent_finish.py --auto-claims
```

Fuehrt aus:
1. Struktur-Guard (`scripts/workflow_check.py`) - kanonisch, in allen Repos identisch
2. Technischer Projektcheck aus `.agents/project_check`
3. Claim-Check (`scripts/claim_check.py`)

Exit 2 blockiert ueber den Stop-Hook den Abschluss.
