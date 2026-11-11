"""Доменні правила комуналки (FR-UTL-1..4). Чиста логіка без I/O.

Вікно подачі показників — правило постачальника з довідника (`utility_providers.window_rule`),
а не код: у кожного міста й постачальника свої дати (FR-CTY-4). Формати правила:

- `{"from_day": 1, "to_day": 5}` — з 1 до 5 числа;
- `{"from_day": 10, "to": "last_working_day"}` — з 10 числа до останнього робочого дня місяця;
- `{"from_days_before_month_end": 2, "to_day_next_month": 3}` — за 2 дні до кінця місяця
  і до 3 числа наступного.

Порожнє або невідоме правило — вікна немає: показник можна подати будь-коли, нагадувань немає.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from domovyk.core.errors import InvalidInput

READING_MAX = Decimal("999999999.999")
LABEL_MAX = 40
ACCOUNT_MAX = 40
TARIFF_MAX = Decimal("100000")
PHOTO_MAX_BYTES = 10 * 1024 * 1024


class MeterKind(StrEnum):
    ELECTRICITY = "electricity"
    WATER_COLD = "water_cold"
    WATER_HOT = "water_hot"
    GAS = "gas"
    HEAT = "heat"


@dataclass(frozen=True)
class Window:
    """Вікно подачі: обидві дати включно."""

    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _last_working_day(year: int, month: int) -> date:
    """Останній будній день місяця. Державні свята не враховуємо (воєнний стан — без вихідних)."""
    day = _last_day(year, month)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _int(rule: dict[str, Any], key: str, lo: int, hi: int) -> int | None:
    value = rule.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        return None
    return value


def window_in_month(rule: dict[str, Any], year: int, month: int) -> Window | None:
    """Вікно, що починається в цьому місяці; None — правила немає або воно некоректне."""
    last = _last_day(year, month)
    from_day = _int(rule, "from_day", 1, 31)
    if from_day is not None:
        start = date(year, month, min(from_day, last.day))
        if rule.get("to") == "last_working_day":
            end = _last_working_day(year, month)
        else:
            to_day = _int(rule, "to_day", 1, 31)
            if to_day is None:
                return None
            end = date(year, month, min(to_day, last.day))
        return Window(start, end) if start <= end else None
    before = _int(rule, "from_days_before_month_end", 0, 27)
    next_day = _int(rule, "to_day_next_month", 1, 28)
    if before is not None and next_day is not None:
        ny, nm = _shift_month(year, month, 1)
        return Window(last - timedelta(days=before), date(ny, nm, next_day))
    return None


def current_or_next_window(rule: dict[str, Any], today: date) -> Window | None:
    """Вікно, в якому сьогодні, або найближче наступне."""
    candidates = []
    for delta in (-1, 0, 1):
        y, m = _shift_month(today.year, today.month, delta)
        w = window_in_month(rule, y, m)
        if w is not None and w.end >= today:
            candidates.append(w)
    return min(candidates, key=lambda w: w.start) if candidates else None


def reminder_stage(window: Window | None, today: date) -> Literal["open", "last"] | None:
    """Коли нагадувати тому, хто подає: протягом вікна (раз) і ще раз в останні два дні."""
    if window is None or not window.contains(today):
        return None
    return "last" if today >= window.end - timedelta(days=1) else "open"


def clean_reading(raw: Decimal | int | float | str) -> Decimal:
    """Показник з табло: невід'ємне число, до 3 знаків після коми."""
    try:
        value = Decimal(str(raw).strip().replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInput("Некоректний показник", field="value") from exc
    if not value.is_finite() or value < 0 or value > READING_MAX:
        raise InvalidInput("Некоректний показник", field="value")
    if value != value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP):
        raise InvalidInput("Не більше трьох знаків після коми", field="value")
    return value.quantize(Decimal("0.001"))


def check_not_less(previous: Decimal | None, value: Decimal) -> None:
    """FR-UTL-3: новий показник не може бути меншим за попередній."""
    if previous is not None and value < previous:
        raise InvalidInput("Показник менший за попередній", field="value", previous=str(previous))


def clean_tariff(raw: Decimal | None) -> Decimal | None:
    if raw is None:
        return None
    value = Decimal(str(raw))
    if not value.is_finite() or value <= 0 or value > TARIFF_MAX:
        raise InvalidInput("Некоректний тариф", field="tariff")
    return value.quantize(Decimal("0.0001"))


def clean_text(raw: str | None, field: str, limit: int) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split())
    if len(text) > limit:
        raise InvalidInput(f"Не довше {limit} символів", field=field)
    return text or None


def estimate(consumed: Decimal | None, tariff: Decimal | None) -> Decimal | None:
    """FR-UTL-4: орієнтовна сума за спожите (лише якщо власник задав тариф)."""
    if consumed is None or tariff is None:
        return None
    return (consumed * tariff).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
