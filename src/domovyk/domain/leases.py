"""Доменні правила оренди (FR-LSE-1..7). Чиста логіка без I/O.

Строк оренди обов'язковий і не довший за 35 місяців (правило 6 CLAUDE.md:
довше — потрібен нотаріус). Те саме перевіряє CHECK у БД.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from enum import StrEnum

from domovyk.core.errors import InvalidInput
from domovyk.domain.state_machine import TransitionTable

MAX_TERM_MONTHS = 35
RENT_MAX = 1_000_000
DUE_DAY_MIN, DUE_DAY_MAX = 1, 28
INVITE_TTL_DAYS = 7  # FR-LSE-1
MESSAGE_MAX = 2000


class LeaseOrigin(StrEnum):
    APPLICATION = "application"
    INVITE = "invite"


class LeaseStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_TENANT = "awaiting_tenant"  # чекаємо відповідального орендаря й згоду з умовами
    AWAITING_SIGNATURES = "awaiting_signatures"  # умови погоджено, чекаємо договір (M4)
    ACTIVE = "active"
    ENDING = "ending"
    ENDED = "ended"
    CANCELLED = "cancelled"


class MemberRole(StrEnum):
    RESPONSIBLE = "responsible"
    CO_TENANT = "co_tenant"


class PayMethod(StrEnum):
    CARD = "card"
    IBAN = "iban"
    JAR = "jar"  # банка monobank
    CASH = "cash"


LEASE = TransitionTable[LeaseStatus](
    "lease",
    {
        LeaseStatus.DRAFT: frozenset({LeaseStatus.AWAITING_TENANT, LeaseStatus.CANCELLED}),
        LeaseStatus.AWAITING_TENANT: frozenset(
            {LeaseStatus.AWAITING_SIGNATURES, LeaseStatus.CANCELLED}
        ),
        LeaseStatus.AWAITING_SIGNATURES: frozenset(
            # Назад — якщо власник змінив умови й орендар має погодити їх знову.
            {LeaseStatus.AWAITING_TENANT, LeaseStatus.ACTIVE, LeaseStatus.CANCELLED}
        ),
        LeaseStatus.ACTIVE: frozenset({LeaseStatus.ENDING}),
        LeaseStatus.ENDING: frozenset({LeaseStatus.ENDED, LeaseStatus.ACTIVE}),
        LeaseStatus.ENDED: frozenset(),
        LeaseStatus.CANCELLED: frozenset(),
    },
)

# Стани, у яких оренда «жива»: займає юніт, учасники бачать картку й чат.
OPEN_STATUSES = frozenset(
    {
        LeaseStatus.DRAFT,
        LeaseStatus.AWAITING_TENANT,
        LeaseStatus.AWAITING_SIGNATURES,
        LeaseStatus.ACTIVE,
        LeaseStatus.ENDING,
    }
)
# До активації умови ще можна змінювати без додаткової угоди.
EDITABLE_STATUSES = frozenset(
    {LeaseStatus.DRAFT, LeaseStatus.AWAITING_TENANT, LeaseStatus.AWAITING_SIGNATURES}
)


def is_open(status: LeaseStatus) -> bool:
    return status in OPEN_STATUSES


# ------------------------------------------------------------------- строк


def add_months(start: date, months: int) -> date:
    """Та сама дата через N місяців; 31 січня + 1 = 28/29 лютого."""
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def end_for_term(start: date, months: int) -> date:
    """Остання дата оренди на N місяців: 1.12.2026 + 11 міс → 31.10.2027."""
    if not 1 <= months <= MAX_TERM_MONTHS:
        raise InvalidInput("Строк — від 1 до 35 місяців", field="months")
    return date.fromordinal(add_months(start, months).toordinal() - 1)


def validate_term(start: date, end: date) -> None:
    if end <= start:
        raise InvalidInput("Дата завершення має бути пізніше за початок", field="end_date")
    if end > add_months(start, MAX_TERM_MONTHS):
        # Більше 35 місяців — договір потребує нотаріуса (правило 6).
        raise InvalidInput("Строк оренди — не більше 35 місяців", field="end_date")


def months_between(start: date, end: date) -> int:
    """Повних місяців між датами (для «залишилось N міс»)."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def validate_money(rent: int, due_day: int, deposit: int | None) -> None:
    if not 0 < rent <= RENT_MAX:
        raise InvalidInput("Некоректна сума оренди", field="rent_amount")
    if not DUE_DAY_MIN <= due_day <= DUE_DAY_MAX:
        raise InvalidInput("День оплати — від 1 до 28", field="rent_due_day")
    if deposit is not None and not 0 <= deposit <= RENT_MAX:
        raise InvalidInput("Некоректний депозит", field="deposit_amount")


# --------------------------------------------------------------- реквізити

_DIGITS = re.compile(r"\D+")
_IBAN_RE = re.compile(r"^UA\d{27}$")
_JAR_RE = re.compile(r"^https://send\.monobank\.ua/jar/[A-Za-z0-9]{6,20}$")


def luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def normalize_card(raw: str) -> str:
    digits = _DIGITS.sub("", raw)
    if len(digits) != 16 or not luhn_ok(digits):
        raise InvalidInput("Перевір номер картки", field="card")
    return digits


def iban_ok(iban: str) -> bool:
    """Контрольна сума IBAN (ISO 13616, mod 97)."""
    moved = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in moved)
    return int(numeric) % 97 == 1


def normalize_iban(raw: str) -> str:
    iban = re.sub(r"\s+", "", raw).upper()
    if not _IBAN_RE.fullmatch(iban) or not iban_ok(iban):
        raise InvalidInput("Перевір IBAN: UA і 27 цифр", field="iban")
    return iban


def normalize_jar(raw: str) -> str:
    url = raw.strip()
    if not _JAR_RE.fullmatch(url):
        raise InvalidInput("Потрібне посилання на банку monobank", field="jar_url")
    return url


def mask_card(card: str) -> str:
    return f"**** {card[-4:]}"


def mask_iban(iban: str) -> str:
    return f"UA** **** {iban[-4:]}"


def short_name(full: str) -> str:
    """«Ірина Коваль» → «Ірина К.» — для показу отримувача до активації."""
    parts = full.split()
    if len(parts) < 2:
        return full
    return f"{parts[0]} {parts[1][0]}."


# ------------------------------------------------------------------- чат


def clean_message(body: str) -> str:
    text = body.strip()
    if not text:
        raise InvalidInput("Порожнє повідомлення", field="body")
    if len(text) > MESSAGE_MAX:
        raise InvalidInput("Задовге повідомлення", field="body")
    return text
