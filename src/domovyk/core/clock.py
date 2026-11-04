"""Єдине джерело «зараз». У тестах підміняємо на FixedClock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")


class Clock(Protocol):
    def now(self) -> datetime:
        """Поточний момент, timezone-aware, UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Годинник для тестів: стоїть, доки його не посунуть."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock потребує timezone-aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> None:
        self._at += delta


def to_kyiv(moment: datetime) -> datetime:
    """Перевести момент у час користувача (Europe/Kyiv)."""
    return moment.astimezone(KYIV)


system_clock: Clock = SystemClock()
