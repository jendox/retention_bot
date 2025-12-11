from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from src.core.sa import Base
from src.schemas.enums import InviteType

TOKEN_LENGTH = 32

invite_type_enum = ENUM(InviteType, name="invite_type_enum", create_type=False)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(TOKEN_LENGTH), unique=True, nullable=False, index=True)
    type: Mapped[InviteType] = mapped_column(invite_type_enum, nullable=False)

    max_uses: Mapped[int | None] = mapped_column(SmallInteger, default=1, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
