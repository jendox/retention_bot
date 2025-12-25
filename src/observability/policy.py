from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AlertSpec:
    """
    Configuration for an admin alert notification.
    """

    level: str
    throttle_key: str
    throttle_sec: int
    text: str


class AlertPolicy:
    """
    Decides which log events should trigger an admin alert.

    Conventions:
    - `event` is a stable string identifier (e.g. "master_reg.complete_failed").
    - `level` is a logging level name: DEBUG/INFO/WARNING/ERROR/CRITICAL.
    - `fields` are structured key/value details to include into the alert.
    """

    def decide(self, *, event: str, level: str, fields: dict[str, Any]) -> AlertSpec | None:
        error_type = str(fields.get("error_type") or "")
        stage = str(fields.get("stage") or "")

        if event == "security.invite_policy_misconfigured":
            return AlertSpec(
                level="WARNING",
                throttle_key="security.invite_policy_misconfigured",
                throttle_sec=60 * 60,
                text="Invite-only master registration is enabled, but invite secret is not configured.",
            )

        if event == "db.query_failed":
            # Avoid paging on expected constraint violations; prefer alerting on connectivity-like failures.
            connectivity_markers = (
                "Connection",
                "Timeout",
                "OperationalError",
                "InterfaceError",
                "TooManyConnections",
                "CannotConnect",
                "ConnectionDoesNotExist",
            )
            if any(marker in error_type for marker in connectivity_markers):
                throttle_key = f"db.query_failed:{error_type or 'UnknownError'}"
                return AlertSpec(
                    level="ERROR",
                    throttle_key=throttle_key,
                    throttle_sec=10 * 60,
                    text="Database query failed (connectivity).",
                )
            return None

        if event == "app.error":
            return AlertSpec(
                level="ERROR",
                throttle_key="app.error",
                throttle_sec=10 * 60,
                text="Bot process error (outside update handling).",
            )

        if event in {"master_reg.start_failed", "master_reg.complete_failed"}:
            throttle_parts = [event]
            if stage:
                throttle_parts.append(stage)
            if error_type:
                throttle_parts.append(error_type)
            throttle_key = ":".join(throttle_parts)
            return AlertSpec(
                level="ERROR",
                throttle_key=throttle_key,
                throttle_sec=10 * 60,
                text="Master registration failed.",
            )

        if event == "bot.unhandled_exception" and level in {"ERROR", "CRITICAL"}:
            throttle_key = f"bot.unhandled_exception:{error_type or 'UnknownError'}"
            return AlertSpec(
                level="ERROR",
                throttle_key=throttle_key,
                throttle_sec=10 * 60,
                text="Unhandled exception in bot handler.",
            )

        return None
