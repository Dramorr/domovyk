"""Доменні правила оплат (FR-PAY-1..6). Чиста логіка без I/O.

Домовик гроші не проводить (правило 1): тут лише облік — що нараховано, що й коли
людина позначила оплаченим і що підтвердив власник.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from domovyk.core.errors import InvalidInput
from domovyk.domain.state_machine import TransitionTable

RECEIPT_MAX_BYTES = 10 * 1024 * 1024
NOTE_MAX = 300
RECEIPT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


class ChargeKind(StrEnum):
    RENT = "rent"
    DEPOSIT = "deposit"
    UTILITIES = "utilities"
    OTHER = "other"


class ChargeStatus(StrEnum):
    UPCOMING = "upcoming"  # нараховано наперед, дата ще не настала
    DUE = "due"  # сьогодні день оплати
    OVERDUE = "overdue"  # дата минула, оплати немає
    REPORTED = "reported"  # орендар позначив оплату — чекає підтвердження
    PAID = "paid"  # підтверджено на всю суму
    DISPUTED = "disputed"
    WAIVED = "waived"  # власник списав


class PaymentStatus(StrEnum):
    AUTO_ACCEPTED = "auto_accepted"  # квитанція збіглась, іде вікно оскарження (частина 2)
    PENDING_LANDLORD = "pending_landlord"  # потрібне підтвердження власника
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    REJECTED = "rejected"


PAYMENT = TransitionTable[PaymentStatus](
    "payment",
    {
        PaymentStatus.AUTO_ACCEPTED: frozenset(
            {PaymentStatus.CONFIRMED, PaymentStatus.DISPUTED, PaymentStatus.REJECTED}
        ),
        PaymentStatus.PENDING_LANDLORD: frozenset(
            {PaymentStatus.CONFIRMED, PaymentStatus.REJECTED}
        ),
        PaymentStatus.CONFIRMED: frozenset(),
        PaymentStatus.DISPUTED: frozenset({PaymentStatus.CONFIRMED, PaymentStatus.REJECTED}),
        PaymentStatus.REJECTED: frozenset(),
    },
)

# Оплати, що «в дорозі»: орендар позначив, власник ще не відповів.
IN_FLIGHT = frozenset({PaymentStatus.AUTO_ACCEPTED, PaymentStatus.PENDING_LANDLORD})


def due_date_in(year: int, month: int, due_day: int) -> date:
    return date(year, month, min(due_day, calendar.monthrange(year, month)[1]))


def rent_periods(start: date, last_day: date, due_day: int) -> list[tuple[date, date]]:
    """(перше число місяця, дата оплати) для кожного місяця оренди.

    Оплата за перший місяць — не раніше дати заїзду; місяць, у якому дата оплати
    вже після останнього дня оренди, не нараховується.
    """
    out: list[tuple[date, date]] = []
    year, month = start.year, start.month
    while True:
        period = date(year, month, 1)
        if period > last_day:
            break
        due = max(due_date_in(year, month, due_day), start)
        if due <= last_day:
            out.append((period, due))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def charge_status(
    due: date,
    today: date,
    amount: Decimal,
    confirmed: Decimal,
    in_flight: bool,
) -> ChargeStatus:
    """Статус нарахування з дат і оплат (FR-PAY-2, FR-PAY-5)."""
    if confirmed >= amount:
        return ChargeStatus.PAID
    if in_flight:
        return ChargeStatus.REPORTED
    if today < due:
        return ChargeStatus.UPCOMING
    if today == due:
        return ChargeStatus.DUE
    return ChargeStatus.OVERDUE


def reminder_offset(due: date, today: date, offsets: tuple[int, ...]) -> int | None:
    """Який із днів нагадування сьогодні (−3, 0, +1 від дати оплати) — або None."""
    for offset in offsets:
        if today == due + timedelta(days=offset):
            return offset
    return None


def clean_amount(raw: Decimal | int | float | str, limit: Decimal) -> Decimal:
    try:
        value = Decimal(str(raw)).quantize(Decimal("0.01"))
    except Exception as exc:
        raise InvalidInput("Некоректна сума", field="amount") from exc
    if value <= 0 or value > limit:
        raise InvalidInput("Некоректна сума", field="amount")
    return value


def clean_note(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split())
    if len(text) > NOTE_MAX:
        raise InvalidInput("Коментар — до 300 символів", field="note")
    return text or None


def sniff_receipt(data: bytes) -> str | None:
    """Тип файлу квитанції за вмістом, а не за назвою (PDF або фото)."""
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "image/heic"
    return None
