# Plan: AI Memory Search Sprint 1

**Generated**: 2026-04-09
**Estimated Complexity**: Medium

## Overview
Sprint 1 schafft die Entscheidungsgrundlage fuer das KI-gestuetzte Suchfeature in Kurier. Ziel ist noch nicht die Implementierung des Features selbst, sondern ein belastbarer Startpunkt fuer den MVP:

- passende lokale LLM-Kandidaten auf Hugging Face vergleichen
- ein kleines Kurier-spezifisches Benchmark-Set bauen
- die wichtigsten Privacy- und Security-Risiken frueh pruefen
- eine klare Modell- und MVP-Entscheidung fuer Sprint 2 vorbereiten

Der Plan baut auf dem aktuellen Repo-Stand auf:

- bestehende Hybrid-Suche in [src/arkiv/core/engine.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/engine.py)
- FTS5 + sqlite-vec in [src/arkiv/db/store.py](/Users/ten.december/claude-projects/kurier/src/arkiv/db/store.py)
- lokales Ollama-LLM als Default in [src/arkiv/core/config.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/config.py)
- Klassifikation mit `suggested_filename` in [src/arkiv/core/classifier.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/classifier.py)

## Prerequisites
- Lokale Dev-Umgebung funktioniert bereits (`.venv`, Tests, Ruff, MyPy).
- Ollama kann lokal gestartet werden.
- Hugging-Face-Modellkarten und Leaderboard-Daten sind erreichbar.
- Es gibt Bereitschaft, fuer die Benchmark-Phase eine kleine Test-Datenbasis mit realistischen Beispielanfragen anzulegen.

## Sprint 1: Modellwahl und Evaluationsbasis
**Goal**: Eine klare, testbare Entscheidung vorbereiten, welches lokale Modell Kurier fuer die Erinnerungssuche zuerst nutzen soll und wie diese Entscheidung spaeter reproduzierbar geprueft wird.

**Demo/Validation**:
- Es existiert eine dokumentierte Modell-Shortlist mit Begruendung.
- Es existiert ein kleines Kurier-Benchmark-Set mit Beispielanfragen und Soll-Erwartungen.
- Es existiert ein erster Security-/Privacy-Review fuer das geplante Feature.
- Es gibt eine schriftliche MVP-Entscheidung fuer Sprint 2.

### Task 1.1: Suchrelevante Erfolgsdefinition festschreiben
- **Location**: `docs/ai-memory-search-requirements.md`
- **Description**: Die Suchfaelle und Erfolgskriterien fuer die Erinnerungs-Suche festhalten. Dazu gehoeren typische Nutzerfragen, Nicht-Ziele und die Minimaldefinition von "guter Treffer".
- **Dependencies**: Keine
- **Acceptance Criteria**:
  - 10-20 echte oder realistische Suchanfragen dokumentiert
  - Zielverhalten pro Anfrage beschrieben
  - Nicht-Ziele dokumentiert, z. B. kein freies Halluzinieren ueber nicht gefundene Dateien
- **Validation**:
  - Review der Datei auf Vollstaendigkeit und Klarheit
  - Beispiele decken Rechnungen, Briefe, Bescheide, Vertraege und OCR-Scans ab

### Task 1.2: Hugging-Face-Kandidaten-Shortlist erstellen
- **Location**: `docs/ai-memory-search-model-shortlist.md`
- **Description**: Lokale Instruct-Modelle fuer den Use Case vergleichen. Startkandidaten: `Qwen/Qwen3-4B-Instruct-2507`, `Qwen/Qwen2.5-7B-Instruct`, `meta-llama/Llama-3.1-8B-Instruct`, `mistralai/Ministral-3-8B-Instruct-2512`.
- **Dependencies**: Task 1.1
- **Acceptance Criteria**:
  - Pro Modell dokumentiert: Lizenz, Groesse, Sprachsignal, Benchmark-Signal, lokale Eignung
  - Vergleichskriterien klar festgehalten: Deutsch, JSON-Stabilitaet, Instruction Following, Laufzeit, Speicherbedarf
  - 2-3 Modelle fuer den lokalen Praxisvergleich priorisiert
- **Validation**:
  - Links zu Modellkarten und Hugging-Face-Quellen enthalten
  - Kandidatenliste ist fuer Kurier nachvollziehbar und nicht nur popularitaetsgetrieben

### Task 1.3: Externe Benchmark-Signale sauber mappen
- **Location**: `docs/ai-memory-search-model-shortlist.md`
- **Description**: Relevante Benchmark-Signale aus Modellkarten und Hugging-Face-Leaderboard-Daten zusammentragen. Fokus auf Instruction Following, mehrsprachige Leistung und strukturierte Ausgabe-Naehe.
- **Dependencies**: Task 1.2
- **Acceptance Criteria**:
  - Pro Modell sind die wichtigsten externen Signale dokumentiert
  - Unterschiede in den Benchmark-Setups als Unsicherheit vermerkt
  - Klarer Hinweis, dass allgemeine Benchmarks allein keine Kurier-Entscheidung ersetzen
- **Validation**:
  - Quellen stammen aus offiziellen Modellkarten oder offiziellen HF-Datenquellen
  - Dokument nennt explizit, welche Metriken fuer Kurier nur indirekt aussagekraeftig sind

### Task 1.4: Kurier-Mini-Benchmark als Testdatenbasis anlegen
- **Location**: `tests/fixtures/ai_search_benchmark.json`
- **Description**: Ein kleines Benchmark-Set anlegen, das echte Kurier-Suchfaelle simuliert. Jeder Fall enthaelt Nutzeranfrage, erwartete Suchbegriffe/Filter und Kriterien fuer eine gute Erklaerung.
- **Dependencies**: Task 1.1
- **Acceptance Criteria**:
  - Datensatz deckt mehrere Dokumentarten ab
  - Jeder Fall enthaelt erwartete strukturierte Ausgabe
  - Benchmark ist klein genug fuer schnellen lokalen Durchlauf, aber breit genug fuer echte Signale
- **Validation**:
  - JSON ist parsebar
  - Mindestens 10 Faelle vorhanden
  - Jede Anfrage hat definierte Sollfelder wie `rewrites`, `filters`, `notes`

### Task 1.5: Einfaches lokales Benchmark-Harness planen und vorbereiten
- **Location**: `tests/test_ai_search_benchmark.py`, optional `src/arkiv/evals/`
- **Description**: Testharness fuer Modellvergleich vorbereiten. Sprint 1 muss das Harness noch nicht komplett ausoptimieren, aber Struktur und Testlogik festlegen.
- **Dependencies**: Task 1.4
- **Acceptance Criteria**:
  - Es ist klar definiert, wie Modelle gegeneinander auf denselben Faellen laufen
  - Bewertet werden mindestens: JSON-Parsebarkeit, Feldvollstaendigkeit, Suchvarianten-Qualitaet, Laufzeit
  - Harness kann spaeter gegen Ollama-basierte Modelle laufen
- **Validation**:
  - Testdatei oder Eval-Skelett ist beschrieben oder angelegt
  - Bewertungslogik ist reproduzierbar und nicht rein manuell

### Task 1.6: Fruehen Privacy- und Security-Review fuer das Feature durchfuehren
- **Location**: `security_best_practices_report.md`
- **Description**: Vor der Implementierung eine erste Architekturpruefung machen. Fokus auf realistische Risiken: Prompt Injection aus Dokumenttext, uebermaessige Datenspeicherung, Logging sensibler Inhalte, Leaks ueber Suchtreffer, API- und Dashboard-Risiken.
- **Dependencies**: Task 1.1, Task 1.4
- **Acceptance Criteria**:
  - Risiken nach Schweregrad sortiert
  - Fuer jedes Risiko eine konkrete Gegenmassnahme beschrieben
  - Klar dokumentiert, welche Risiken vor Sprint 2 geloest werden muessen
- **Validation**:
  - Report referenziert relevante Stellen im bestehenden Code
  - Report unterscheidet zwischen bestaetigten Risiken und noch zu pruefenden Annahmen

### Task 1.7: Datenmodell-Luecken fuer die spaetere Suche benennen
- **Location**: `docs/ai-memory-search-requirements.md`
- **Description**: Festhalten, welche Felder fuer Sprint 2 in der DB und im Suchindex erweitert werden muessen. Besonders wichtig: `suggested_filename` und suchrelevante Teile von `destination`.
- **Dependencies**: Task 1.1
- **Acceptance Criteria**:
  - Bestehende Suchsignale und fehlende Suchsignale dokumentiert
  - Migrationsbedarf kurz beschrieben
  - Klarer Vorschlag fuer neue Felder oder Index-Erweiterungen vorhanden
- **Validation**:
  - Verweist auf bestehende Speicher- und Suchlogik in `src/arkiv/db/store.py` und `src/arkiv/core/engine.py`

### Task 1.8: Sprint-2-Startentscheidung festschreiben
- **Location**: `docs/ai-memory-search-mvp-decision.md`
- **Description**: Am Ende von Sprint 1 die praktische Entscheidung dokumentieren: Welches Modell startet, welche Felder werden erweitert, was ist der erste MVP-Scope.
- **Dependencies**: Task 1.2 bis Task 1.7
- **Acceptance Criteria**:
  - Ein primaeres Modell und ein Fallback-Modell benannt
  - MVP-Scope fuer Sprint 2 klar formuliert
  - Offene Risiken und spaetere Nice-to-haves getrennt dokumentiert
- **Validation**:
  - Entscheidung ist aus den Sprint-1-Artefakten ableitbar
  - Es gibt eine klare Go-/No-Go-Grundlage fuer die Implementierung

## Testing Strategy
- Dokumenten- und Planungsartefakte per Review pruefen
- Benchmark-JSON auf Parsebarkeit pruefen
- Falls ein erstes Harness angelegt wird: mindestens ein Smoke-Test gegen ein lokales Ollama-Modell
- In Sprint 1 noch keine produktive Feature-Tests erzwingen, aber die Testschablone fuer Sprint 2 vorbereiten

## Potential Risks & Gotchas
- Externe Benchmark-Werte aus Modellkarten sind nicht 1:1 vergleichbar.
  - **Mitigation**: Immer mit Kurier-eigenem Mini-Benchmark kombinieren.
- Ein Modell kann in Benchmarks gut aussehen, aber lokal zu langsam sein.
  - **Mitigation**: Laufzeit und Speicherbedarf als harte Kriterien aufnehmen.
- Ein Modell kann gut formulieren, aber instabiles JSON liefern.
  - **Mitigation**: JSON-Parsebarkeit als Pflichtkriterium definieren.
- Deutschsprachige Suchfaelle koennen mit dem aktuellen Embedding-Modell nur mittelgut abgedeckt sein.
  - **Mitigation**: In Sprint 2 die Embedding-Seite separat evaluieren, statt nur das LLM zu tauschen.
- Die spaetere Suchqualitaet bleibt begrenzt, wenn `suggested_filename` nicht indexiert wird.
  - **Mitigation**: Datenmodell-Luecke explizit als Sprint-2-Eingangskriterium festhalten.
- Security-/Privacy-Risiken werden leicht zu spaet betrachtet.
  - **Mitigation**: Architektur-Review in Sprint 1 verpflichtend machen und nicht auf das Ende verschieben.

## Rollback Plan
- Wenn kein Modell die Mindestanforderungen erfuellt, bleibt Kurier vorerst bei der bestehenden Hybrid-Suche ohne KI-Erweiterung.
- Wenn die Risiken fuer Privacy oder Prompt Injection zu hoch sind, wird Sprint 2 auf Datenmodell- und Sicherheitsvorbereitung begrenzt.
- Alle Sprint-1-Artefakte sind dokumentativ und benchmark-orientiert; sie koennen ohne Produktregression entfernt oder angepasst werden.
