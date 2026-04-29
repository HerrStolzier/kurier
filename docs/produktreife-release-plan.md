# Produktreife Release Plan: Demo-RC mit Raspberry-Pi-n8n

Stand: 2026-04-29

## Ziel

Dieser Release Candidate soll ehrlich demo-fertig werden. Das bedeutet: Kurier startet sauber, repariert die lokale Suchdatenbank ohne Item-Verlust, die Tests/CI laufen enger, und der Webhook-Weg kann gegen einen privaten n8n-Empfaenger auf einem Raspberry Pi bewiesen werden.

## Status

| Bereich | Status | Naechster Beweis |
|---|---|---|
| DB-Reparatur | Lokal verifiziert | `kurier doctor --repair-db`, `kurier status`, `kurier doctor` und `kurier audit` sind lokal sauber |
| CI/Test-Angleichung | Lokal verifiziert | `scripts/release-check` ist lokal gruen; GitHub CI bleibt der Remote-Beweis |
| Fresh install smokes | Lokal verifiziert | `scripts/smoke-editable-install` und `scripts/smoke-wheel-install` sind lokal gruen |
| Raspberry Pi n8n | Vorbereitet | Pi ins LAN haengen, dann `PI_HOST=... scripts/pi-n8n-setup` |
| n8n Workflow | Manuell offen | Workflow `kurier-demo-webhook` im n8n Editor anlegen und aktivieren |
| Kurier -> n8n Demo | Offen bis Pi erreichbar | Demo-Config aus `examples/kurier-n8n-demo.config.toml` verwenden |

## Geplante Reihenfolge

1. Lokale Checks laufen lassen:

   ```bash
   scripts/release-check
   scripts/smoke-editable-install
   scripts/smoke-wheel-install
   ```

   Status: erledigt am 2026-04-29.

2. Lokale Kurier-Datenbank reparieren:

   ```bash
   kurier service off
   kurier doctor --repair-db
   kurier status
   ```

   Status: erledigt am 2026-04-29. Backup wurde unter `~/.local/share/kurier/` angelegt.

3. Inbox und Review bereinigen:

   Status: erledigt am 2026-04-29.

   - 2 Inbox-Dateien verarbeitet
   - 1 Review-Datei ueber `kurier audit --fix` neu klassifiziert
   - `kurier audit` meldet danach keine offenen Befunde

4. Raspberry Pi bereitstellen:

   ```bash
   PI_HOST=raspberrypi.local PI_USER=pi scripts/pi-n8n-setup
   ```

   Status: offen. `raspberrypi.local` ist aktuell noch nicht im LAN aufloesbar.

5. n8n Workflow anlegen:

   - Workflow: `kurier-demo-webhook`
   - Webhook: `POST /kurier-demo`
   - Produktive URL: `http://PI_HOST:5678/webhook/kurier-demo`

6. Kurier-Demo gegen n8n ausfuehren:

   ```bash
   cp examples/kurier-n8n-demo.config.toml /tmp/kurier-n8n-demo.toml
   perl -pi -e 's/PI_HOST/192.168.178.50/g' /tmp/kurier-n8n-demo.toml
   uv run kurier doctor --fix --config /tmp/kurier-n8n-demo.toml
   uv run kurier add /tmp/kurier-n8n-demo/rechnung.txt --config /tmp/kurier-n8n-demo.toml
   ```

7. Doku erst nach echtem Smoke hochstufen:

   - Webhook-Status darf erst nach echtem Pi/n8n POST sagen: gegen lokalen n8n-Empfaenger real geprueft.
   - Slack/Discord bleiben nicht als stabil behauptet, solange sie nicht separat live geprueft wurden.

## Sicherheitsgrenzen

- Kein Router-Portforwarding.
- Kein oeffentlicher Tunnel.
- Keine Secrets in Git.
- `.env` auf dem Pi bleibt unter `/opt/n8n/.env` mit Modus `0600`.
- Demo-Config bleibt temporaer und verwendet nur harmlose Demo-Dateien.
