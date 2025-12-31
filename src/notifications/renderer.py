from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape as html_escape

from aiogram.types import InlineKeyboardMarkup

from src.notifications.context import (
    BillingContext,
    BookingContext,
    LimitsContext,
    OnboardingContext,
    ReminderContext,
    SubscriptionContext,
)
from src.notifications.templates import (
    render_billing_template,
    render_booking_template,
    render_limits_template,
    render_onboarding_template,
    render_reminder_template,
    render_subscription_template,
)
from src.notifications.types import NotificationEvent, RecipientKind


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    parse_mode: str = "HTML"
    reply_markup: InlineKeyboardMarkup | None = None


def _e(value: str | None) -> str:
    return html_escape(value or "", quote=False)


def _escape_context(
    context: (
        BookingContext
        | LimitsContext
        | ReminderContext
        | OnboardingContext
        | SubscriptionContext
        | BillingContext
    ),
) -> (
    BookingContext
    | LimitsContext
    | ReminderContext
    | OnboardingContext
    | SubscriptionContext
    | BillingContext
):
    if isinstance(context, BookingContext):
        return replace(
            context,
            master_name=_e(context.master_name),
            client_name=_e(context.client_name),
            slot_str=_e(context.slot_str),
        )
    if isinstance(context, ReminderContext):
        return replace(
            context,
            master_name=_e(context.master_name),
            slot_str=_e(context.slot_str),
        )
    if isinstance(context, OnboardingContext):
        return replace(
            context,
            master_name=_e(context.master_name),
        )
    if isinstance(context, SubscriptionContext):
        return replace(
            context,
            master_name=_e(context.master_name),
            plan=_e(context.plan),
            ends_on=_e(context.ends_on),
        )
    if isinstance(context, BillingContext):
        return replace(
            context,
            master_name=_e(context.master_name),
        )
    return context


def render(
    *,
    event: NotificationEvent,
    recipient: RecipientKind,
    context: (
        BookingContext
        | LimitsContext
        | ReminderContext
        | OnboardingContext
        | SubscriptionContext
        | BillingContext
    ),
    reply_markup: InlineKeyboardMarkup | None = None,
) -> RenderedMessage:
    safe_context = _escape_context(context)
    if isinstance(safe_context, BookingContext):
        text = render_booking_template(event=event, recipient=recipient, context=safe_context)
    elif isinstance(safe_context, LimitsContext):
        text = render_limits_template(event=event, recipient=recipient, context=safe_context)
    elif isinstance(safe_context, ReminderContext):
        text = render_reminder_template(event=event, recipient=recipient, context=safe_context)
    elif isinstance(safe_context, OnboardingContext):
        text = render_onboarding_template(event=event, recipient=recipient, context=safe_context)
    elif isinstance(safe_context, SubscriptionContext):
        text = render_subscription_template(event=event, recipient=recipient, context=safe_context)
    elif isinstance(safe_context, BillingContext):
        text = render_billing_template(event=event, recipient=recipient, context=safe_context)
    else:
        text = ""
    return RenderedMessage(text=text, parse_mode="HTML", reply_markup=reply_markup)
