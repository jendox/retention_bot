from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import InviteRepository, MasterRepository
from src.schemas import Invite
from src.schemas.enums import InviteType
from src.settings import get_settings


@dataclass(frozen=True)
class CreateClientInviteResult:
    token: str
    link: str
    master_id: int
    master_name: str


class CreateClientInvite:
    def __init__(
        self,
        session: AsyncSession,
        *,
        bot_username: str | None = None,
    ) -> None:
        self._master_repo = MasterRepository(session)
        self._invite_repo = InviteRepository(session)
        self._bot_username = bot_username

    async def execute_for_telegram(self, *, master_telegram_id: int) -> CreateClientInviteResult:
        master = await self._master_repo.get_by_telegram_id(master_telegram_id)
        invite = await self._invite_repo.create(
            Invite(
                type=InviteType.CLIENT,
                master_id=master.id,
            ),
        )
        bot_username = self._bot_username or get_settings().telegram.bot_username
        link = f"https://t.me/{bot_username}?start=c_{invite.token}"

        return CreateClientInviteResult(
            token=invite.token,
            link=link,
            master_id=master.id,
            master_name=master.name,
        )
