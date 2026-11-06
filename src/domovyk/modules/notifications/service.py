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
from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.tx import Tx, TxManager
from domovyk.modules.notifications.repository import NotificationsRepository
from domovyk.modules.notifications.schemas import (
    LEASE_OPTIONAL_TYPES,
    LeaseNotificationPrefs,
    NotificationType,
    PendingNotification,
    RenderedMessage,
)
from domovyk.modules.users.service import UsersService

__all__ = [
    "LeaseNotificationPrefs",
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

    # ------------------------------------------------- налаштування (FR-NTF-1)

    _QUIET = "quiet_hours"

    async def lease_prefs(
        self, tx: Tx, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> LeaseNotificationPrefs:
        raw = await self._repo(tx).prefs(user_id, lease_id)
        prefs = LeaseNotificationPrefs()
        for t in LEASE_OPTIONAL_TYPES:
            if t.value in raw:
                setattr(prefs, t.value, raw[t.value][0])
        if self._QUIET in raw:
            params = raw[self._QUIET][1]
            prefs.quiet_from = int(params.get("from", prefs.quiet_from))
            prefs.quiet_to = int(params.get("to", prefs.quiet_to))
        return prefs

    async def set_lease_prefs(
        self, tx: Tx, user_id: uuid.UUID, lease_id: uuid.UUID, prefs: LeaseNotificationPrefs
    ) -> LeaseNotificationPrefs:
        repo = self._repo(tx)
        for t in LEASE_OPTIONAL_TYPES:
            await repo.set_pref(user_id, lease_id, t.value, bool(getattr(prefs, t.value)), {})
        await repo.set_pref(
            user_id, lease_id, self._QUIET, True, {"from": prefs.quiet_from, "to": prefs.quiet_to}
        )
        return prefs

    async def enqueue_for_lease(
        self,
        tx: Tx,
        user_id: uuid.UUID,
        lease_id: uuid.UUID,
        type_: NotificationType,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> bool:
        """Як enqueue, але з налаштуваннями людини: вимкнене — не шлемо, тихі години — без звуку."""
        prefs = await self.lease_prefs(tx, user_id, lease_id)
        if type_ in LEASE_OPTIONAL_TYPES and not getattr(prefs, type_.value):
            return False
        hour = to_kyiv(self._clock.now()).hour
        if _in_quiet_hours(hour, prefs.quiet_from, prefs.quiet_to):
            payload = {**payload, "_silent": True}
        return await self.enqueue(tx, user_id, type_, payload, dedupe_key=dedupe_key)

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
                    await notifier.send_message(
                        user.telegram_uid,
                        message.text,
                        message.buttons,
                        silent=bool(item.payload.get("_silent")),
                    )
                except NotifierError as exc:
                    log.warning("Сповіщення %s не надіслано: %s", item.id, exc)
                    retry_at = None if exc.permanent else self._clock.now() + RETRY_DELAY
                    await repo.mark_failed(item.id, str(exc), retry_at=retry_at)
                    continue
                await repo.mark_sent(item.id, self._clock.now())
                sent += 1
        return sent


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """22→8 — через північ; 13→15 — удень; start == end — тихих годин немає."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end
