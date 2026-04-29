# Raspberry Pi n8n Demo Receiver

This guide sets up a private n8n receiver for the Kurier webhook demo.

The goal is deliberately small: Kurier sends a real POST to n8n inside the local network. No public tunnel, no router port forwarding, no public webhook URL.

## What We Use

- Raspberry Pi in the same LAN as the Mac
- SSH access to the Pi
- Docker Compose on the Pi
- n8n on port `5678`
- n8n data persisted in `/opt/n8n/data`
- shared local files directory at `/opt/n8n/local-files`

The setup follows the official n8n Docker guidance: n8n exposes port `5678`, persists `/home/node/.n8n`, uses SQLite by default, and supports Docker Compose based self-hosting.

References:

- <https://docs.n8n.io/hosting/installation/docker/>
- <https://docs.n8n.io/hosting/installation/server-setups/docker-compose/>

## 1. Find The Pi

Try the local hostname first:

```bash
ssh pi@raspberrypi.local
```

If that fails, use the IP address from the router/DHCP list:

```bash
ssh pi@192.168.178.50
```

If SSH still asks for a password and you want key-based login:

```bash
ssh-copy-id pi@PI_HOST
```

## 2. Run The Setup Script

From the repo root:

```bash
PI_HOST=raspberrypi.local PI_USER=pi scripts/pi-n8n-setup
```

Or with an IP address:

```bash
PI_HOST=192.168.178.50 PI_USER=pi scripts/pi-n8n-setup
```

The script checks the Pi, sets the hostname to `kurier-n8n-pi`, sets timezone `Europe/Berlin`, installs Docker if needed, creates `/opt/n8n`, writes `/opt/n8n/.env` with `0600`, and starts n8n through Docker Compose.

It does not configure router port forwarding.

## 3. Check n8n

On the Mac:

```bash
curl http://PI_HOST:5678/
```

On the Pi:

```bash
ssh pi@PI_HOST
cd /opt/n8n
sudo docker compose ps
cat /opt/n8n/DEPLOYMENT.md
```

## 4. Create The n8n Workflow

In the n8n editor, create a workflow named `kurier-demo-webhook`.

Use a Webhook node:

- Method: `POST`
- Path: `kurier-demo`

Add an Edit Fields or Code node if you want to show only the important fields:

- `event`
- `item.category`
- `item.route_name`
- `item.summary`
- `item.tags`

Activate the workflow.

Production URL for Kurier:

```text
http://PI_HOST:5678/webhook/kurier-demo
```

Use the n8n test URL only while the workflow editor is in test mode.

## 5. Smoke Test The Webhook

```bash
scripts/demo-n8n-smoke http://PI_HOST:5678/webhook/kurier-demo
```

This proves that n8n receives a POST. It does not yet prove Kurier classification. That comes next.

## 6. Test Kurier Against n8n

Use a temporary config, not the real main config:

```bash
cp examples/kurier-n8n-demo.config.toml /tmp/kurier-n8n-demo.toml
perl -pi -e 's/PI_HOST/192.168.178.50/g' /tmp/kurier-n8n-demo.toml
mkdir -p /tmp/kurier-n8n-demo
printf 'Demo-Rechnung ueber 42,00 EUR fuer den Kurier n8n Test.\n' \
  > /tmp/kurier-n8n-demo/rechnung.txt
uv run kurier doctor --fix --config /tmp/kurier-n8n-demo.toml
uv run kurier add /tmp/kurier-n8n-demo/rechnung.txt --config /tmp/kurier-n8n-demo.toml
```

Success means:

- Kurier processes the demo file.
- n8n receives a real POST.
- The payload contains category, summary, route, and tags.
- No private paths, webhook secrets, or local `.env` files are committed.

## Notes

For this private LAN demo, plain HTTP is acceptable. For public access, this would need HTTPS, authentication, and a stronger threat model. That is intentionally outside this demo.
