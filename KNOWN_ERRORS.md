# Known Errors

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
