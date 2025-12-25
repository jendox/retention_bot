from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class PaywallButtons:
    upgrade: InlineKeyboardButton
    back: InlineKeyboardButton | None = None


def _upgrade_url_from_contact(contact: str) -> str | None:
    raw = (contact or "").strip()
    url: str | None = None
    if raw:
        if raw.startswith(("http://", "https://")):
            url = raw
        elif raw.startswith("@") and len(raw) > 1:
            url = f"https://t.me/{raw[1:]}"
        elif raw.startswith(("t.me/", "telegram.me/")):
            url = f"https://{raw}"
        elif "t.me/" in raw:
            url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
    return url


def build_upgrade_button(*, contact: str, text: str) -> InlineKeyboardButton:
    upgrade_url = _upgrade_url_from_contact(contact)
    return InlineKeyboardButton(text=text, url=upgrade_url) if upgrade_url else InlineKeyboardButton(
        text=text,
        callback_data="paywall:contact",
    )


def build_paywall_keyboard(
    *,
    contact: str,
    upgrade_text: str,
    back_text: str,
    back_callback_data: str | None,
) -> InlineKeyboardMarkup:
    upgrade_url = _upgrade_url_from_contact(contact)
    upgrade_btn = (
        InlineKeyboardButton(text=upgrade_text, url=upgrade_url)
        if upgrade_url
        else InlineKeyboardButton(text=upgrade_text, callback_data="paywall:contact")
    )

    rows: list[list[InlineKeyboardButton]] = [[upgrade_btn]]
    if back_callback_data:
        rows.append([InlineKeyboardButton(text=back_text, callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_upgrade_only_keyboard(*, contact: str, upgrade_text: str) -> InlineKeyboardMarkup:
    upgrade_url = _upgrade_url_from_contact(contact)
    upgrade_btn = (
        InlineKeyboardButton(text=upgrade_text, url=upgrade_url)
        if upgrade_url
        else InlineKeyboardButton(text=upgrade_text, callback_data="paywall:contact")
    )
    return InlineKeyboardMarkup(inline_keyboard=[[upgrade_btn]])
