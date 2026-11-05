"""Тіла фонових задач. Їх викликає воркер arq (prod) і dev-запуск (`domovyk.dev`)."""

from __future__ import annotations

import asyncio
import logging

from domovyk.adapters.notifier.base import Notifier
from domovyk.container import Services
from domovyk.interfaces.telegram.notifications import make_renderer

log = logging.getLogger(__name__)

OUTBOX_INTERVAL_SECONDS = 15
PURGE_INTERVAL_SECONDS = 6 * 60 * 60


async def send_notifications(services: Services, notifier: Notifier, mini_app_url: str) -> int:
    """Outbox → Telegram (BOT_FLOWS §18)."""
    sent = await services.notifications.send_pending(make_renderer(mini_app_url), notifier)
    if sent:
        log.info("Надіслано сповіщень: %s", sent)
    return sent


async def purge_ownership_documents(services: Services) -> int:
    """FR-PRP-4, п. 4: файли довідок — геть через N днів після рішення."""
    purged = await services.properties.purge_documents()
    if purged:
        log.info("Видалено документів власності: %s", purged)
    return purged


async def dev_background_loop(services: Services, notifier: Notifier, mini_app_url: str) -> None:
    """Для dev без окремого воркера: outbox кожні 15 с, прибирання кожні 6 год."""
    since_purge = PURGE_INTERVAL_SECONDS
    while True:
        try:
            await send_notifications(services, notifier, mini_app_url)
            if since_purge >= PURGE_INTERVAL_SECONDS:
                await purge_ownership_documents(services)
                since_purge = 0
        except Exception:  # фонова петля не має падати через один збій
            log.exception("Помилка фонової задачі")
        await asyncio.sleep(OUTBOX_INTERVAL_SECONDS)
        since_purge += OUTBOX_INTERVAL_SECONDS
