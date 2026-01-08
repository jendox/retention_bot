from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.paywall import upgrade_url_from_contact
from src.settings import get_settings
from src.texts import support as support_txt


def build_support_keyboard(*, contact: str) -> InlineKeyboardMarkup | None:
    url = upgrade_url_from_contact(contact)
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Написать в поддержку", url=url)]],
    )


async def send_support_contact(*, bot, chat_id: int) -> None:
    contact = get_settings().billing.contact
    kb = build_support_keyboard(contact=str(contact))
    await bot.send_message(
        chat_id=int(chat_id),
        text=support_txt.support_message(contact=str(contact)),
        reply_markup=kb,
        parse_mode="HTML",
    )
