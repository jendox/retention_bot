# Observability (Logging & Alerts)

The bot uses structured JSON logs and a best-effort admin alerting mechanism.

## Logs

Logs are written to stdout in JSON via `src/observability/logging.py`. For each Telegram update the middleware
injects common context fields automatically (e.g. `trace_id`, `telegram_id`, `chat_id`, `handler`, `fsm_state`).

## Alerts to admins

Alerts are delivered via Telegram messages to `ADMIN__TELEGRAM_IDS`. They are throttled (deduplicated) to avoid spam.

Alerts are controlled by `AppSettings.observability`:

- `OBSERVABILITY__ALERTS_ENABLED` (default `true`)
- `OBSERVABILITY__ALERTS_DEFAULT_THROTTLE_SEC` (default `600`)
- `OBSERVABILITY__ALERTS_EVENTS` (optional allowlist: `event1,event2,...`)
- `OBSERVABILITY__ALERTS_LEVEL_BY_EVENT` (optional overrides: `event=WARNING,event2=ERROR`)
- `OBSERVABILITY__ALERTS_THROTTLE_SEC_BY_EVENT` (optional overrides: `event=1800,event2=600`)

### Recommended minimal allowlist for production

Start with this list to keep noise low, but still be paged on real problems:

`OBSERVABILITY__ALERTS_EVENTS=security.invite_policy_misconfigured,bot.unhandled_exception,master_reg.start_failed,master_reg.complete_failed,app.error,db.query_failed`

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

