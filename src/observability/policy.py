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
        for build in (
            self._security,
            self._db,
            self._app,
            self._master_reg,
            self._bot_unhandled,
        ):
            spec = build(event=event, level=level, error_type=error_type, stage=stage)
            if spec is not None:
                return spec
        return None

    @staticmethod
    def _security(*, event: str, level: str, error_type: str, stage: str) -> AlertSpec | None:  # noqa: ARG004
        if event != "security.invite_policy_misconfigured":
            return None
        return AlertSpec(
            level="WARNING",
            throttle_key="security.invite_policy_misconfigured",
            throttle_sec=60 * 60,
            text="Invite-only master registration is enabled, but invite secret is not configured.",
        )

    @staticmethod
    def _db(*, event: str, level: str, error_type: str, stage: str) -> AlertSpec | None:  # noqa: ARG004
        if event != "db.query_failed":
            return None
        connectivity_markers = (
            "Connection",
            "Timeout",
            "OperationalError",
            "InterfaceError",
            "TooManyConnections",
            "CannotConnect",
            "ConnectionDoesNotExist",
        )
        if not any(marker in error_type for marker in connectivity_markers):
            return None
        throttle_key = f"db.query_failed:{error_type or 'UnknownError'}"
        return AlertSpec(
            level="ERROR",
            throttle_key=throttle_key,
            throttle_sec=10 * 60,
            text="Database query failed (connectivity).",
        )

    @staticmethod
    def _app(*, event: str, level: str, error_type: str, stage: str) -> AlertSpec | None:  # noqa: ARG004
        if event != "app.error":
            return None
        return AlertSpec(
            level="ERROR",
            throttle_key="app.error",
            throttle_sec=10 * 60,
            text="Bot process error (outside update handling).",
        )

    @staticmethod
    def _master_reg(*, event: str, level: str, error_type: str, stage: str) -> AlertSpec | None:  # noqa: ARG004
        if event not in {"master_reg.start_failed", "master_reg.complete_failed"}:
            return None
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

    @staticmethod
    def _bot_unhandled(*, event: str, level: str, error_type: str, stage: str) -> AlertSpec | None:  # noqa: ARG004
        if event != "bot.unhandled_exception" or level not in {"ERROR", "CRITICAL"}:
            return None
        throttle_key = f"bot.unhandled_exception:{error_type or 'UnknownError'}"
        return AlertSpec(
            level="ERROR",
            throttle_key=throttle_key,
            throttle_sec=10 * 60,
            text="Unhandled exception in bot handler.",
        )
