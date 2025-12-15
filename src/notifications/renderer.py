from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup

from src.notifications.context import BookingContext
from src.notifications.templates import render_booking_template
from src.notifications.types import NotificationEvent, RecipientKind


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    parse_mode: str = "HTML"
    reply_markup: InlineKeyboardMarkup | None = None


def render(
    *,
    event: NotificationEvent,
    recipient: RecipientKind,
    context: BookingContext,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> RenderedMessage:
    text = render_booking_template(event=event, recipient=recipient, context=context)
    return RenderedMessage(text=text, parse_mode="HTML", reply_markup=reply_markup)

