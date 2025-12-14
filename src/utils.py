import logging
from collections.abc import Iterable

import phonenumbers
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from phonenumbers.phonenumberutil import NumberParseException

logger = logging.getLogger(__name__)

BUCKET_KEY = "_gc_buckets"
BucketName = str


async def track_message(
    state: FSMContext,
    message: Message,
    bucket: BucketName = "default",
) -> None:
    """
    Сохранить message_id в указанный bucket.
    Подходит и для сообщений бота, и для сообщений пользователя.
    """
    data = await state.get_data()
    buckets: dict = data.get(BUCKET_KEY, {})

    bucket_data = buckets.get(bucket, {})
    message_ids: list[int] = bucket_data.get("message_ids", [])

    message_ids.append(message.message_id)

    bucket_data["message_ids"] = message_ids
    bucket_data["chat_id"] = message.chat.id

    buckets[bucket] = bucket_data

    await state.update_data(**{BUCKET_KEY: buckets})


async def track_callback_message(
    state: FSMContext,
    callback: CallbackQuery,
    bucket: BucketName = "default",
) -> None:
    """
    Удобный helper для коллбеков:
    трекает связанное сообщение, если оно есть.
    """
    if callback.message:
        await track_message(state, callback.message, bucket=bucket)


async def cleanup_messages(
    state: FSMContext,
    bot: Bot,
    bucket: BucketName = "default",
    *,
    clear_bucket: bool = True,
    ignore_errors: bool = True,
) -> None:
    """
    Удаляет все сообщения из указанного bucket.

    clear_bucket=True  -> очищает только этот bucket в FSM
    clear_bucket=False -> оставляет ids (на случай, если хочешь повторно пробовать).
    """
    data = await state.get_data()
    buckets: dict = data.get(BUCKET_KEY, {})
    bucket_data = buckets.get(bucket)

    if not bucket_data:
        return

    chat_id = bucket_data.get("chat_id")
    message_ids: Iterable[int] = bucket_data.get("message_ids", [])

    if chat_id is None:
        return

    for mid in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except (TelegramBadRequest, TelegramAPIError) as e:
            # тут "следим за исключениями": логируем, но не роняем хендлер
            logger.warning(
                "message_gc.delete_failed",
                extra={"chat_id": chat_id, "message_id": mid, "error": str(e)},
            )
            if not ignore_errors:
                raise

    if clear_bucket:
        buckets.pop(bucket, None)
        await state.update_data(**{BUCKET_KEY: buckets})


async def clear_all_buckets(state: FSMContext) -> None:
    """
    Полностью убирает всю технику для GC из FSM.
    Удобно вызывать, если ты в конце флоу ещё и state.clear() НЕ делаешь.
    """
    data = await state.get_data()
    if BUCKET_KEY in data:
        data.pop(BUCKET_KEY)
        await state.set_data(data)


async def answer_tracked(
    message: Message,
    state: FSMContext,
    text: str,
    bucket: BucketName = "default",
    **kwargs,
) -> Message:
    """
    Обертка над message.answer: отправляет + автоматически трекает.
    """
    msg = await message.answer(text, **kwargs)
    await track_message(state, msg, bucket=bucket)
    return msg


async def edit_text_tracked(
    state: FSMContext,
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    bucket: BucketName = "default",
    **kwargs,
) -> Message:
    """
    Если хочешь редактировать существующее сообщение и тоже его трекать
    (хотя часто трекать достаточно один раз при создании).
    """
    msg = await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        **kwargs,
    )
    await track_message(state, msg, bucket=bucket)
    return msg


def validate_phone(value: str, region: str = "BY") -> str | None:
    try:
        number = phonenumbers.parse(value, region)
        if not phonenumbers.is_valid_number(number):
            raise ValueError()
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (NumberParseException, ValueError):
        return None


def styled_text(text: str, color: str = None, bold: bool = False, italic: bool = False) -> str:
    """Форматирование текста с разными стилями"""
    styles = []

    if color:
        styles.append(f"color: {color}")

    if bold:
        styles.append("font-weight: bold")

    if italic:
        styles.append("font-style: italic")

    if styles:
        style_attr = " ".join(styles)
        return f'<span style="{style_attr}">{text}</span>'

    return text
