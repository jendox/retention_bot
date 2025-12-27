from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class PaywallButtons:
    upgrade: InlineKeyboardButton
    back: InlineKeyboardButton | None = None


def upgrade_url_from_contact(contact: str) -> str | None:
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


def _upgrade_url_from_contact(contact: str) -> str | None:
    # Backward-compatible wrapper for internal uses/tests.
    return upgrade_url_from_contact(contact)


def build_upgrade_button(*, contact: str, text: str) -> InlineKeyboardButton:
    return build_upgrade_button_with_fallback(contact=contact, text=text)


def build_upgrade_button_with_fallback(
    *,
    contact: str,
    text: str,
    callback_data: str = "paywall:contact",
    force_callback: bool = False,
) -> InlineKeyboardButton:
    if force_callback:
        return InlineKeyboardButton(text=text, callback_data=callback_data)
    upgrade_url = upgrade_url_from_contact(contact)
    return InlineKeyboardButton(text=text, url=upgrade_url) if upgrade_url else InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
    )


def build_paywall_keyboard(
    *,
    contact: str,
    upgrade_text: str,
    back_text: str,
    back_callback_data: str | None,
    upgrade_callback_data: str = "paywall:contact",
    force_upgrade_callback: bool = False,
) -> InlineKeyboardMarkup:
    upgrade_btn = build_upgrade_button_with_fallback(
        contact=contact,
        text=upgrade_text,
        callback_data=upgrade_callback_data,
        force_callback=force_upgrade_callback,
    )

    rows: list[list[InlineKeyboardButton]] = [[upgrade_btn]]
    if back_callback_data:
        rows.append([InlineKeyboardButton(text=back_text, callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_upgrade_only_keyboard(
    *,
    contact: str,
    upgrade_text: str,
    upgrade_callback_data: str = "paywall:contact",
    force_upgrade_callback: bool = False,
) -> InlineKeyboardMarkup:
    upgrade_btn = build_upgrade_button_with_fallback(
        contact=contact,
        text=upgrade_text,
        callback_data=upgrade_callback_data,
        force_callback=force_upgrade_callback,
    )
    return InlineKeyboardMarkup(inline_keyboard=[[upgrade_btn]])
