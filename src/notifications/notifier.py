from dataclasses import dataclass, replace
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from src.notifications import BookingContext, NotificationEvent, NotificationService, RecipientKind
from src.notifications.context import LimitsContext, OnboardingContext, ReminderContext, SubscriptionContext
from src.notifications.policy import NotificationFacts, NotificationPolicy
from src.notifications.renderer import RenderedMessage, render


@dataclass(frozen=True)
class NotificationRequest:
    event: NotificationEvent
    recipient: RecipientKind
    chat_id: int | None
    context: BookingContext | LimitsContext | ReminderContext | OnboardingContext | SubscriptionContext
    reply_markup: InlineKeyboardMarkup | None = None
    facts: NotificationFacts | None = None
    meta: dict[str, Any] | None = None


def build_facts(request: NotificationRequest) -> NotificationFacts:
    """
    Build NotificationFacts for a request, automatically enriching with context fields
    when possible (keeps handlers minimal/noisy).
    """
    facts = request.facts or NotificationFacts(
        event=request.event,
        recipient=request.recipient,
        chat_id=request.chat_id,
    )

    if isinstance(request.context, LimitsContext):
        if facts.usage is None:
            facts = replace(facts, usage=request.context.usage)
        if facts.clients_limit is None and request.context.clients_limit is not None:
            facts = replace(facts, clients_limit=request.context.clients_limit)
        if facts.bookings_limit is None and request.context.bookings_limit is not None:
            facts = replace(facts, bookings_limit=request.context.bookings_limit)

    return facts


async def maybe_notify(
    *,
    bot: Bot,
    policy: NotificationPolicy,
    request: NotificationRequest,
) -> bool:
    facts = build_facts(request)
    decision = policy.check(facts)
    if not decision.allowed:
        return False
    await NotificationService(bot).send(
        event=request.event,
        recipient=request.recipient,
        chat_id=request.chat_id,
        context=request.context,
        reply_markup=request.reply_markup,
    )
    return True


@dataclass
class Notifier:
    bot: Bot
    policy: NotificationPolicy

    async def maybe_send(self, request: NotificationRequest) -> bool:
        return await maybe_notify(
            bot=self.bot,
            policy=self.policy,
            request=request,
        )

    def maybe_render(self, request: NotificationRequest) -> RenderedMessage | None:
        """
        Check policy and render a notification message without sending it.
        Useful for embedding "optional" notification copy into other UI flows.
        """
        facts = build_facts(request)
        decision = self.policy.check(facts)
        if not decision.allowed:
            return None
        return render(
            event=request.event,
            recipient=request.recipient,
            context=request.context,
            reply_markup=request.reply_markup,
        )
