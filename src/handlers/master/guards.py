from __future__ import annotations

from aiogram.types import CallbackQuery, Message

from src.observability.events import EventLogger
from src.rate_limiter import RateLimiter
from src.texts import common as common_txt

ev = EventLogger(__name__)


async def rate_limit_callback(
    callback: CallbackQuery,
    rate_limiter: RateLimiter | None,
    *,
    name: str,
    ttl_sec: int,
    **labels: object,
) -> bool:
    if rate_limiter is None:
        return True
    clean_labels = {k: v for k, v in labels.items() if v is not None}
    allowed = await rate_limiter.hit(
        name=name,
        ttl_sec=ttl_sec,
        telegram_id=callback.from_user.id,
        **clean_labels,
    )
    if allowed:
        return True
    ev.info("rate_limit.blocked", scope=name)
    await callback.answer(common_txt.too_many_requests(), show_alert=True)
    return False


async def rate_limit_message(
    message: Message,
    rate_limiter: RateLimiter | None,
    *,
    name: str,
    ttl_sec: int,
    **labels: object,
) -> bool:
    if rate_limiter is None or message.from_user is None:
        return True
    clean_labels = {k: v for k, v in labels.items() if v is not None}
    allowed = await rate_limiter.hit(
        name=name,
        ttl_sec=ttl_sec,
        telegram_id=message.from_user.id,
        **clean_labels,
    )
    if allowed:
        return True
    ev.info("rate_limit.blocked", scope=name)
    await message.answer(common_txt.too_many_requests())
    return False
