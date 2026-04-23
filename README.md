# Retention Bot · BeautyDesk

Production-oriented **Telegram** bot for service businesses, with **hair and beauty** as the primary use case. Service providers (masters) run schedules, bookings, and client retention; clients book time slots and receive reminders.

Built with **Python 3.13**, **aiogram 3**, **async SQLAlchemy** + **PostgreSQL**, **Redis** (FSM, rate limits, heartbeats), background **workers** for reminders and payment status polling, optional **ExpressPay** integration, **Pydantic Settings**, **Alembic** migrations, structured **observability** (JSON logs, Prometheus metrics, admin alerts, append-only DB audit log), and **CI** (tests, Docker image build, secret scanning). A solid showcase of async Python, service-oriented layout, and operations-minded design.

---

## Capabilities

| Area | What’s included |
|------|-----------------|
| **Service provider (master)** | Sign-up (including invite-only), schedule and slots, create / reschedule / cancel bookings, client list, invite links, notification settings, Pro subscription and plan limits |
| **Client** | Find a master, book appointments, list bookings, settings, personal-data policy consent |
| **Payments** | Pro checkout via **ExpressPay** (optional; dedicated payment worker for status polling) |
| **Reliability** | Notification outbox, reminder worker, worker heartbeat watchdog, log sampling |

For business events and analytics naming conventions, see [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md).

## Architecture

```mermaid
flowchart TB
  subgraph users [Users]
    M[Service provider]
    C[Client]
  end

  TG[Telegram API]

  subgraph app [Processes]
    B["Bot: src.main\naiogram + FSM"]
    R["Worker: src.workers.reminders"]
    P["Worker: src.workers.payments"]
  end

  DB[(PostgreSQL)]
  RS[(Redis)]

  M & C --> TG
  TG <--> B
  B <--> DB
  B <--> RS
  R <--> DB
  R <--> RS
  P <--> DB
  P <--> RS
```

## Tech stack

| Category | Stack |
|----------|--------|
| Runtime | Python **3.13** (asyncio) |
| Telegram | **aiogram** 3.x |
| Data | **PostgreSQL** (asyncpg), **SQLAlchemy 2** async, **Alembic** |
| State / cache | **Redis** (FSM storage, rate limiting, service keys) |
| Configuration | **Pydantic Settings** |
| Code quality | **Ruff**, **mypy** (project dependencies) |
| Observability | JSON logs, **Prometheus** metrics, optional admin alerts |
| CI | **GitHub Actions** — tests (Postgres + Redis in Docker), **Docker** image build, secret scanning |

## Repository layout (high level)

| Path | Role |
|------|------|
| `src/main.py` | Bot entrypoint, middleware, dispatcher, worker watchdog |
| `src/handlers/` | Command and flow handlers (admin, client, master, billing, …) |
| `src/workers/` | Background jobs: reminders, payments |
| `src/repositories/`, `src/use_cases/` | Data access and application logic |
| `src/integrations/expresspay/` | ExpressPay client |
| `src/observability/` | Logging, metrics, alerts |
| `src/migrations/` | Database migrations (Alembic) |
| `tests/` | Unit and integration tests (`unittest`) |
| `docs/` | Deployment, observability, production logging notes |

## Local development

You need **Python 3.13** and [**uv**](https://docs.astral.sh/uv/).

1. **Install dependencies**

   ```bash
   uv sync
   ```

2. **Infrastructure (Postgres + Redis)**

   ```bash
   docker compose -f docker-compose.yml up -d postgres redis
   ```

3. **Environment** — set `TELEGRAM__BOT_TOKEN`, `TELEGRAM__BOT_USERNAME`, `DATABASE__POSTGRES_URL`, `DATABASE__REDIS_URL` (see [`.env.prod.example`](.env.prod.example) and the test job in [`.github/workflows/tests.yml`](.github/workflows/tests.yml)).

4. **Migrations**

   ```bash
   uv run alembic upgrade head
   ```

5. **Run the bot**

   ```bash
   uv run python -m src.main
   ```

6. **Tests** (same as CI: Postgres and Redis up, `INTEGRATION_TESTS=1`):

   ```bash
   export INTEGRATION_TESTS=1
   export TELEGRAM__BOT_TOKEN=dummy
   export TELEGRAM__BOT_USERNAME=dummy_bot
   export DATABASE__POSTGRES_URL=postgresql+asyncpg://retention_bot:retention_bot_dev_password@localhost:5432/retention_bot
   export DATABASE__REDIS_URL=redis://localhost:6379/0
   uv run python -m unittest -v
   ```

## Production

Step-by-step VPS deploy (Docker Compose, systemd for databases, migrations, metrics): [`docs/DEPLOY_VPS.md`](docs/DEPLOY_VPS.md).

Also: [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md), [`ops/README_LOGGING.md`](ops/README_LOGGING.md).

---

*Suggested GitHub topics for a pinned repository: `telegram`, `aiogram`, `python`, `asyncio`, `postgresql`, `redis`, `docker`, `prometheus`.*
