import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

__all__ = (
    "Base",
    "Database",
    "session_local",
)

URL_PARTS = 2

logger = logging.getLogger("db.sa")


class Base(DeclarativeBase):
    pass


class Database:
    engine: AsyncEngine | None = None
    session_maker: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def _init(cls, *, url: str, echo: bool) -> None:
        cls.engine = create_async_engine(
            url=url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            future=True,
        )
        cls.session_maker = async_sessionmaker(
            cls.engine, expire_on_commit=False, class_=AsyncSession,
        )
        logger.info("db.engine.initialized", extra={"url": _redact_url(url)})

    @classmethod
    async def _close(cls) -> None:
        if cls.engine is not None:
            await cls.engine.dispose()
            logger.info("db.engine.closed")
        cls.engine = None
        cls.session_maker = None

    @classmethod
    @asynccontextmanager
    async def lifespan(cls, url: str, echo: bool = False):
        await cls._init(url=url, echo=echo)
        try:
            yield
        finally:
            await cls._close()

    @classmethod
    def require_session_maker(cls) -> async_sessionmaker[AsyncSession]:
        if cls.session_maker is None:
            raise RuntimeError("DB session_maker is not initialized. Call Database.init() first.")
        return cls.session_maker


@asynccontextmanager
async def session_local() -> AsyncIterator[AsyncSession]:
    session_maker = Database.require_session_maker()
    async with session_maker() as session:
        yield session


@asynccontextmanager
async def active_session(*, begin: bool = True) -> AsyncIterator[AsyncSession]:
    async with session_local() as session:
        if not begin:
            yield session
            return

        async with session.begin():
            yield session


def _redact_url(url: str) -> str:
    try:
        prefix, rest = url.split("://", 1)
        creds_host = rest.split("@", 1)
        if len(creds_host) == URL_PARTS:
            creds, host = creds_host
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{prefix}://{user}:****@{host}"
    except Exception:
        pass
    return url
