from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.texts.base import Translator, noop_t as _noop_t


def online_status(*, t: Translator = _noop_t) -> str:
    return t("📱 Telegram подключён")


def offline_status(*, t: Translator = _noop_t) -> str:
    return t("📵 Офлайн-клиент (без Telegram)")


def offline_hint(*, t: Translator = _noop_t) -> str:
    return t("ℹ️ Клиент без Telegram. Уведомления о записи не отправляются.")


def noshow_hint(*, count: int, t: Translator = _noop_t) -> str:
    return t(f"⚠️ Было {int(count)} неявки.\nРекомендуем подтверждать запись вручную.")


def last_visit(*, day: date, t: Translator = _noop_t) -> str:
    return t(f"📅 Последний визит: {day:%d.%m.%Y}")


def total_visits(*, count: int, t: Translator = _noop_t) -> str:
    return t(f"⏳ Всего визитов: {int(count)}")


def no_show(*, count: int, t: Translator = _noop_t) -> str:
    return t(f"❌ No-show: {int(count)}")


@dataclass(frozen=True)
class ClientSummary:
    last_visit_day: date | None
    total_visits: int
    no_show: int


@dataclass(frozen=True)
class ClientHints:
    show_offline_hint: bool = True
    show_noshow_hint: bool = True
    noshow_hint_threshold: int = 2


def card(  # noqa: C901
    *,
    name: str,
    is_offline: bool,
    phone: str | None,
    summary: ClientSummary,
    hints: ClientHints = ClientHints(),
    t: Translator = _noop_t,
) -> str:
    lines: list[str] = [name, offline_status(t=t) if is_offline else online_status(t=t)]

    if phone:
        lines.extend(["", t(f"📞 {phone}")])

    # Summary block: hide when empty; also hide zeros by convention.
    summary_lines: list[str] = []
    if summary.last_visit_day is not None:
        summary_lines.append(last_visit(day=summary.last_visit_day, t=t))
    if summary.total_visits > 0:
        summary_lines.append(total_visits(count=summary.total_visits, t=t))
    if summary.no_show > 0:
        summary_lines.append(no_show(count=summary.no_show, t=t))

    if summary_lines:
        lines.extend(["", *summary_lines])

    status_hints: list[str] = []
    if hints.show_offline_hint and is_offline:
        status_hints.append(offline_hint(t=t))
    if hints.show_noshow_hint and summary.no_show >= int(hints.noshow_hint_threshold):
        status_hints.append(noshow_hint(count=int(summary.no_show), t=t))

    if status_hints:
        lines.extend(["", *status_hints])

    return "\n".join(lines).strip()
