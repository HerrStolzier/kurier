# Kurier Handover

Stand: 13. August 2026

Dieses Dokument ist die aktuelle Übergabe für einen neuen Codex, Claude oder ChatGPT Agenten. Es ersetzt den früheren Stand in dieser Datei.

## Auftrag und aktueller Fokus

Kurier ist ein lokaler Dokumentenhelfer. Dateien kommen in einen Eingangsordner, werden erkannt, passend abgelegt und später über Suche oder Dashboard wiedergefunden.

Die letzten beiden Produktänderungen waren:

1. Verträge und AGB im Dashboard auf Wunsch in einfacher Sprache erklären.
2. Den Dokumentbereich im Dashboard ruhiger, kleiner und verständlicher machen.

Der nächste sinnvolle Schwerpunkt ist nicht noch eine neue Funktion. Zuerst sollte der Prüf Ordner vollständig mit der Prüfliste im Dashboard verbunden werden.

## Bestätigter Stand des Repos

| Punkt | Stand |
|---|---|
| Branch | `main` |
| Produktstand vor diesem Handover | `4662bb2` |
| GitHub | `main` war am 13. August 2026 mit `origin/main` synchron |
| Projektversion | `0.4.2` |
| Arbeitsbaum vor diesem Handover | sauber |

Wichtige aktuelle Commits:

| Commit | Inhalt |
|---|---|
| `4662bb2` | Workflow Guard auf Version 1.0.1 aktualisiert |
| `86aa800` | Dokumentbereich im Dashboard beruhigt |
| `3fe8348` | Lokale Erklärung für Verträge und AGB eingebaut |
| `cf51718` und `910e957` | Große Scans lesen und OCR Fehlerfälle absichern |
| `b33d122` | Inhaltsgleiche Dokumente als Duplikate erkennen |

## Bestätigter lokaler Betriebszustand

Der Gesundheitscheck wurde am 13. August 2026 außerhalb der geschützten Testumgebung ausgeführt.

| Bereich | Ergebnis |
|---|---|
| Automatische Sortierung | läuft als lokaler Dienst |
| Eingang | leer |
| Prüf Ordner | 5 Dateien warten auf Sichtung |
| Ablage Daten | 43 Einträge, keine Fehler |
| Webhooks | keine offenen Zustellungen |
| Klassifikation | Ollama mit `qwen2.5:7b`, erreichbar |
| Vertragserklärung | Ollama mit `qwen3.5:9b`, erreichbar |
| Arbeitsspeicher | 16 GB, Gesundheitscheck meldet passend |
| Dashboard Server | läuft nicht dauerhaft mit dem Sortierdienst und muss bei Bedarf mit `kurier serve` gestartet werden |

`ollama list` zeigte nur diese benötigten Modelle:

- `qwen2.5:7b`
- `qwen3.5:9b`
- `nomic-embed-text:latest`

Ein Gemma Modell ist nicht mehr installiert.

Wichtig: Der Sortierdienst und das Dashboard sind getrennt. Die automatische Sortierung kann laufen, während auf Port `8790` kein Dashboard Server aktiv ist.

## Lokale Erklärung für Verträge und AGB

### Was umgesetzt ist

Bei abgelegten Dokumenten der Kategorie `vertrag` erscheint im Dashboard die Schaltfläche **Einfach erklären**. Erst nach dem Klick liest Kurier die lokale Datei und erstellt eine feste Erklärung. Es gibt keinen freien Chat.

Die Ausgabe enthält:

- eine kurze Zusammenfassung
- höchstens sechs wichtige Punkte
- offene Fragen, wenn der Text etwas nicht klar sagt
- die jeweiligen Textstellen aus dem Dokument
- einen klaren Hinweis, dass dies keine Rechtsberatung ist

### Warum zwei Modelle verwendet werden

`qwen2.5:7b` bleibt für die normale Dokument Erkennung zuständig. Es ist für diese kurze Aufgabe schneller und im bisherigen Kurier Benchmark bewährt.

`qwen3.5:9b` wird nur für die ausführlichere Vertragserklärung genutzt. Der Denkmodus ist im Code mit `think=False` abgeschaltet, damit keine lange Denkpause entsteht.

### Wie die Absicherung funktioniert

Die Erklärung wird als festes JSON Format verlangt und danach streng geprüft. Aussagen ohne echte Textstelle werden verworfen. Bei einem unbrauchbaren Ergebnis versucht Kurier es einmal erneut. Danach zeigt Kurier lieber einen Fehler, statt eine unbelegte Erklärung auszugeben.

Lange Dokumente werden nur bis zur eingestellten Grenze gelesen. Die Oberfläche warnt sichtbar, wenn nicht das gesamte Dokument berücksichtigt wurde. Die aktuelle Grenze ist `8000` Zeichen.

Wichtige Dateien:

- `src/arkiv/core/explainer.py`
- `src/arkiv/application/explain.py`
- `src/arkiv/core/config.py`
- `src/arkiv/dashboard/routes.py`
- `src/arkiv/dashboard/templates/partials/document_explanation.html`
- `tests/test_explainer.py`
- `tests/test_dashboard.py`

Aktuelle lokale Einstellung:

```toml
[llm]
provider = "ollama"
model = "qwen2.5:7b"

[explanation]
enabled = true
model = "qwen3.5:9b"
```

## Ruhiger Dokumentbereich im Dashboard

### Was sich geändert hat

Der frühere große Bereich mit vielen verschachtelten Karten wurde durch eine kompakte Liste ersetzt. Sichtbar sind nur die zehn neuesten Dokumente. Quelle, Sortierregel und Korrekturhinweis liegen unter **Weitere Angaben**.

Die drei Bereiche heißen jetzt:

- **Neu abgelegt**
- **Zu prüfen**
- **Fehler**

### Wie die Aktualisierung funktioniert

Die Dokumentliste lädt sich nicht mehr alle fünf Sekunden vollständig neu. Kurier prüft alle 30 Sekunden nur einen kleinen Änderungszähler in SQLite. Erst wenn sich Dokument Daten geändert haben, wird die sichtbare Liste neu geladen.

Uploads sowie Bestätigungen und Korrekturen senden sofort das Ereignis `kurier:documents-changed`. Dadurch bleibt die Oberfläche aktuell, ohne beim Lesen aufzuspringen.

Wichtige Stellen:

- `src/arkiv/db/store.py`: Tabelle und Trigger für `dashboard_revision`
- `src/arkiv/dashboard/routes.py`: `/dashboard/documents-version`
- `src/arkiv/dashboard/templates/dashboard.html`: ruhige Aktualisierung und Tabs
- `src/arkiv/dashboard/templates/partials/recent.html`: kompakte Dokumentzeilen

Die Oberfläche wurde in einer schmalen Browseransicht geprüft. Ein geöffnetes Detail blieb über 35 Sekunden offen. Damit wurde belegt, dass die Liste ohne echte Änderung nicht mehr neu aufgebaut wird.

## Wichtigster offener Produktfehler

Im Prüf Ordner liegen aktuell fünf Dateien. Die Dashboard Prüfliste zeigt aber nur unsichere Datenbank Einträge, deren `route_name` nicht `__review__` ist. Dateien, die Kurier bereits physisch in den Prüf Ordner verschoben hat, werden deshalb bewusst ausgeschlossen.

Die Ursache steht in `Store.low_confidence()` in `src/arkiv/db/store.py`:

```sql
AND route_name != '__review__'
```

Diese Zeile darf nicht einfach entfernt werden. Frühere Gegenprüfungen haben zwei Folgefehler gezeigt:

1. Bestätigen oder Korrigieren würde nur die Datenbank ändern. Die Datei bliebe trotzdem im Prüf Ordner.
2. Ein neuer Status wie `reviewed` würde bestehende Regeln für Duplikate, Watcher und Rückgängig machen beschädigen, weil diese mit `routed` arbeiten.

Die saubere Lösung braucht einen vollständigen Ablauf:

1. Das Dashboard zeigt auch Einträge mit `route_name = '__review__'`.
2. Bestätigen oder Korrigieren führt die Datei aus dem Prüf Ordner durch die passende Ablageregel.
3. Die Datenbank behält einen Status, den Watcher, Duplikat Prüfung und Rückgängig machen verstehen.
4. Der Abschluss der Prüfung wird getrennt und eindeutig gespeichert.
5. Tests prüfen Dateiort, Datenbank, erneutes Laden, Duplikate und Rückgängig machen gemeinsam.

Dieser Punkt hat Vorrang, weil der Gesundheitscheck fünf wartende Dateien meldet, die aktuelle Dashboard Prüfliste aber nicht zuverlässig bearbeiten kann.

## Weitere offene Punkte

### Echter Alltagstest fehlt noch

Der lokale Beta Bericht enthält noch keine Stolperer. Der geplante Test über fünf echte Nutzungstage wurde noch nicht abgeschlossen. Grundlage ist `docs/anti-failure-plan.md`.

### Vertragserklärung braucht reale Qualitätsfälle

Modell, Gesundheitscheck, Oberfläche und automatische Tests sind grün. In dieser Übergabe wurde aber keine neue Erklärung mit einem echten privaten Vertrag erzeugt und inhaltlich bewertet.

Sinnvoll sind zwei bis drei bewusst ausgewählte Dokumente:

- ein kurzer Vertrag mit klaren Kosten und Fristen
- längere AGB mit Kündigung und Haftung
- ein gescannter Vertrag mit OCR Text

Dabei getrennt prüfen: richtige Aussagen, belegte Textstellen, verständliche Sprache, Wartezeit und sichtbare Unsicherheit.

### Dokumentation ist teilweise veraltet

`README.md`, `docs/product-maturity.md` und `docs/anti-failure-plan.md` nennen teilweise noch **Letzte Dokumente** statt **Dokumente** und **Neu abgelegt**. Die Produktreife Dokumente tragen außerdem noch den Stand Mai 2026 und kennen die neue Vertragserklärung nicht vollständig.

Diese Texte sollten nach der Lösung des Prüf Ordner Ablaufs gemeinsam aktualisiert werden, damit die Beschreibung nicht erneut sofort veraltet.

## Empfohlene Reihenfolge für die nächste Arbeit

1. Prüf Ordner und Dashboard Prüfliste als vollständigen Ablauf planen und gegenlesen lassen.
2. Den Ablauf mit echten temporären Dateien umsetzen und alle Folgewege testen.
3. Zwei bis drei reale Vertragserklärungen qualitativ prüfen und Wartezeiten notieren.
4. Den fünf Tage Alltagstest starten und lokale Beta Hinweise auswerten.
5. README und Produktreife Dokumente auf den dann bestätigten Stand bringen.

## Technische Leitplanken

### KI Aufrufe

Alle Modell Aufrufe müssen durch `src/arkiv/core/llm.py` laufen. `litellm` darf nicht wieder eingeführt werden.

### Einstellungen

Die echte Konfiguration liegt unter `~/.config/kurier/config.toml`. Grundeinstellungen wie `inbox_dir` und `review_dir` müssen vor der ersten TOML Tabelle stehen. Sonst werden sie still dem falschen Abschnitt zugeordnet.

Keine Zugangsdaten ausgeben oder committen. Der lokale n8n Schlüssel liegt außerhalb des Repos.

### Installierte lokale Version aktualisieren

Ein einfaches `pipx install --force` kann alten Code behalten. Nach einer geprüften Änderung diese Installation verwenden:

```bash
/Users/ten.december/.local/pipx/venvs/kurier/bin/python \
  -m pip install --force-reinstall --no-cache-dir --no-deps .
```

Danach den betroffenen Kurier Prozess neu starten und die installierte Funktion prüfen.

### macOS Netzwerk Falle

Die Freigabe **Lokales Netzwerk** gilt pro Python Programm. Der installierte Kurier Dienst kann n8n erreichen, während die Repo Umgebung denselben Aufruf mit `No route to host` ablehnen kann. Bei Webhook Fehlern immer beide Python Programme getrennt prüfen.

### Dashboard CSS

Nach neuen Klassen in Dashboard Vorlagen das feste Tailwind CSS neu bauen:

```bash
cd src/arkiv/dashboard
./node_modules/.bin/tailwindcss -i input.css -o static/styles.css --minify
```

`node_modules` nie committen.

## Pflichtprüfungen nach Änderungen

Für normale Code Änderungen:

```bash
uv run ruff check src/ tests/
uv run mypy src/arkiv/ --ignore-missing-imports
uv run pytest tests/ -x -q
```

Bei Änderungen am Webhook Plugin zusätzlich:

```bash
uv run pytest \
  --rootdir=plugins/arkiv-webhook \
  --override-ini="testpaths=plugins/arkiv-webhook/tests" \
  plugins/arkiv-webhook/tests/
```

Vor dem Abschluss einer nicht kleinen Änderung:

```bash
scripts/agent_review --uncommitted
python3 scripts/agent_finish.py --auto-claims
```

Wenn sich der Code nach der Gegenprüfung ändert, ist die alte Gegenprüfung nicht mehr gültig.

## Zuletzt belegte Qualität

Für den heutigen Repository Stand einschließlich dieses Handover Inhalts wurden diese Ergebnisse bestätigt:

- 311 Tests erfolgreich
- Ruff ohne Fehler
- Typprüfung ohne Fehler
- Review Gate erfolgreich, ohne neue Code Änderungen
- Workflow Guard erfolgreich
- Browserprüfung in schmaler Ansicht erfolgreich

Eine zusätzliche externe Gegenprüfung des Handover Textes wurde aus Datenschutzgründen blockiert. Sie hätte lokale Projektpfade an einen externen Prüfer senden können. Die lokale Abschlussprüfung meldete keine Code Änderungen und verlangte deshalb keine neue Modell Gegenprüfung.

## Direkt nutzbarer Startauftrag für den nächsten Agenten

> Lies zuerst `AGENTS.md`, `CLAUDE.md`, `docs/project-learnings.md` und `HANDOVER.md`. Untersuche dann den Ablauf für die fünf Dateien im Prüf Ordner. Plane eine Lösung, bei der Bestätigen oder Korrigieren die echte Datei aus dem Prüf Ordner passend ablegt, ohne Watcher, Duplikat Erkennung oder Rückgängig machen zu beschädigen. Lass den Plan gegenlesen, setze ihn mit End to End Tests um und schließe mit `python3 scripts/agent_finish.py --auto-claims` ab. Zeige oder drucke keine privaten Dokumentinhalte und keine Zugangsdaten.
