from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.sa import Base
from src.models.client import Client
from src.models.master import Master
from src.schemas.enums import BookingStatus

booking_status_enum = ENUM(BookingStatus, name="booking_status_enum", create_type=False)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(booking_status_enum, default=BookingStatus.PENDING, nullable=False)

    master: Mapped[Master] = relationship("Master", back_populates="bookings")
    client: Mapped[Client] = relationship("Client", back_populates="bookings")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_bookings_master_start_at", "master_id", "start_at"),
        Index("ix_bookings_master_status_start_at", "master_id", "status", "start_at"),
    )
