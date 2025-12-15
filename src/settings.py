from __future__ import annotations

import os
from contextvars import ContextVar
from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
                part = part.strip()
                if not part:
                    continue
                ids.add(int(part))
            return ids
        return value


class BillingSettings(BaseModel):
    contact: str = "@admin"


class AppSettings(BaseSettings):
    debug: bool = False

    telegram: TelegramSettings
    database: DatabaseSettings
    admin: AdminSettings = Field(default_factory=AdminSettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def load(cls, *, env_file: str | None = None) -> "AppSettings":
        env_file = env_file or os.getenv("ENV_FILE") or ".env.local"
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
