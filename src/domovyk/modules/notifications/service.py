"""Публічний інтерфейс модуля notifications: outbox сповіщень.

Сервіси інших модулів кладуть сповіщення в `notifications` (у своїй транзакції,
з `dedupe_key`), воркер розсилає через `adapters/notifier`. Тексти рендерить
інтерфейс (бот), бо тексти живуть у файлах локалізації.

TODO(M5): налаштування сповіщень і тихі години (BOT_FLOWS §18).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from domovyk.adapters.notifier.base import Notifier, NotifierError
from domovyk.core.clock import Clock
from domovyk.core.tx import Tx, TxManager
from domovyk.modules.notifications.repository import NotificationsRepository
from domovyk.modules.notifications.schemas import (
    NotificationType,
    PendingNotification,
    RenderedMessage,
)
from domovyk.modules.users.service import UsersService

__all__ = [
    "NotificationType",
    "NotificationsService",
    "PendingNotification",
    "RenderedMessage",
    "Renderer",
]

log = logging.getLogger(__name__)

Renderer = Callable[[PendingNotification], RenderedMessage | None]
MAX_ATTEMPTS = 5
RETRY_DELAY = timedelta(minutes=2)


class NotificationsService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], NotificationsRepository],
        users: UsersService,
        clock: Clock,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._clock = clock

    async def enqueue(
        self,
        tx: Tx,
        user_id: uuid.UUID,
        type_: NotificationType,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
        send_after: datetime | None = None,
    ) -> bool:
        """Покласти сповіщення в outbox у транзакції бізнес-операції. False — дубль."""
        return await self._repo(tx).add(
            user_id, type_.value, payload, dedupe_key, send_after or self._clock.now()
        )

    async def send_pending(self, render: Renderer, notifier: Notifier, *, limit: int = 50) -> int:
        """Розіслати те, що вже можна (воркер). Повертає кількість надісланих."""
        sent = 0
        for _ in range(limit):
            async with self._tx.begin() as tx:
                repo = self._repo(tx)
                item = await repo.claim_next(self._clock.now(), MAX_ATTEMPTS)
                if item is None:
                    break
                user = await self._users.get_by_id(item.user_id)
                if user is None or user.telegram_uid is None:
                    await repo.mark_failed(item.id, "no_chat", retry_at=None)
                    continue
                message = render(item)
                if message is None:
                    await repo.mark_failed(item.id, f"no_template:{item.type}", retry_at=None)
                    continue
                try:
                    await notifier.send_message(user.telegram_uid, message.text, message.buttons)
                except NotifierError as exc:
                    log.warning("Сповіщення %s не надіслано: %s", item.id, exc)
                    retry_at = None if exc.permanent else self._clock.now() + RETRY_DELAY
                    await repo.mark_failed(item.id, str(exc), retry_at=retry_at)
                    continue
                await repo.mark_sent(item.id, self._clock.now())
                sent += 1
        return sent
