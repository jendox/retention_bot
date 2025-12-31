from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit_log import AuditLog
from src.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    def add(  # noqa: PLR0913
        self,
        *,
        event: str,
        actor: str | None = None,
        actor_id: int | None = None,
        master_id: int | None = None,
        client_id: int | None = None,
        booking_id: int | None = None,
        invite_id: int | None = None,
        invoice_id: int | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "event": event,
            "actor": actor,
            "actor_id": actor_id,
            "master_id": master_id,
            "client_id": client_id,
            "booking_id": booking_id,
            "invite_id": invite_id,
            "invoice_id": invoice_id,
            "trace_id": trace_id,
            "meta": metadata,
        }
        if occurred_at is not None:
            kwargs["occurred_at"] = occurred_at
        maybe_awaitable = self._session.add(AuditLog(**kwargs))
        if inspect.isawaitable(maybe_awaitable):
            maybe_awaitable.close()
