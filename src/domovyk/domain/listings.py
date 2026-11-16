"""Доменні правила оголошень (FR-LST-1..5, ADR 0018). Чиста логіка без I/O.

Оголошення — це умови угоди поверх анкети об'єкта. Живі стани — draft, active,
paused; rented, archived і blocked — кінцеві: це історія, на яку посилатимуться
заявки й відгуки. Нове оголошення на той самий об'єкт — новий запис.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta
from enum import StrEnum

from domovyk.core.errors import InvalidInput
from domovyk.domain.leases import MAX_TERM_MONTHS
from domovyk.domain.state_machine import TransitionTable

PRICE_MIN, PRICE_MAX = 100, 1_000_000  # грн на місяць, як CHECK у БД
DEPOSIT_MAX = 3_000_000
AVAILABLE_AHEAD_DAYS = 365  # дата заїзду — не далі ніж за рік


class ListingStatus(StrEnum):
    DRAFT = "draft"  # власник готує; публікація — після перевірок
    ACTIVE = "active"  # видно в пошуку
    PAUSED = "paused"  # тимчасово сховано власником
    RENTED = "rented"  # на об'єкт створено оренду
    ARCHIVED = "archived"  # знято власником
    BLOCKED = "blocked"  # заблоковано модератором (M7)


LIVE = frozenset({ListingStatus.DRAFT, ListingStatus.ACTIVE, ListingStatus.PAUSED})
PUBLISHED = frozenset({ListingStatus.ACTIVE, ListingStatus.PAUSED})

LISTING = TransitionTable[ListingStatus](
    "listing",
    {
        ListingStatus.DRAFT: frozenset({ListingStatus.ACTIVE}),
        ListingStatus.ACTIVE: frozenset(
            {
                ListingStatus.PAUSED,
                ListingStatus.RENTED,
                ListingStatus.ARCHIVED,
                ListingStatus.BLOCKED,
            }
        ),
        ListingStatus.PAUSED: frozenset(
            {
                ListingStatus.ACTIVE,
                ListingStatus.RENTED,
                ListingStatus.ARCHIVED,
                ListingStatus.BLOCKED,
            }
        ),
    },
)


def clean_price(value: int) -> int:
    if not PRICE_MIN <= value <= PRICE_MAX:
        raise InvalidInput("Ціна поза межами", field="price_amount")
    return value


def clean_deposit(value: int | None) -> int | None:
    if value is None:
        return None
    if not 0 <= value <= DEPOSIT_MAX:
        raise InvalidInput("Депозит поза межами", field="deposit_amount")
    return value


def clean_min_term(value: int | None) -> int | None:
    """Мінімальний строк не довший за найдовший можливий договір (правило 6)."""
    if value is None:
        return None
    if not 1 <= value <= MAX_TERM_MONTHS:
        raise InvalidInput("Строк поза межами", field="min_term_months")
    return value


def clean_available_from(value: date | None, today: date) -> date | None:
    """Минула дата — «вже вільна»: зберігаємо як None, щоб не показувати старе число."""
    if value is None or value <= today:
        return None
    if value > today + timedelta(days=AVAILABLE_AHEAD_DAYS):
        raise InvalidInput("Дата заїзду задалеко", field="available_from")
    return value


_WORD_RE = re.compile(r"[\w'’ʼ]+", re.UNICODE)


def find_risk_words(text: str, stems: Iterable[str]) -> list[str]:
    """Основи «ріелторських» слів, з яких починається хоч одне слово тексту (FR-LST-5).

    Збіг лише з початку слова: «агент» знайде «агентство», але не «реагент».
    """
    words = [w.lower() for w in _WORD_RE.findall(text)]
    found: list[str] = []
    for stem in stems:
        s = stem.lower()
        if s and s not in found and any(w.startswith(s) for w in words):
            found.append(s)
    return found
