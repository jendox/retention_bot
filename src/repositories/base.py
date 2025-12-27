from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.events import EventLogger


class BaseRepository:
    def __init__(self, session: AsyncSession, name: str | None = None) -> None:
        self._session = session
        self.ev = EventLogger(name or __name__)
