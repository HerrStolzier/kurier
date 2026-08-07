# Known Errors

> **Zweck:** Bekannte Fehler in Kurier mit Symptom, Ursache und Loesung.
> **Scope:** Ruff-Format-Drift, lokale DB-Zustaende, Webhook-Zustellung, Watcher-Stabilitaetsheuristik.
> **Suchbegriffe:** ruff, format, drift, database, status, webhook, delivery, watcher, stability, retry, on_modified, failed, fehlgeschlagen, nicht geschafft, friendly_error
> **Stand:** 2026-08-07

## Global Ruff Format Drift

### Symptom

`uv run ruff format --check src/ tests/` meldet bestehende Dateien, die formatiert wuerden.

### Ursache

Einige Altdateien sind nicht im aktuellen Ruff-Format. Das ist unabhaengig von kleineren, gezielten Aenderungen.

### Loesung

Bei kleinen Fixes nur die beruehrten Python-Dateien formatieren oder formatpruefen. Einen globalen Format-Diff nur als separate Aufraeum-Aenderung machen.

## Local Database Malformed Status

### Symptom

`kurier status` kann mit `DatabaseError: database disk image is malformed` abbrechen, obwohl `sqlite3 integrity_check` `ok` meldet.

### Ursache

Wahrscheinlich sind abgeleitete Daten wie FTS-, Vector- oder Store-Migrationen betroffen, nicht zwingend die eigentlichen Item-Daten.

### Loesung

Keine Daten hart loeschen. Erst sichern, dann abgeleitete Indizes oder Migrationszustand reparieren. Zielpfad ist `kurier doctor --repair-db`.

## Webhook Delivery Failures

### Symptom

Webhook-Ziele sind nicht erreichbar oder antworten fehlerhaft.

### Ursache

Lokale oder LAN-Ziele wie n8n koennen offline sein. Externe Ziele koennen transient fehlschlagen.

### Loesung

Fehlgeschlagene Webhooks ueber die lokale Outbox/Retry-Logik sichtbar halten und mit `kurier webhooks retry` erneut senden.

## Watcher verpasst Nachschreiben nach Schreibpause

Behoben am 2026-08-04. Gefunden im Cross-Model-Review desselben Tages (P2).
Hier dokumentiert, weil das Symptom in aelteren Staenden auftritt.

### Symptom

Eine Datei, deren Erzeuger laenger als rund eine Sekunde pausiert und danach
weiterschreibt, wird in der unfertigen Fassung verarbeitet. Die fertige Fassung
loest keine erneute Verarbeitung mehr aus. Sichtbar vor allem bei
Webhook-only-Routen, weil die Datei dort im Eingang liegen bleibt: gesendet wird
nur der Torso.

### Ursache

`_wait_until_stable()` in `src/arkiv/inlets/watch.py` haelt eine Datei fuer
fertig, sobald die Groesse bei zwei Messungen im Abstand von 0,5 s gleich
bleibt. Eine laengere Schreibpause sieht damit aus wie ein Dateiende. Seit
Commit d760e1b verarbeitet `on_modified()` nur noch Pfade aus
`_retry_pending`, also nur Dateien, deren Stabilitaetswartezeit in den Timeout
lief. Ein faelschlich als stabil erkannter Pfad steht dort nicht drin, spaetere
Modify-Events laufen deshalb ins Leere. Vor d760e1b haette der zweite
Schreibvorgang eine erneute Verarbeitung ausgeloest.

### Loesung

Der Handler merkt sich jetzt pro Pfad die zuletzt verarbeitete Signatur aus
Groesse und mtime (`_processed` in `src/arkiv/inlets/watch.py`). Ein
Modify-Event verarbeitet erneut, wenn diese Signatur sich unterscheidet, und
wird sonst ignoriert. Damit bleiben Webhook-only-Dateien vor Doppelverarbeitung
geschuetzt, und wirklich weitergeschriebene Dateien kommen noch einmal dran. Der
Cooldown greift auf diesem Pfad bewusst nicht: der Erzeuger kann innerhalb des
Cooldown-Fensters fertig werden, und danach kommt kein Event mehr.

## Fehlgeschlagene Dateien landen als 'Nicht geschafft' im Dashboard

### Symptom

Eine Datei (z.B. defekte PDF) wird nicht einsortiert. Frueher: keinerlei Spur in der
Datenbank, die Datei blieb kommentarlos im Eingang liegen.

### Ursache

Bis 2026-08-07 las die Pipeline die Datei VOR dem ersten DB-Eintrag; Lese- und
Klassifikationsfehler verliessen `ingest_file()` ohne Datensatz.

### Loesung

Seit UX Phase 5 speichert die Engine solche Fehlschlaege als `status='failed'` mit
verstaendlichem Grund (`src/arkiv/core/errors.py`). Sichtbar im Dashboard-Tab
'Nicht geschafft' und in `kurier doctor`. Wiederholte Fehlschlaege derselben Datei
werden per Upsert zusammengefasst (`upsert_failure` in `src/arkiv/db/store.py`),
und ein failed-Eintrag blockiert eine spaetere erfolgreiche Verarbeitung nicht.
