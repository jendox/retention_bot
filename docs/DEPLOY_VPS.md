# VPS deploy (docker compose + systemd Postgres/Redis)

Target setup:
- `bot`, `reminders`, `payments` run via `docker compose` using host networking.
- Postgres and Redis run on the VPS as systemd services.
- Prometheus runs as a container (optional).

## 1) Postgres (systemd)

- Bind only to localhost.
- Create a dedicated user + database for the bot.

Example checks:
- `ss -ltnp | rg ':5432'`
- `psql -h 127.0.0.1 -U retention_bot -d retention_bot -c 'select 1'`

## 2) Redis (systemd)

- Bind only to localhost.
- Configure auth (`requirepass`) or a unix socket.

Example checks:
- `ss -ltnp | rg ':6379'`
- `redis-cli -h 127.0.0.1 -a '<PASSWORD>' ping`

## 3) Production env file

Create `.env.prod` on the VPS (do not commit it). Start from `.env.prod.example`.

Important note with `network_mode: host`:
- `localhost` in container points to the VPS (good for systemd Postgres/Redis).

## 4) Run DB migrations

From the repo folder on the VPS:
- `docker compose -f docker-compose.prod.yml pull`
- `docker compose -f docker-compose.prod.yml run --rm bot alembic upgrade head`

## 5) Start services

- `docker compose -f docker-compose.prod.yml up -d`
- `docker compose -f docker-compose.prod.yml ps`
- `docker compose -f docker-compose.prod.yml logs -f bot`

## 6) Metrics (optional but recommended)

With host networking each process must use a different metrics port.
`docker-compose.prod.yml` assigns:
- bot: `127.0.0.1:8000/metrics`
- reminders: `127.0.0.1:8001/metrics`
- payments: `127.0.0.1:8002/metrics`
- prometheus UI: `127.0.0.1:9090`

Smoke checks on the VPS:
- `curl -fsS http://127.0.0.1:8000/metrics | head`
- `curl -fsS http://127.0.0.1:9090/-/healthy`

