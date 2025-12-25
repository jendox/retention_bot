import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.observability.events import EventLogger
from src.settings import get_settings

__all__ = (
    "Base",
    "Database",
    "session_local",
    "active_session",
)

URL_PARTS = 2

logger = logging.getLogger("db.sa")
ev = EventLogger("db.sa")


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
        _setup_query_observability(cls.engine)
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


def _setup_query_observability(engine: AsyncEngine) -> None:
    """
    Attach minimal SQLAlchemy instrumentation:
    - `db.query_failed` on DBAPI errors
    - `db.query_slow` for slow statements (threshold is configurable)

    This is intentionally lightweight for MVP and avoids logging bind params.
    """

    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._query_started_at = time.perf_counter()  # type: ignore[attr-defined]

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        started = getattr(context, "_query_started_at", None)
        if started is None:
            return
        duration_ms = int((time.perf_counter() - started) * 1000)
        slow_ms = get_settings().observability.db_slow_query_ms
        if duration_ms >= int(slow_ms):
            ev.warning(
                "db.query_slow",
                duration_ms=duration_ms,
                statement=_short_stmt(statement),
            )

    @event.listens_for(sync_engine, "handle_error")
    def _handle_error(exception_context):  # noqa: ANN001
        # Called for DBAPI-level exceptions.
        err = exception_context.original_exception
        ev.error(
            "db.query_failed",
            error_type=type(err).__name__ if err is not None else None,
            statement=_short_stmt(exception_context.statement),
        )


def _short_stmt(statement: str | None, *, limit: int = 400) -> str | None:
    if statement is None:
        return None
    text = " ".join(str(statement).split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text
