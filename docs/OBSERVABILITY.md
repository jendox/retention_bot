# Observability (Logging & Alerts)

The bot uses structured JSON logs and a best-effort admin alerting mechanism.

## Logs

Logs are written to stdout in JSON via `src/observability/logging.py`. For each Telegram update the middleware
injects common context fields automatically (e.g. `trace_id`, `telegram_id`, `chat_id`, `handler`, `fsm_state`).

## Business events (funnels)

For product/ops analytics we log stable domain-level events (rather than UI-specific handler events). Key conventions:
- Use a single stable `event` name for the domain action, even if UX changes.
- Include `actor` where applicable: `"master"` / `"client"`.
- For rejected attempts, include a short `error` reason code (limits/validation/conflicts/forbidden/etc).
- Avoid PII in fields (no phone numbers, tokens, invite secrets).

### Audit log (DB)

In addition to JSON logs, selected business events are written to Postgres in `audit_logs` (append-only).
This is meant for reliable funnels/cohorts without requiring a log pipeline.

### Minimal recommended analytics events

If you don’t have full analytics yet, start by collecting these events (in JSON logs).

- `master_registration.completed` (master registered / profile completed)
- `master_settings.work_days_updated` / `master_settings.work_time_updated` (working hours set/updated)
- `master_settings.slot_size_updated` (slot duration set/updated)
- `client_added` (`offline=true/false`)
- `invite_client.created` (invite link created)
- `invite_link_shared` (invite link shown to master in a chosen format)
- `invite.accepted` (client connected via invite; see fields `invite_consumed`, `invite_burned_noop`)
- `booking.created` (with `actor`), and legacy `booking.created_by_master` / `booking.created_by_client`
- `booking.reviewed` / `booking.cancelled` / `booking.rescheduled` (booking status changed)
- `booking.attendance_marked` (ATTENDED/NO_SHOW)
- `pro_features_toggled` (`master.notify_clients`, `client.notifications_enabled`, etc.)
- `trial_started`
- `billing.pro_invoice_created` / `billing.pro_renewal_invoice_created`
- `billing.pro_payment_checked` + `payment_success`
- `subscription_renewed`

### Registration / PD consent

- `master_reg.*` and `client_reg.*`
- `master_reg.pd.*` and `client_reg.pd.*`
- `pd.delete_*` (delete prompt/confirm/delete outcomes)

### Invites

- `invite.accept_start` (canonical accept attempt)

### Bookings (canonical)

- Create:
  - `booking.create_attempt`
  - `booking.create_rejected`
  - `booking.created`
- Review/confirm/decline (master):
  - `booking.review_attempt`
  - `booking.review_rejected`
  - `booking.reviewed`
- Reschedule (master):
  - `booking.reschedule_attempt`
  - `booking.reschedule_rejected`
  - `booking.rescheduled`
- Cancel:
  - `booking.cancel_attempt`
  - `booking.cancel_rejected`
  - `booking.cancelled`

Note: legacy/UX-specific events may still exist (e.g. `booking.created_by_master`) for backwards compatibility.

### Billing (canonical-ish)

- `billing.pro_invoice_created`
- `billing.pro_renewal_invoice_created`
- `billing.pro_payment_checked`

## Alerts to admins

Alerts are delivered via Telegram messages to `ADMIN__TELEGRAM_IDS`. They are throttled (deduplicated) to avoid spam.

Alerts are controlled by `AppSettings.observability`:

- `OBSERVABILITY__ALERTS_ENABLED` (default `true`)
- `OBSERVABILITY__ALERTS_DEFAULT_THROTTLE_SEC` (default `600`)
- `OBSERVABILITY__ALERTS_EVENTS` (optional allowlist: `event1,event2,...`)
- `OBSERVABILITY__ALERTS_LEVEL_BY_EVENT` (optional overrides: `event=WARNING,event2=ERROR`)
- `OBSERVABILITY__ALERTS_TEXT_BY_EVENT` (optional overrides: `event=Some text;event2=Another text`)
- `OBSERVABILITY__ALERTS_THROTTLE_SEC_BY_EVENT` (optional overrides: `event=1800,event2=600`)

## Workers: heartbeat + silence alerts

Workers (`src/workers/reminders.py`, `src/workers/payments.py`) periodically write a Redis key:

- `beautydesk:heartbeat:<worker>` with a unix timestamp value and TTL.

The bot process can run a watchdog that checks these keys and emits admin alerts on “silence”:

- `workers.reminders.heartbeat_missing`
- `workers.payments.heartbeat_missing`

Settings:
- `OBSERVABILITY__WORKERS_WATCHDOG_ENABLED` (default `false`)
- `OBSERVABILITY__WORKERS_HEARTBEAT_CHECK_SEC` (default `60`)
- `OBSERVABILITY__WORKERS_HEARTBEAT_STALE_SEC` (default `300`)
- `OBSERVABILITY__WORKERS_HEARTBEAT_TTL_SEC` (default `600`)
- `OBSERVABILITY__WORKERS_HEARTBEAT_LOG_EVERY_SEC` (default `300`)

Optional diagnostic alert (only matters if you allowlist it):
- `workers.watchdog.redis_error`

## Metrics (Prometheus)

The service can expose a minimal Prometheus-compatible `/metrics` endpoint (no extra dependencies).

Settings (nested env vars):
- `METRICS__ENABLED=true|false` (default `false`)
- `METRICS__EXPORTER=prometheus|both` (default `prometheus`)
- `METRICS__HOST=0.0.0.0` (default)
- `METRICS__PORT=8000` (default)
- `METRICS__PATH=/metrics` (default)

Minimal exported metrics include:
- `bot_handler_duration_seconds` (histogram)
- `bot_handler_ok_total`, `bot_handler_exceptions_total`, `bot_unhandled_exceptions_total` (counters)
- `db_query_duration_seconds`, `db_queries_total`, `db_query_errors_total` (counters/histogram)
- `external_http_duration_seconds`, `external_http_requests_total`, `external_http_errors_total` (counters/histogram)

### Recommended minimal allowlist for production

Start with this list to keep noise low, but still be paged on real problems:

`OBSERVABILITY__ALERTS_EVENTS=security.invite_policy_misconfigured,bot.unhandled_exception,master_reg.start_failed,master_reg.complete_failed,app.error,db.query_failed,workers.reminders.heartbeat_missing,workers.payments.heartbeat_missing`

Notes:
- `bot.unhandled_exception` is the strongest signal that something is broken in a handler.
- `master_reg.*_failed` covers the most valuable funnel in MVP.
- `db.query_failed` helps detect DB connectivity/migrations issues early.
- `app.error` catches startup/shutdown exceptions outside update handling.

## Sampling (reduce noise in logs)

Use `OBSERVABILITY__LOG_SAMPLE_RATE_BY_EVENT` to reduce volume for high-frequency events:

Example:
`OBSERVABILITY__LOG_SAMPLE_RATE_BY_EVENT=handler.ok=0.01,master_reg.input_invalid=0.1,master_reg.rate_limited=0.01`

Sampling is deterministic per `trace_id` when available (so a single update is either fully sampled-in or sampled-out).
