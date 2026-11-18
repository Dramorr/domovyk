"""Доменні правила показів (FR-APP-4, FR-APP-5). Чиста логіка без I/O.

Власник додає слоти: час і місткість (більше одного — груповий показ). Схвалений орендар
записується на слот. Після показу обидві сторони кажуть, чи він відбувся.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from domovyk.core.clock import KYIV
from domovyk.core.errors import InvalidInput
from domovyk.domain.applications import ApplicationStatus


class SlotStatus(StrEnum):
    OPEN = "open"
    CANCELLED = "cancelled"  # власник скасував — записані повертаються до вибору часу
    DONE = "done"  # зарезервовано на майбутнє: архів слотів


class ShowingAnswer(StrEnum):
    DONE = "done"  # показ відбувся
    NO_SHOW = "no_show"  # інша сторона не прийшла
    MOVED = "moved"  # домовились на інший час


@dataclass(frozen=True)
class SlotRules:
    min_lead_minutes: int = 60  # не раніше, ніж за годину від «зараз»
    horizon_days: int = 60  # не далі, ніж на два місяці вперед
    min_minutes: int = 15
    max_minutes: int = 240
    max_capacity: int = 20  # як CHECK у БД


def clean_slot(
    day: date, start: time, minutes: int, capacity: int, now: datetime, rules: SlotRules
) -> tuple[datetime, datetime, int]:
    """Слот у часі Києва → (початок, кінець) в UTC і місткість. Помилка — з полем форми."""
    if not rules.min_minutes <= minutes <= rules.max_minutes:
        raise InvalidInput("Тривалість показу поза межами", field="minutes")
    if not 1 <= capacity <= rules.max_capacity:
        raise InvalidInput("Місткість поза межами", field="capacity")
    starts = datetime.combine(day, start.replace(second=0, microsecond=0), tzinfo=KYIV)
    starts = starts.astimezone(UTC)
    if starts < now + timedelta(minutes=rules.min_lead_minutes):
        raise InvalidInput("Показ має бути пізніше", field="start", reason="too_soon")
    if starts > now + timedelta(days=rules.horizon_days):
        raise InvalidInput("Показ надто далеко", field="day", reason="too_far")
    return starts, starts + timedelta(minutes=minutes), capacity


def resolve_showing(
    tenant: ShowingAnswer | None, landlord: ShowingAnswer | None, *, deadline_passed: bool
) -> ApplicationStatus | None:
    """FR-APP-5: результат показу з відповідей сторін; None — ще чекаємо.

    «Перенесли» від будь-кого — назад до вибору часу. Обидва відповіли (або вікно минуло):
    хтось каже «відбувся» — відбувся (обидві відповіді лишаються в історії для розгляду);
    лише «не прийшли» — no_show. Ніхто не відповів за вікно — заявка повертається до вибору
    часу: краще дати ще шанс, ніж вигадати результат.
    """
    answers = [a for a in (tenant, landlord) if a is not None]
    if ShowingAnswer.MOVED in answers:
        return ApplicationStatus.APPROVED
    if len(answers) < 2 and not deadline_passed:
        return None
    if not answers:
        return ApplicationStatus.APPROVED
    if ShowingAnswer.DONE in answers:
        return ApplicationStatus.SHOWING_DONE
    return ApplicationStatus.NO_SHOW
