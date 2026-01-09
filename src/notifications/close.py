from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.notifications.types import NotificationEvent, RecipientKind
from src.texts.buttons import btn_close

NOTIFICATION_CLOSE_CB = "ntf:close"

_EXCLUDE_CLOSE: set[tuple[NotificationEvent, RecipientKind]] = {
    # Master must take an action (confirm/decline).
    (NotificationEvent.BOOKING_CREATED_PENDING, RecipientKind.MASTER),
    # Master must mark attendance (or snooze) in-place.
    (NotificationEvent.MASTER_ATTENDANCE_NUDGE, RecipientKind.MASTER),
}


def should_add_close_button(*, event: NotificationEvent, recipient: RecipientKind) -> bool:
    return (event, recipient) not in _EXCLUDE_CLOSE


def add_close_button(reply_markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup:
    """
    Append a bottom "Закрыть" button (best-effort delete) to an inline keyboard.
    If the keyboard already contains NOTIFICATION_CLOSE_CB, it is returned unchanged.
    """
    if reply_markup is not None:
        for row in reply_markup.inline_keyboard:
            for btn in row:
                if getattr(btn, "callback_data", None) == NOTIFICATION_CLOSE_CB:
                    return reply_markup
        rows = [list(row) for row in reply_markup.inline_keyboard]
    else:
        rows = []

    rows.append([InlineKeyboardButton(text=btn_close(), callback_data=NOTIFICATION_CLOSE_CB)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
