"""Публічний інтерфейс модуля analytics: запис подій (FR-BETA-2)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from domovyk.core.tx import Tx
from domovyk.modules.analytics.repository import EventsRepository
from domovyk.modules.analytics.schemas import EventName, EventSource

__all__ = ["AnalyticsService", "EventName", "EventSource"]


class AnalyticsService:
    def __init__(self, repo: Callable[[Tx], EventsRepository]) -> None:
        self._repo = repo

    async def record(
        self,
        tx: Tx,
        name: EventName,
        *,
        user_id: uuid.UUID | None = None,
        source: EventSource = EventSource.API,
        props: dict[str, Any] | None = None,
    ) -> None:
        """Записати подію в межах транзакції бізнес-операції. Без персональних даних у props."""
        await self._repo(tx).add(user_id, name.value, props or {}, source.value)
