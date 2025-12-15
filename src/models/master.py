from __future__ import annotations

import typing
from datetime import date as date_type, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.sa import Base
from src.schemas.enums import Timezone

if typing.TYPE_CHECKING:
    from src.models.booking import Booking
    from src.models.client import Client

timezone_enum = ENUM(
    Timezone,
    name="timezone_enum",
    create_type=True,
)


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)

    work_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=[0, 1, 2, 3, 4], nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    slot_size_min: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    timezone: Mapped[Timezone] = mapped_column(timezone_enum, default=Timezone.EUROPE_MINSK, nullable=False)
    notify_clients: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    clients: Mapped[list[Client]] = relationship(
        "Client",
        secondary="master_clients",
        back_populates="masters",
        order_by="Client.name",
    )
    bookings: Mapped[list[Booking]] = relationship(
        "Booking", back_populates="master", cascade="all, delete-orphan",
    )
    overrides: Mapped[list[WorkdayOverride]] = relationship(
        "WorkdayOverride",
        back_populates="master",
        cascade="all, delete-orphan",
        order_by="WorkdayOverride.date",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False,
    )


class WorkdayOverride(Base):
    __tablename__ = "workday_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)

    # None = выходной
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    master: Mapped[Master] = relationship("Master", back_populates="overrides")

    __table_args__ = (
        UniqueConstraint("master_id", "date", name="uq_override_master_date"),
    )


master_clients = Table(
    "master_clients",
    Base.metadata,
    Column(
        "master_id",
        ForeignKey("masters.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "client_id",
        ForeignKey("clients.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
)
