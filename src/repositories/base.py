import logging

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, session: AsyncSession, name: str | None = None) -> None:
        self._session = session
        self.logger = logging.getLogger(name or __name__)
