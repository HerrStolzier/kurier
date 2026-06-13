# Security & Privacy Review: AI Memory Search (Sprint 1)

**Date**: 2026-04-09  
**Scope**: Architektur- und Frueh-Review fuer das geplante KI-gestuetzte Suchfeature in Kurier, mit Fokus auf bestehende Risiken in der aktuellen Codebasis und auf neue Risiken, die durch die Erinnerungssuche entstehen wuerden.  
**Stack**: Python / FastAPI / Jinja2 / HTMX / SQLite / sqlite-vec / lokales oder externes LLM

## Executive Summary
Kurier hat bereits eine gute Basis fuer lokale Dokumentverarbeitung, aber fuer ein KI-gestuetztes Suchfeature gibt es vor dem Bau noch echte Privacy- und Security-Themen, die nicht nur theoretisch sind.

Die wichtigsten Risiken sind:

1. **Dokumentinhalt wird heute bereits im Klartext gespeichert und ueber breite Datenpfade erreichbar gemacht.**
2. **Eine spaetere KI-Suche wuerde sehr wahrscheinlich einen realistischen Prompt-Injection-Angriffspfad aus Dokumentinhalt eroefnen, wenn Retrieval und LLM nicht strikt getrennt werden.**
3. **Die Web- und API-Ausgabe zeigt heute schon mehr Kontext als fuer eine Suchoberflaeche noetig ist, insbesondere Pfade und rohe Datensaetze.**

Bevor Sprint 2 startet, sollte die Implementierung auf einem klaren Prinzip aufbauen:

**Retrieval first, LLM second, minimale Datenausgabe, keine freien Rohtexte an die UI oder an den Modelllayer ohne Begrenzung.**

## Findings

## High Severity

### KBP-001: Klare Dokumentinhalte werden standardmaessig gespeichert und koennen ueber breite API-Pfade austreten
- **Rule ID**: KBP-001
- **Severity**: High
- **Location**:
  - [src/arkiv/core/config.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/config.py#L36) bis [src/arkiv/core/config.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/config.py#L40)
  - [src/arkiv/core/engine.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/engine.py#L81) bis [src/arkiv/core/engine.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/engine.py#L94)
  - [src/arkiv/db/store.py](/Users/ten.december/claude-projects/kurier/src/arkiv/db/store.py#L15) bis [src/arkiv/db/store.py](/Users/ten.december/claude-projects/kurier/src/arkiv/db/store.py#L34)
  - [src/arkiv/inlets/api.py](/Users/ten.december/claude-projects/kurier/src/arkiv/inlets/api.py#L216) bis [src/arkiv/inlets/api.py](/Users/ten.december/claude-projects/kurier/src/arkiv/inlets/api.py#L223)
- **Evidence**:
  - `store_content: bool = True`
  - `content_text=content[:2000] if store_content else ""`
  - `items(content_text TEXT, ...)`
  - `items_fts(..., content_text, ...)`
  - `/recent` gibt `engine.store.recent(...)` als rohe `list[dict[str, Any]]` zurueck
- **Impact**:
  - Sensible OCR- oder Dokumentinhalte werden standardmaessig in der lokalen DB und im Suchindex abgelegt.
  - Ueber `/recent` werden komplette Datensaetze ausgegeben, nicht nur minimierte Suchfelder. Das vergroessert die Leckflaeche fuer private Dokumentdaten erheblich, besonders falls die API jemals bewusst oder versehentlich im Netzwerk erreichbar ist.
- **Fix**:
  - Fuer das KI-Suchfeature ein eigenes minimiertes Suchdatenmodell definieren.
  - `content_text` nicht blind als Standard fuer jede Suchfunktion weiterverwenden.
  - `/recent` auf explizit erlaubte Felder beschraenken statt rohe DB-Records auszugeben.
  - Fuer neue AI-Suchpfade nur die noetigen Felder an den Modelllayer geben.
- **Mitigation**:
  - `store_content` fuer privacy-kritische Setups deaktivierbar lassen und dokumentieren.
  - Sensible Inhalte nicht in API- oder UI-Responses ausliefern, wenn Summary oder Titel ausreichen.
- **False positive notes**:
  - Wenn die App strikt lokal bleibt, ist das Risiko kleiner, aber nicht null. Lokale Malware, Mehrbenutzer-Systeme oder spaetere Netzfreigaben vergroessern die Auswirkung deutlich.

### KBP-002: Geplante KI-Suche haette ohne Schutzmassnahmen einen realistischen Prompt-Injection-Pfad aus Dokumentinhalt
- **Rule ID**: KBP-002
- **Severity**: High
- **Location**:
  - [src/arkiv/core/engine.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/engine.py#L50) bis [src/arkiv/core/engine.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/engine.py#L70)
  - [src/arkiv/core/classifier.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/classifier.py#L27) bis [src/arkiv/core/classifier.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/classifier.py#L54)
  - [src/arkiv/core/classifier.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/classifier.py#L129) bis [src/arkiv/core/classifier.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/classifier.py#L159)
  - [src/arkiv/core/llm.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/llm.py#L35) bis [src/arkiv/core/llm.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/llm.py#L60)
- **Evidence**:
  - Dokumentinhalt wird direkt in Prompts eingebettet: `Content: --- {content} ---`
  - OCR-Text und Dateiinhalt werden ungefiltert extrahiert und weitergereicht.
  - Die bestehende LLM-Architektur arbeitet mit direkten Chat-Prompts gegen lokal oder extern laufende Modelle.
- **Impact**:
  - Sobald die Erinnerungssuche spaeter Trefferinhalte, OCR-Text oder Summaries zur LLM-basierten Erklaerung oder Nachsortierung verwendet, kann boesartiger Dokumenttext versuchen, das Modell umzulenken.
  - Ein manipuliertes Dokument koennte die Suchbegruendung verfremden, irrelevante Treffer pushen oder spaeter Tools / Filter / Erklaerungen beeinflussen.
- **Fix**:
  - Fuer die KI-Suche Retrieval und LLM strikt trennen.
  - Das LLM in Phase 1 nur die Nutzeranfrage verarbeiten lassen, nicht direkt den Rohtext aller Dokumente.
  - Fuer Phase 2 bei Treffer-Erklaerungen nur kleinste, kontrollierte Ausschnitte und stark eingegrenzte Aufgaben an das Modell geben.
  - Striktes JSON-Schema und defensives Prompting einsetzen.
- **Mitigation**:
  - Keine freien Tool-Aufrufe oder Agentenlogik auf Basis von Dokumentinhalt.
  - Dokumenttext niemals als "Instruktion" behandeln, sondern nur als Datenquelle.
  - Alle LLM-Ausgaben fuer Suche als untrusted behandeln und nicht ungeprueft in die UI oder weitere Logik einspeisen.
- **False positive notes**:
  - Das ist im aktuellen Stand hauptsaechlich ein Architektur-Risiko fuer das geplante Feature, noch kein bestaetigter Endpunkt-Exploit in der heutigen Suche. Gerade deshalb sollte es vor Sprint 2 abgefangen werden.

## Medium Severity

### KBP-003: Die Such- und UI-Ausgabe zeigt heute volle lokale Pfade und vergroessert damit die Privacy-Leckflaeche
- **Rule ID**: KBP-003
- **Severity**: Medium
- **Location**:
  - [src/arkiv/dashboard/templates/partials/search_results.html](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/templates/partials/search_results.html#L16) bis [src/arkiv/dashboard/templates/partials/search_results.html](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/templates/partials/search_results.html#L17)
  - [src/arkiv/dashboard/routes.py](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/routes.py#L67) bis [src/arkiv/dashboard/routes.py](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/routes.py#L79)
  - [src/arkiv/cli.py](/Users/ten.december/claude-projects/kurier/src/arkiv/cli.py#L336) bis [src/arkiv/cli.py](/Users/ten.december/claude-projects/kurier/src/arkiv/cli.py#L352)
- **Evidence**:
  - Dashboard zeigt `{{ item.original_path }}`
  - CLI-Suche arbeitet stark mit Summary, Route und Datum; die kommende Erinnerungssuche wuerde diese Ausgabe voraussichtlich erweitern
- **Impact**:
  - Lokale Pfade enthalten oft Namen, Firmennamen, Projektstrukturen oder private Ordnernamen.
  - Eine spaetere KI-Suche mit Erklaerungen kann dieses Problem vergroessern, wenn sie Pfade, Zielorte oder Dokumentdetails ungefiltert in Trefferbeschreibungen mischt.
- **Fix**:
  - Fuer Suchtreffer ein minimiertes Anzeigeobjekt definieren, z. B. `display_title`, `category`, `summary`, `created_at`, `route_name`.
  - Vollstaendige Pfade nur auf ausdruecklichen Wunsch in einer Detailansicht zeigen.
- **Mitigation**:
  - Im Dashboard und in API-Responses standardmaessig nur basename oder abstrahierte Titel anzeigen.
- **False positive notes**:
  - Wenn Kurier ausschliesslich fuer Einzelplatz-Lokalnutzung gedacht ist, ist das eher ein Privacy- als ein klassischer Remote-Security-Fund. Fuer das geplante Feature bleibt es trotzdem relevant.

### KBP-004: Fehlertexte werden roh an API- und Dashboard-Clients zurueckgegeben
- **Rule ID**: KBP-004
- **Severity**: Medium
- **Location**:
  - [src/arkiv/inlets/api.py](/Users/ten.december/claude-projects/kurier/src/arkiv/inlets/api.py#L134) bis [src/arkiv/inlets/api.py](/Users/ten.december/claude-projects/kurier/src/arkiv/inlets/api.py#L148)
  - [src/arkiv/inlets/api.py](/Users/ten.december/claude-projects/kurier/src/arkiv/inlets/api.py#L136) bis [src/arkiv/inlets/api.py](/Users/ten.december/claude-projects/kurier/src/arkiv/inlets/api.py#L138)
  - [src/arkiv/dashboard/routes.py](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/routes.py#L101) bis [src/arkiv/dashboard/routes.py](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/routes.py#L123)
- **Evidence**:
  - `raise HTTPException(status_code=500, detail=str(e))`
  - Dashboard rendert `message=str(e)` in Fehlerfaellen
- **Impact**:
  - Interne Pfade, Provider-Fehler, Dateisystemdetails oder Parserfehler koennen an Clients sichtbar werden.
  - Bei spaeteren AI-Suchfehlern koennte das auch Modellantworten oder Prompt-bezogene Interna preisgeben.
- **Fix**:
  - Fuer Clients generische Fehlermeldungen verwenden.
  - Details nur serverseitig loggen.
- **Mitigation**:
  - Fehlermeldungen in UI/API in sichere Kategorien mappen: Upload fehlgeschlagen, Suchverarbeitung fehlgeschlagen, Modell nicht verfuegbar.
- **False positive notes**:
  - Fuer lokale Entwicklung ist das hilfreich. Fuer produktive oder geteilte Nutzung ist es ein unnötiger Informationsabfluss.

### KBP-005: Schutzmechanismen fuer HTML-Ausgabe sind nur teilweise sichtbar; vor KI-generierten Erklaerungen sollte die Browser-Haertung explizit geklaert werden
- **Rule ID**: KBP-005
- **Severity**: Medium
- **Location**:
  - [src/arkiv/dashboard/routes.py](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/routes.py#L29) bis [src/arkiv/dashboard/routes.py](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/routes.py#L45)
  - [src/arkiv/dashboard/templates/base.html](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/templates/base.html#L1) bis [src/arkiv/dashboard/templates/base.html](/Users/ten.december/claude-projects/kurier/src/arkiv/dashboard/templates/base.html#L33)
- **Evidence**:
  - Jinja2 `autoescape=True` ist sichtbar, das ist gut.
  - Eine explizite CSP oder andere Browser-Schutzheader sind im App-Code nicht sichtbar.
- **Impact**:
  - Heute ist das Risiko begrenzt, weil die Templates simpel sind und autoescape aktiv ist.
  - Mit spaeteren KI-Erklaerungen, hervorgehobenen Treffern oder reichhaltigeren UI-Texten steigt der Schaden einer Rendering-Schwachstelle.
- **Fix**:
  - Vor Sprint 2 klaeren, wo CSP und weitere Browser-Schutzheader gesetzt werden.
  - KI-Erklaerungen ausschliesslich als Text rendern, nicht als HTML.
- **Mitigation**:
  - Keine Verwendung von `innerHTML` oder vergleichbaren HTML-Sinks fuer spaetere Sucherlaeuterungen.
  - Header-Setup zur Laufzeit oder im Reverse Proxy dokumentieren.
- **False positive notes**:
  - Es ist moeglich, dass CSP oder weitere Schutzheader ausserhalb des App-Codes gesetzt werden. Das ist hier nicht sichtbar und muss zur Laufzeit verifiziert werden.

## Empfehlungen vor Sprint 2

### Muss vor der Implementierung feststehen
1. Das KI-Suchfeature darf **nicht** auf "LLM denkt frei ueber alle Dokumente nach" aufbauen.
2. Treffer- und API-Ausgabe muessen auf minimale Suchfelder reduziert werden.
3. `suggested_filename` sollte als eigenes Suchsignal gespeichert werden, statt spaeter ueber rohe Pfade zu leaken.
4. Das LLM darf fuer die erste Version nur Query-Verstehen und eng begrenzte Treffer-Erklaerung machen.

### Sollte frueh in Sprint 2 passieren
1. Such-Responses als eigene DTOs definieren, nicht rohe DB-Dicts.
2. `recent` und aehnliche Endpunkte minimieren.
3. Fehlertexte fuer Clients haerten.
4. Security-Guardrails fuer den LLM-Pfad dokumentieren:
   - nur JSON
   - keine freie Tool-Logik
   - Dokumenttext bleibt Datenquelle, nie Instruktionsquelle

## Zusammenfassung fuer die Produktentscheidung
Das KI-Suchfeature ist **machbar**, aber nur sicher und datenschutzfreundlich, wenn es von Anfang an als kontrollierte Suchhilfe gebaut wird.

Die wichtigste praktische Leitlinie ist:

**Nicht das LLM suchen lassen.  
Die Suche soll echte Treffer liefern, und das LLM darf nur beim Verstehen und knappen Erklaeren helfen.**
