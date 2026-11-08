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
REMIND_INTERVAL_SECONDS = 30 * 60


async def send_notifications(services: Services, notifier: Notifier, mini_app_url: str) -> int:
    """Outbox → Telegram (BOT_FLOWS §18)."""
    sent = await services.notifications.send_pending(make_renderer(mini_app_url), notifier)
    if sent:
        log.info("Надіслано сповіщень: %s", sent)
    return sent


async def purge_ownership_documents(services: Services) -> int:
    """FR-PRP-4, п. 4: файли довідок — геть через N днів після рішення."""
    leases = await services.leases.purge_cancelled()  # FR-LSE-9
    if leases:
        log.info("Видалено скасованих оренд: %s", leases)
    purged = await services.properties.purge_documents()
    if purged:
        log.info("Видалено документів власності: %s", purged)
    return purged


async def finish_leases(services: Services) -> int:
    """FR-LSE-7: оренди з датою розірвання, що настала, або з минулим строком → ended."""
    done = await services.leases.finish_due()
    if done:
        log.info("Завершено оренд: %s", done)
    return done


async def payments_daily(services: Services) -> int:
    """FR-PAY-1/2/4: нарахування наперед, статуси за датами, нагадування."""
    stats = await services.payments.run_daily()
    if any(stats.values()):
        log.info("Оплати: %s", stats)
    return sum(stats.values())


async def confirm_auto_payments(services: Services) -> int:
    """FR-PAY-3: вікно оскарження минуло — автоматично зараховані оплати стають confirmed."""
    done = await services.payments.confirm_expired()
    if done:
        log.info("Підтверджено оплат після вікна оскарження: %s", done)
    return done


async def remind_unsigned_contracts(services: Services) -> int:
    """CTR-SIGN: нагадування тим, хто ще не підписав договір (24 і 72 год)."""
    queued = await services.contracts.remind_unsigned()
    if queued:
        log.info("Нагадувань про підпис договору: %s", queued)
    return queued


async def dev_background_loop(services: Services, notifier: Notifier, mini_app_url: str) -> None:
    """Для dev без окремого воркера: outbox кожні 15 с, прибирання кожні 6 год."""
    since_purge = PURGE_INTERVAL_SECONDS
    since_remind = REMIND_INTERVAL_SECONDS
    while True:
        try:
            await send_notifications(services, notifier, mini_app_url)
            if since_purge >= PURGE_INTERVAL_SECONDS:
                await purge_ownership_documents(services)
                since_purge = 0
            if since_remind >= REMIND_INTERVAL_SECONDS:
                await remind_unsigned_contracts(services)
                await finish_leases(services)
                await payments_daily(services)
                await confirm_auto_payments(services)
                since_remind = 0
        except Exception:  # фонова петля не має падати через один збій
            log.exception("Помилка фонової задачі")
        await asyncio.sleep(OUTBOX_INTERVAL_SECONDS)
        since_purge += OUTBOX_INTERVAL_SECONDS
        since_remind += OUTBOX_INTERVAL_SECONDS
