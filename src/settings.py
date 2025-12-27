from __future__ import annotations

import os
from contextvars import ContextVar
from functools import lru_cache
from typing import Self

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.integrations.expresspay import ExpressPaySettings


class TelegramSettings(BaseModel):
    bot_token: SecretStr
    bot_username: str = "beautydesk_bot"


class DatabaseSettings(BaseModel):
    postgres_url: str
    redis_url: str


class AdminSettings(BaseModel):
    telegram_ids: set[int] = Field(default_factory=set)

    @field_validator("telegram_ids", mode="before")
    @classmethod
    def _parse_telegram_ids(cls, value):
        if value is None:
            return set()
        if isinstance(value, set):
            return value
        if isinstance(value, (list, tuple)):
            return {int(v) for v in value if str(v).strip()}
        if isinstance(value, str):
            ids: set[int] = set()
            for part in value.split(","):
                stripped = part.strip()
                if not stripped:
                    continue
                ids.add(int(stripped))
            return ids
        return value


class BillingSettings(BaseModel):
    contact: str = "@admin"


class SecuritySettings(BaseModel):
    """
    Anti-abuse controls.

    If master_invite_secret is set, you can switch master registration to invite-only.
    """

    master_invite_secret: SecretStr | None = None
    master_invite_ttl_sec: int = 60 * 60 * 24  # 24h
    master_public_registration: bool = False
    master_registration_start_rl_sec: int = 5
    master_registration_confirm_rl_sec: int = 15


class ObservabilitySettings(BaseModel):
    """
    Runtime knobs for logging and admin alerting.

    These settings are intentionally minimal for MVP, but allow tuning without code changes.
    """

    alerts_enabled: bool = True
    alerts_default_throttle_sec: int = 10 * 60
    alerts_throttle_sec_by_event: dict[str, int] = Field(default_factory=dict)
    alerts_events: set[str] | None = None
    alerts_level_by_event: dict[str, str] = Field(default_factory=dict)
    alerts_text_by_event: dict[str, str] = Field(default_factory=dict)

    # Per-event log sampling rates in range [0..1]. If an event is present here and its rate < 1,
    # EventLogger will emit it only for a subset of updates (deterministically by trace_id when possible).
    log_sample_rate_by_event: dict[str, float] = Field(default_factory=dict)
    db_slow_query_ms: int = 500
    handler_slow_ms: int = 3_000

    @staticmethod
    def _parse_kv_string(
        raw: str,
        *,
        item_sep: str,
        value_cast,
        key_cast=str,
        value_transform=lambda v: v,
    ):
        items = {}
        for chunk in raw.split(item_sep):
            part = chunk.strip()
            if not part or "=" not in part:
                continue
            key_raw, value_raw = part.split("=", 1)
            key = key_raw.strip()
            val = value_raw.strip()
            if not key or not val:
                continue
            items[key_cast(key)] = value_cast(value_transform(val))
        return items

    @staticmethod
    def _parse_kv_map(
        value,
        *,
        item_sep: str,
        value_cast,
        key_cast=str,
        value_transform=lambda v: v,
    ):
        if value is None:
            return {}
        if isinstance(value, dict):
            return {key_cast(k): value_cast(value_transform(v)) for k, v in value.items()}
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return {}
        return ObservabilitySettings._parse_kv_string(
            raw,
            item_sep=item_sep,
            value_cast=value_cast,
            key_cast=key_cast,
            value_transform=value_transform,
        )

    @field_validator("alerts_events", mode="before")
    @classmethod
    def _parse_alerts_events(cls, value):
        if value is None:
            return None
        if isinstance(value, set):
            return {str(v).strip() for v in value if str(v).strip()}
        if isinstance(value, (list, tuple)):
            return {str(v).strip() for v in value if str(v).strip()}
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return set()
            return {part.strip() for part in raw.split(",") if part.strip()}
        return value

    @field_validator("alerts_level_by_event", mode="before")
    @classmethod
    def _parse_alert_levels(cls, value):
        return cls._parse_kv_map(
            value,
            item_sep=",",
            value_cast=str,
            value_transform=lambda v: str(v).upper(),
        )

    @field_validator("alerts_text_by_event", mode="before")
    @classmethod
    def _parse_alert_texts(cls, value):
        # Semicolon separator allows using commas inside texts.
        return cls._parse_kv_map(value, item_sep=";", value_cast=str)

    @field_validator("alerts_throttle_sec_by_event", mode="before")
    @classmethod
    def _parse_alert_throttles(cls, value):
        return cls._parse_kv_map(value, item_sep=",", value_cast=int)

    @field_validator("log_sample_rate_by_event", mode="before")
    @classmethod
    def _parse_sample_rates(cls, value):
        return cls._parse_kv_map(value, item_sep=",", value_cast=float)


class AppSettings(BaseSettings):
    debug: bool = False

    telegram: TelegramSettings
    database: DatabaseSettings
    admin: AdminSettings = Field(default_factory=AdminSettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    express_pay: ExpressPaySettings = Field(default=ExpressPaySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_ignore_empty=True,
        enable_decoding=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """
        Filter dotenv keys like `OBSERVABILITY=` (empty string) that pydantic-settings otherwise tries to JSON-decode
        for nested models, which would crash during parsing.
        """

        top_level = {"telegram", "database", "admin", "billing", "security", "observability"}

        def filtered_dotenv():
            env_vars = getattr(dotenv_settings, "env_vars", None)
            if isinstance(env_vars, dict):
                for key in list(env_vars.keys()):
                    if key.lower() in top_level and not str(env_vars.get(key, "")).strip():
                        env_vars.pop(key, None)
            return dotenv_settings()

        return init_settings, env_settings, filtered_dotenv, file_secret_settings

    @classmethod
    def load(cls, *, env_file: str | None = None) -> Self:
        env_file = env_file or os.getenv("ENV_FILE") or ".env.local"
        # pydantic-settings treats top-level nested models as "complex" values and will attempt to JSON-decode
        # env vars like OBSERVABILITY/SECURITY/TELEGRAM if they are present. An empty value would crash JSON
        # decoding, so we proactively ignore empty top-level blobs.
        for key in ("TELEGRAM", "DATABASE", "ADMIN", "BILLING", "SECURITY", "OBSERVABILITY"):
            if key in os.environ and not os.environ[key].strip():
                os.environ.pop(key, None)
        file_values = _read_env_file(env_file)
        raw_admin_ids = (
            os.getenv("ADMIN__TELEGRAM_IDS")
            or os.getenv("ADMIN_TELEGRAM_IDS")
            or file_values.get("ADMIN__TELEGRAM_IDS")
            or file_values.get("ADMIN_TELEGRAM_IDS")
        )
        if raw_admin_ids:
            return cls(
                admin={"telegram_ids": raw_admin_ids},
                _env_file=env_file,
                _env_file_encoding="utf-8",
            )
        return cls(_env_file=env_file, _env_file_encoding="utf-8")


app_settings: ContextVar[AppSettings] = ContextVar("app_settings")


@lru_cache(maxsize=1)
def _cached_settings() -> AppSettings:
    return AppSettings.load()


def get_settings() -> AppSettings:
    """
    Returns settings for current context if set, otherwise falls back to a cached instance.
    """
    try:
        return app_settings.get()
    except LookupError:
        return _cached_settings()


def _read_env_file(path: str) -> dict[str, str]:
    """
    Minimal .env parser to support legacy keys that are not nested (e.g. ADMIN_TELEGRAM_IDS).
    pydantic-settings will still load the file; this is only used for fallback lookups.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values
