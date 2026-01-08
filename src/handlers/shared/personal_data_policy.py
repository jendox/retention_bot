from __future__ import annotations

from pathlib import Path

from aiogram.types import FSInputFile

from src.observability.events import EventLogger
from src.settings import get_settings
from src.texts import common as common_txt, personal_data as pd_txt

ev = EventLogger(__name__)


async def send_personal_data_policy(*, bot, chat_id: int) -> bool:
    """
    Sends the Personal Data Processing Policy PDF to the user as a Telegram document.
    The file is expected to be present inside the container (mounted in production).
    """
    path = Path(get_settings().security.personal_data_policy_path)
    try:
        document = FSInputFile(str(path), filename=path.name)
    except Exception as exc:
        ev.warning("pd.policy_file_open_failed", chat_id=int(chat_id), path=str(path), exc=str(exc))
        await bot.send_message(chat_id=int(chat_id), text=pd_txt.policy_in_progress(), parse_mode="HTML")
        return False

    try:
        await bot.send_document(chat_id=int(chat_id), document=document)
        ev.info("pd.policy_sent", chat_id=int(chat_id), path=str(path))
        return True
    except Exception as exc:
        ev.warning("pd.policy_send_failed", chat_id=int(chat_id), path=str(path), exc=str(exc))
        await bot.send_message(chat_id=int(chat_id), text=common_txt.generic_error(), parse_mode="HTML")
        return False
