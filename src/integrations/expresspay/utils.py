from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

ACCOUNT_NUMBER_LENGTH = 22


def format_amount(amount: Decimal) -> str:
    """
    API ожидает строку с запятой в качестве разделителя дробной части.
    """
    q = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{q:f}"
    if "." in s:
        s = s.replace(".", ",")
    else:
        s += ",00"
    # гарантируем 2 знака
    if "," in s:
        left, right = s.split(",", 1)
        right = (right + "00")[:2]
        s = f"{left},{right}"
    return s


def format_expiration(dt: datetime | date) -> str:
    """
    Expiration: yyyyMMdd или yyyyMMddHHmm.
    """
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt.strftime("%Y%m%d")

    if dt.tzinfo is None:
        # считаем, что уже "локальное" время вызывающего кода
        return dt.strftime("%Y%m%d%H%M")

    # приводим к UTC, чтобы поведение было стабильным
    utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y%m%d%H%M")


def default_epos_account_no(
    master_id: int,
    *,
    base_account_number: str = "01",
    formed_at: datetime | date | None = None,
) -> str:
    """
    AccountNo (22 символа):
        base_account_number (2 цифры, например "01") +
        дата формирования инвойса ("%d%m%y") +
        нули до нужной длины +
        master_id (в конце)

    Пример (master_id=123):
        01 271225 00000000000 123
        (пробелы только для примера; в реальности без пробелов)
    """
    if master_id <= 0:
        raise ValueError("master_id must be positive")

    if len(base_account_number) != 2:  # noqa: PLR2004
        raise ValueError("base_account_number must be exactly 2 characters")
    if not base_account_number.isdigit():
        raise ValueError("base_account_number must contain only digits")

    dt = formed_at or datetime.now(UTC)
    date_part = dt.strftime("%d%m%y")  # 6 символов
    order_part = str(master_id)

    # 22 = 2 (base) + 6 (date) + pad + len(order_part)
    fixed_len = len(base_account_number) + len(date_part)  # 2 + 6 = 8
    remaining = 22 - fixed_len  # 14 символов под pad+master_id

    if len(order_part) > remaining:
        raise ValueError(f"master_id is too long to fit: max {remaining} digits, got {len(order_part)}")

    pad = "0" * (remaining - len(order_part))

    account_no = f"{base_account_number}{date_part}{pad}{order_part}"

    if len(account_no) != ACCOUNT_NUMBER_LENGTH:
        raise AssertionError(f"AccountNo must be 22 chars, got {len(account_no)}: {account_no}")

    return account_no
