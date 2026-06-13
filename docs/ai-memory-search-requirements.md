# AI Memory Search Requirements

**Status**: Draft for Sprint 1  
**Date**: 2026-04-09

## Zielbild
Kurier soll eine Erinnerungssuche bekommen, die auch dann gute Treffer findet, wenn Nutzer den exakten Dateinamen oder Titel nicht mehr kennen.

Die Suche soll nicht "frei raten", sondern auf echten Suchtreffern aufbauen:

1. Nutzer beschreibt die gesuchte Datei in Alltagssprache.
2. Ein lokales LLM versteht die Beschreibung und erzeugt Suchvarianten sowie moegliche Filter.
3. Die bestehende Hybrid-Suche holt echte Kandidaten aus dem Index.
4. Optional erklaert ein leichtes Reranking, warum ein Treffer wahrscheinlich passt.

## Was Erfolg hier bedeutet
Eine gute Erinnerungssuche ist nicht einfach "intelligent klingend", sondern in der Praxis hilfreich:

- Sie findet relevante Dokumente trotz ungenauer Formulierung.
- Sie bleibt lokal und datensparsam.
- Sie halluziniert keine Dokumente, die es nicht gibt.
- Sie gibt bei Unsicherheit lieber mehrere plausible Treffer aus.
- Sie erklaert kurz, warum ein Treffer passt.

## Nicht-Ziele
- Kein freies Chatten ueber den gesamten Dokumentbestand.
- Kein Cloud-Zwang fuer die erste Version.
- Kein vollstaendiger Ersatz der bestehenden Hybrid-Suche.
- Keine "magische" Antwort ohne nachvollziehbare Trefferbasis.

## Bestehende Grundlage im Repo
Kurier hat bereits wichtige Bausteine:

- Hybrid-Suche aus FTS + Vektor in [engine.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/engine.py) und [store.py](/Users/ten.december/claude-projects/kurier/src/arkiv/db/store.py)
- Lokales Ollama-LLM als Default in [config.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/config.py)
- Klassifikation mit `summary`, `tags` und `suggested_filename` in [classifier.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/classifier.py)
- Umbenennung mit sprechendem Dateinamen in [router.py](/Users/ten.december/claude-projects/kurier/src/arkiv/core/router.py)

## Offene Produktluecke
Die aktuelle Suche ist bereits staerker als reine Stichwortsuche, aber fuer Erinnerungssuche fehlen noch zwei Dinge:

1. Der durch das LLM erzeugte `suggested_filename` wird bisher nicht als eigenes Suchsignal gespeichert.
2. Die Nutzeroberflaechen bieten noch keinen Suchmodus fuer vage Beschreibungen mit Begruendung.

Das ist wichtig, weil sich Nutzer oft eher an den "sprechenden Titel" eines Dokuments erinnern als an den urspruenglichen Dateinamen.

## Erfolgsfaelle fuer Sprint 1 und 2
Die ersten Suchfaelle sollen bewusst alltagsnah sein.

### Dokumenttyp: Rechnung
- "die Telekom-Rechnung vom Fruehling"
- "irgendeine Internetrechnung von 2026"
- "die Handyrechnung mit Telekom oder Vodafone"

### Dokumenttyp: Versicherung / Krankenkasse
- "dieses Schreiben von meiner Krankenkasse wegen Beitrag"
- "ein Brief von der Versicherung, keine Rechnung"
- "das eingescannte Dokument von der Allianz"

### Dokumenttyp: Vertrag
- "mein Mietvertrag aus Wuerzburg"
- "der Vertrag fuer die Wohnung in der Schillerstrasse"
- "ein alter Vertrag mit Adresse drin"

### Dokumenttyp: Bescheid / Brief
- "ein amtliches Schreiben wegen Steuern"
- "dieser Bescheid von letztem Jahr"
- "ein Brief vom Amt mit Datum 2025"

### Dokumenttyp: OCR-lastige Scans
- "das eingescannte Blatt mit kaum lesbarem Text von der Krankenkasse"
- "dieses PDF, wo der Dateiname nichts sagt, aber es war ein offizieller Brief"

## Erwartetes Sollverhalten pro Anfrage
Fuer jede Anfrage soll die erste Version mindestens Folgendes leisten:

- 2-5 brauchbare Suchvarianten erzeugen
- erkennbare Filter-Hinweise ableiten, wenn moeglich:
  - Firma / Absender
  - Zeitraum
  - Dokumenttyp
  - Thema
- echte Treffer aus dem bestehenden Index holen
- kurze Begruendung liefern, zum Beispiel:
  - "passt wegen Telekom im OCR-Text und Zusammenfassung"
  - "passt wegen Versicherung, Brief-Kategorie und Zeitraum"

## Datenmodell-Luecken fuer Sprint 2
Diese Punkte muessen fuer den spaeteren MVP sehr wahrscheinlich erweitert werden:

### 1. `suggested_filename` speichern
Aktuell wird `suggested_filename` fuer das Umbenennen benutzt, aber nicht als eigenes Feld in der DB gehalten. Fuer Erinnerungssuche ist das ein starkes Signal und sollte separat gespeichert werden.

### 2. Suchfreundlicher Zielname / Zielpfad
`destination` wird zwar gespeichert, aber aktuell nicht in den FTS-Index aufgenommen. Mindestens der sichtbare Ziel-Dateiname sollte als Suchsignal oder Anzeige-Feld besser nutzbar sein.

### 3. Erklaerbare Trefferanzeige
Dashboard, CLI und TUI zeigen heute vor allem Summary und Pfade. Fuer Erinnerungssuche wird zusaetzlich ein sprechender Titel und spaeter eine kurze Match-Erklaerung gebraucht.

## Empfohlene Datenmodell-Richtung fuer Sprint 2
Damit Sprint 2 nicht ins Leere baut, ist die Richtung schon jetzt relativ klar.

### Neue oder abgeleitete Felder
- `suggested_filename`
  - vom Klassifizierer erzeugter sprechender Dokumentname
- `destination_name`
  - nur der Dateiname aus `destination`, ohne kompletten Pfad
- `display_title`
  - bevorzugter sichtbarer Titel fuer Treffer, zum Beispiel:
    1. `suggested_filename`
    2. `destination_name`
    3. Dateiname aus `original_path`
- optional spaeter `search_title`
  - normalisierte Suchvariante des sichtbaren Titels

### Warum diese Aufteilung sinnvoll ist
- `suggested_filename` ist stark fuer Erinnerungssuche.
- `destination_name` hilft beim Wiederfinden ohne kompletten Pfadleak.
- `display_title` trennt Such-/UI-Bedarf von rohen Speicherfeldern.

### FTS-Erweiterung
Fuer den MVP sollte die FTS-Seite nicht nur `original_path`, `summary`, `tags` und `content_text` sehen, sondern mindestens auch:

- `suggested_filename`
- `destination_name`
- optional `display_title`

So kann die Suche auf den sprechenden Namen reagieren, unter dem Nutzer ein Dokument spaeter mental wiedererkennen.

### API- und UI-Ausgabe fuer den MVP
Die spaetere Suche sollte keine rohen DB-Dicts direkt an Clients geben. Stattdessen braucht es ein minimiertes Ausgabeobjekt wie:

```json
{
  "id": 123,
  "display_title": "Rechnung Telekom Maerz 2026",
  "category": "rechnung",
  "summary": "Telekom-Rechnung fuer Maerz 2026",
  "route_name": "archiv",
  "created_at": "2026-03-23T10:15:00Z",
  "match_reason": "Telekom und Rechnung passen zu Anfrage und Suchvarianten."
}
```

Das ist fuer Nutzer klarer und datensparsamer als rohe Pfade oder komplette Record-Ausgaben.

## JSON-Zielstruktur fuer den spaeteren Query-Assist
Die erste LLM-Stufe sollte keine freie Prosa zurueckgeben, sondern etwas in diesem Stil:

```json
{
  "rewrites": [
    "Telekom Rechnung Fruehling 2026",
    "Internetrechnung Telekom 2026",
    "Handyrechnung Telekom"
  ],
  "filters": {
    "category": ["rechnung"],
    "organizations": ["Telekom"],
    "date_hints": ["2026", "Fruehling"]
  },
  "notes": "Nutzer erinnert sich an Rechnung eines Telekommunikationsanbieters."
}
```

## Minimaler MVP-Scope
Die erste baubare Version sollte nur Folgendes koennen:

- natuerliche Suchbeschreibung annehmen
- Suchvarianten + Filter lokal erzeugen
- bestehende Hybrid-Suche mehrfach anwerfen
- Treffer zusammenfuehren
- kurze, vorsichtige Begruendung liefern

Alles darueber hinaus ist spaeter:

- Rueckfragen-Dialog
- persoenliche Suchhistorie
- lernendes Nutzerfeedback
- automatische Query-Korrektur ueber mehrere Runden

## Harte Qualitaetskriterien
- Kein Dokument darf "erfunden" werden.
- JSON-Ausgabe des Modells muss stabil parsebar sein.
- Unsicherheit muss sichtbar bleiben.
- Private Inhalte duerfen nicht unnoetig weitergegeben oder gespeichert werden.
- Die Suche muss lokal praktikabel bleiben und darf nicht traege wirken.
