"""Доступ до таблиці events. Лише всередині модуля analytics."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from domovyk.core.tx import Tx, session_of
from domovyk.modules.analytics.models import EventRow


class EventsRepository(Protocol):
    async def add(
        self, user_id: uuid.UUID | None, name: str, props: dict[str, Any], source: str
    ) -> None: ...


class SqlEventsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def add(
        self, user_id: uuid.UUID | None, name: str, props: dict[str, Any], source: str
    ) -> None:
        self._s.add(EventRow(user_id=user_id, name=name, props=props, source=source))
        await self._s.flush()
