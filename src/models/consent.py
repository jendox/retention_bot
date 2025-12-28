from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.sa import Base


class UserConsent(Base):
    __tablename__ = "user_consents"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
