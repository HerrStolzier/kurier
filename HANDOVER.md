# Handover for Next Agent

## Kurzüberblick

Kurier ist aktuell auf `main` sauber committed und mit `origin/main` synchron.

Wichtige letzte Commits:

- `6ac5666` `docs: defer browser extension roadmap item`
- `e6404b4` `fix: confirm manual review corrections`
- `4964030` `fix: serve dashboard static assets`
- `d3119db` `feat: add memory search query assist`

Der Fokus der letzten Session lag auf drei Dingen:

1. KI-gestützte Erinnerungssuche
2. echte End-to-End-Tests wie ein Nutzer
3. frische Installations-Tests von null

## Was schon umgesetzt ist

### KI-Suche / Memory Search

Die Erinnerungssuche ist als echte Produktfunktion eingebaut.

Wichtige Punkte:

- Suchsignale wurden erweitert:
  - `suggested_filename`
  - `destination_name`
  - `display_title`
- Diese Felder werden in der DB gespeichert und im FTS-Index berücksichtigt.
- Das lokale LLM wird als Query-Assist verwendet:
  - Suchanfrage umformulieren
  - Filterhinweise ableiten
  - Suchvarianten erzeugen
- Treffer bekommen eine lesbare Begründung über `match_reason`.

Wichtige Dateien:

- [src/arkiv/core/search_assistant.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/core/search_assistant.py)
- [src/arkiv/core/engine.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/core/engine.py)
- [src/arkiv/db/store.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/db/store.py)
- [src/arkiv/inlets/api.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/inlets/api.py)
- [src/arkiv/cli.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/cli.py)
- [src/arkiv/dashboard/routes.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/dashboard/routes.py)
- [src/arkiv/dashboard/templates/dashboard.html](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/dashboard/templates/dashboard.html)
- [src/arkiv/dashboard/templates/partials/search_results.html](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/dashboard/templates/partials/search_results.html)

### Eval-/Planungsartefakte

Es gibt bereits vorbereitete Unterlagen für Modellvergleich und Eval-Logik:

- [ai-memory-search-sprint-1-plan.md](/Users/clawdkent/Desktop/projekte-codex/kurier/ai-memory-search-sprint-1-plan.md)
- [docs/ai-memory-search-requirements.md](/Users/clawdkent/Desktop/projekte-codex/kurier/docs/ai-memory-search-requirements.md)
- [docs/ai-memory-search-model-shortlist.md](/Users/clawdkent/Desktop/projekte-codex/kurier/docs/ai-memory-search-model-shortlist.md)
- [docs/ai-memory-search-mvp-decision.md](/Users/clawdkent/Desktop/projekte-codex/kurier/docs/ai-memory-search-mvp-decision.md)
- [security_best_practices_report.md](/Users/clawdkent/Desktop/projekte-codex/kurier/security_best_practices_report.md)
- [src/arkiv/evals/ai_search_benchmark.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/evals/ai_search_benchmark.py)
- [tests/fixtures/ai_search_benchmark.json](/Users/clawdkent/Desktop/projekte-codex/kurier/tests/fixtures/ai_search_benchmark.json)

### Review-Flow-Fix

Ein echter Nutzerbug wurde behoben:

- Vorher kam ein Eintrag nach manueller Korrektur im Dashboard-Review nach dem nächsten Refresh wieder zurück.
- Ursache: `update_category()` änderte die Kategorie, aber nicht die Confidence.
- Jetzt gilt eine manuelle Korrektur als bestätigt und setzt `confidence = 1.0`.

Relevante Stellen:

- [src/arkiv/db/store.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/db/store.py)
- [tests/test_store.py](/Users/clawdkent/Desktop/projekte-codex/kurier/tests/test_store.py)
- [tests/test_dashboard.py](/Users/clawdkent/Desktop/projekte-codex/kurier/tests/test_dashboard.py)

### Dashboard-Static-Fix

Ein echter Browser-Bug wurde behoben:

- Vorher wurden `/dashboard/static/styles.css` und `/dashboard/static/htmx.min.js` nicht korrekt ausgeliefert.
- Ursache war das Mounting der Static Assets.
- Das ist jetzt gefixt.

Relevante Stellen:

- [src/arkiv/inlets/api.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/inlets/api.py)
- [src/arkiv/dashboard/routes.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/dashboard/routes.py)
- [tests/test_dashboard.py](/Users/clawdkent/Desktop/projekte-codex/kurier/tests/test_dashboard.py)

## Was wirklich E2E getestet wurde

Die folgenden Wege wurden nicht nur über Unit-Tests, sondern als echter Nutzerlauf geprüft:

### Vorhandene Umgebung / echter Produktfluss

- `kurier doctor`
- `kurier add`
- `kurier search --memory`
- `kurier serve`
- Dashboard-Suche mit `dev-browser`
- `kurier watch` mit echter Datei im Eingang
- `kurier undo`
- Dashboard-Review-Flow mit echten Klicks im Browser
- `kurier tui` Start-Smoke

### Frische Installation von null

Zwei Wege wurden neu geprüft:

1. Developer-Install
   - neues Python-3.11-venv
   - `uv pip install -e ".[dev]"`
   - danach echter CLI-Smoke

2. Paket-Install
   - Wheel via `uv build`
   - neues Python-3.11-venv
   - `uv pip install <wheel>`
   - danach echter CLI-Smoke

Beide Installationswege funktionierten.

## Letzter verifizierter technischer Stand

Folgende Checks liefen grün:

- `ruff check src/`
- `mypy src/arkiv/ --ignore-missing-imports`
- `pytest tests/ -x -q`

Letztes Ergebnis:

- `110 passed`
- bekannte OCR-Deprecation-Warnings, aber keine neuen Testfehler

## Wichtige inhaltliche Entscheidungen

### Aus dem aktuellen Kernumfang herausgenommen

- Browser-Extension: vorerst gestrichen
- E-Mail-Inlet: vorerst nicht Kernfokus, nur als optional später markiert

Aktuelle README-Lage:

- Browser-Extension ist aus der Roadmap entfernt.
- E-Mail-Inlet steht als `Optional later`.

### Webhook-Plugin bleibt optional sinnvoll

Das Webhook-Plugin ist noch im Projekt und als optionales Extra sinnvoll:

- Slack
- Discord
- n8n
- Zapier
- eigene Endpunkte

Es gehört aber eher zum Erweiterungsbild als zum minimalen Kernfluss.

## Offene Kanten / ehrliche Restpunkte

Diese Dinge sind **nicht** vollständig als harter E2E-Gesamtfluss abgeschlossen:

- Webhook-Plugin als kompletter Live-End-to-End-Lauf gegen einen echten Zielendpunkt
- Browser-Extension, bewusst zurückgestellt
- E-Mail-Inlet als Priorität, bewusst zurückgestellt
- eventuelle tiefere TUI-Interaktion über den Start-Smoke hinaus

Außerdem ist bei frischer Installation ein kleiner UX-Punkt sichtbar:

- `kurier doctor` meldet anfangs das Archiv-Verzeichnis als fehlend
- das ist kein echter Defekt, weil `kurier add` es korrekt anlegt
- aber es ist eine erste Nutzerkante, falls jemand einen komplett reibungslosen Erststart erwartet

## Was der nächste Agent sinnvoll tun könnte

Die sinnvollsten nächsten Richtungen sind:

1. README/Erststart noch glatter machen
   - klarer erklären, dass `doctor` fehlende Zielordner am Anfang nur als Hinweis meldet
   - eventuell `init` oder `doctor --fix` weiter schärfen

2. Webhook-Plugin bewusst einordnen
   - optionales Extra so dokumentieren, dass es nicht wie Kernfunktion wirkt
   - wenn gewünscht: einmal echter Live-Smoke gegen lokalen Test-Webhook

3. KI-Suche weiter verbessern
   - bessere Filterlogik
   - multilingual/deutschfreundlichere Embeddings prüfen
   - echtes Modell-Benchmarking gegen die vorbereiteten Eval-Fixtures

4. Produktreife-Einschätzung machen
   - kurze ehrliche Matrix: „stabil“, „brauchbar“, „experimentell“, „zurückgestellt“

## Praktische Hinweise für den nächsten Agenten

- Bitte alle LLM-Aufrufe weiter über [src/arkiv/core/llm.py](/Users/clawdkent/Desktop/projekte-codex/kurier/src/arkiv/core/llm.py) laufen lassen.
- `litellm` nicht wieder einführen.
- Nach Codeänderungen diese drei Checks laufen lassen:
  - `ruff check src/`
  - `mypy src/arkiv/ --ignore-missing-imports`
  - `pytest tests/ -x -q`
- Wenn Provider-Wiring, Klassifikation oder Plugin-Hooks verändert werden, mindestens einen echten Provider-Smoke mitlaufen lassen.

## Repo-Zustand zum Handover-Zeitpunkt

- Branch: `main`
- Remote: `origin/main`
- Stand: synchron
- Arbeitsbaum: sauber
