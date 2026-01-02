from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_MONTHS_RU = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]
_WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass(frozen=True)
class MonthRef:
    year: int
    month: int


@dataclass(frozen=True)
class CalendarLimits:
    today: date
    min_date: date
    max_date: date
    pro_max_date: date
    plan_is_pro: bool


@dataclass(frozen=True)
class CalendarControls:
    cancel_text: str
    cancel_callback_data: str
    show_pro_button: bool


def _month_start(d: date) -> date:
    return date(int(d.year), int(d.month), 1)


def _add_months(d: date, months: int) -> date:
    month_index = int(d.month) - 1 + int(months)
    year = int(d.year) + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def format_month_title(*, year: int, month: int) -> str:
    return f"{_MONTHS_RU[int(month) - 1]} {int(year)}"


def cb_noop(prefix: str) -> str:
    return f"{prefix}:noop"


def cb_month(prefix: str, *, year: int, month: int) -> str:
    return f"{prefix}:m:{int(year):04d}{int(month):02d}"


def cb_today(prefix: str) -> str:
    return f"{prefix}:today"


def cb_day(prefix: str, *, day: date) -> str:
    return f"{prefix}:d:{day:%Y%m%d}"


def cb_locked(prefix: str, *, day: date) -> str:
    return f"{prefix}:l:{day:%Y%m%d}"


def cb_pro(prefix: str) -> str:
    return f"{prefix}:pro"


def parse(prefix: str, data: str | None) -> tuple[str, str | None]:
    raw = (data or "").strip()
    if not raw.startswith(prefix + ":"):
        return "invalid", None
    rest = raw[len(prefix) + 1 :]
    parts = rest.split(":", 1)
    action = parts[0]
    arg = parts[1] if len(parts) > 1 else None
    return action, arg


def parse_month(arg: str | None) -> MonthRef | None:
    if not arg or len(arg) != 6:  # noqa: PLR2004
        return None
    try:
        year = int(arg[:4])
        month = int(arg[4:6])
    except ValueError:
        return None
    if not (1 <= month <= 12):  # noqa: PLR2004
        return None
    return MonthRef(year=year, month=month)


def parse_day(arg: str | None) -> date | None:
    if not arg or len(arg) != 8:  # noqa: PLR2004
        return None
    try:
        return date.fromisoformat(f"{arg[:4]}-{arg[4:6]}-{arg[6:8]}")
    except ValueError:
        return None


def _nav_max_month_first(*, limits: CalendarLimits) -> date:
    nav_max = limits.max_date if limits.plan_is_pro else limits.pro_max_date
    return _month_start(nav_max)


def _build_nav_row(*, prefix: str, shown_month: date, today: date, nav_max_month: date) -> list[InlineKeyboardButton]:
    current_month = _month_start(today)
    if shown_month > current_month:
        prev_month = _add_months(shown_month, -1)
        prev_cb = cb_month(prefix, year=prev_month.year, month=prev_month.month)
        prev_btn = InlineKeyboardButton(text="«", callback_data=prev_cb)
    else:
        prev_btn = InlineKeyboardButton(text=" ", callback_data=cb_noop(prefix))

    if shown_month < nav_max_month:
        next_month = _add_months(shown_month, 1)
        next_cb = cb_month(prefix, year=next_month.year, month=next_month.month)
        next_btn = InlineKeyboardButton(text="»", callback_data=next_cb)
    else:
        next_btn = InlineKeyboardButton(text=" ", callback_data=cb_noop(prefix))

    title = format_month_title(year=shown_month.year, month=shown_month.month)
    title_btn = InlineKeyboardButton(text=title, callback_data=cb_noop(prefix))
    return [prev_btn, title_btn, next_btn]


def _build_weekday_header_row(*, prefix: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=wd, callback_data=cb_noop(prefix)) for wd in _WEEKDAYS_RU]


def _day_cell(*, prefix: str, d: date, day_num: int, limits: CalendarLimits) -> InlineKeyboardButton:
    label = f"*{day_num}" if d == limits.today else str(day_num)

    if d < limits.min_date:
        cb = cb_noop(prefix)
    elif d <= limits.max_date:
        cb = cb_day(prefix, day=d)
    elif (not limits.plan_is_pro) and d <= limits.pro_max_date:
        label = f"{day_num}🔒"
        cb = cb_locked(prefix, day=d)
    else:
        cb = cb_noop(prefix)
    return InlineKeyboardButton(text=label, callback_data=cb)


def _build_month_cells(*, prefix: str, shown_month: date, limits: CalendarLimits) -> list[list[InlineKeyboardButton]]:
    year = int(shown_month.year)
    month = int(shown_month.month)
    first_weekday, days_in_month = calendar.monthrange(year, month)  # Mon=0

    cells: list[InlineKeyboardButton] = []
    for _ in range(first_weekday):
        cells.append(InlineKeyboardButton(text=" ", callback_data=cb_noop(prefix)))

    for day_num in range(1, int(days_in_month) + 1):
        d = date(year, month, int(day_num))
        cells.append(_day_cell(prefix=prefix, d=d, day_num=day_num, limits=limits))

    while len(cells) % 7 != 0:  # noqa: PLR2004
        cells.append(InlineKeyboardButton(text=" ", callback_data=cb_noop(prefix)))
    while len(cells) < 42:  # 6x7 for stable layout  # noqa: PLR2004
        cells.append(InlineKeyboardButton(text=" ", callback_data=cb_noop(prefix)))

    rows: list[list[InlineKeyboardButton]] = []
    for row_start in range(0, 42, 7):
        rows.append(cells[row_start : row_start + 7])
    return rows


def build(
    *,
    prefix: str,
    month: MonthRef,
    limits: CalendarLimits,
    controls: CalendarControls,
) -> InlineKeyboardMarkup:
    shown_month = date(int(month.year), int(month.month), 1)
    nav_max_month = _nav_max_month_first(limits=limits)

    rows: list[list[InlineKeyboardButton]] = [
        _build_nav_row(prefix=prefix, shown_month=shown_month, today=limits.today, nav_max_month=nav_max_month),
        _build_weekday_header_row(prefix=prefix),
        *_build_month_cells(prefix=prefix, shown_month=shown_month, limits=limits),
    ]

    if controls.show_pro_button and not limits.plan_is_pro:
        rows.append([InlineKeyboardButton(text="🔓 Pro (60 дней)", callback_data=cb_pro(prefix))])
    rows.append(
        [
            InlineKeyboardButton(text="Сегодня", callback_data=cb_today(prefix)),
            InlineKeyboardButton(text=controls.cancel_text, callback_data=controls.cancel_callback_data),
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
