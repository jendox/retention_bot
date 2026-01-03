# Loki + Grafana + Promtail (logs)

This stack lets you search Docker container logs in a web UI.

## Prerequisites

- Docker logging driver should be `json-file` (default).
- The `promtail` container must be able to read:
  - `/var/lib/docker/containers` (read-only)
  - `/var/run/docker.sock` (read-only) for container discovery/labels

## Start

From the repo folder on the VPS:

- `export GRAFANA_ADMIN_PASSWORD='<strong-password>'`
- `docker compose -f docker-compose.logging.yml up -d`

Grafana is bound to localhost on port 3000:
- Open via SSH tunnel: `ssh -L 3000:127.0.0.1:3000 root@<vps-ip>`
- Then open `http://127.0.0.1:3000` and login (`admin` / your password).

## Find bot logs

In Grafana → Explore, pick Loki and use queries like:

- `{compose_service="bot"}`
- `{compose_service="bot"} |= "bot.unhandled_exception"`
- `{compose_service="payments", level="ERROR"}`
- `{compose_service="reminders"} | json | event="workers.reminders.heartbeat"`

