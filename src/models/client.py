from __future__ import annotations

import typing
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.sa import Base
from src.schemas.enums import Timezone

if typing.TYPE_CHECKING:
    from src.models.booking import Booking
    from src.models.master import Master

timezone_enum = ENUM(
    Timezone,
    name="timezone_enum",
    create_type=True,
)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    timezone: Mapped[Timezone] = mapped_column(timezone_enum, default=Timezone.EUROPE_MINSK, nullable=False)

    masters: Mapped[list[Master]] = relationship(
        "Master", secondary="master_clients", back_populates="clients",
    )
    bookings: Mapped[list[Booking]] = relationship(
        "Booking", back_populates="client", cascade="all, delete-orphan",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now(),
                                                 nullable=False)
