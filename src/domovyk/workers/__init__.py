"""Фоновий воркер (arq + Redis): outbox сповіщень, прибирання документів; далі —
нарахування, нагадування, автопідтвердження.

Запуск: `uv run arq domovyk.workers.WorkerSettings`.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from domovyk.config import get_settings
from domovyk.container import Container
from domovyk.core.clock import system_clock
from domovyk.core.logging import setup_logging
from domovyk.workers import jobs

log = logging.getLogger(__name__)

# Налаштування читаються під час імпорту: небезпечна конфігурація
# (наприклад, DEV_SKIP_OWNERSHIP_CHECK поза dev) не дасть воркеру стартувати.
_settings = get_settings()


async def heartbeat(ctx: dict[str, Any]) -> str:
    """Перевірка, що воркер живий."""
    now = system_clock.now().isoformat()
    log.info("heartbeat %s", now)
    return now


async def send_notifications(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    if container.notifier is None:
        return 0
    return await jobs.send_notifications(
        container.services, container.notifier, _settings.mini_app_url
    )


async def purge_ownership_documents(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    return await jobs.purge_ownership_documents(container.services)


async def remind_unsigned_contracts(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    return await jobs.remind_unsigned_contracts(container.services)


async def finish_leases(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    return await jobs.finish_leases(container.services)


async def payments_daily(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    return await jobs.payments_daily(container.services)


async def confirm_auto_payments(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    return await jobs.confirm_auto_payments(container.services)


async def meter_reminders(ctx: dict[str, Any]) -> int:
    container: Container = ctx["container"]
    return await jobs.meter_reminders(container.services)


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    # arq має власний обробник логів; без цього кожен рядок дублюється через root.
    logging.getLogger("arq").propagate = False
    _settings.require("telegram_bot_token")
    ctx["container"] = Container(_settings)
    log.info("Воркер стартує (APP_ENV=%s)", _settings.app_env)


async def shutdown(ctx: dict[str, Any]) -> None:
    container: Container | None = ctx.get("container")
    if container is not None:
        await container.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        heartbeat,
        send_notifications,
        purge_ownership_documents,
        remind_unsigned_contracts,
        finish_leases,
        payments_daily,
        confirm_auto_payments,
        meter_reminders,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(heartbeat, minute={0, 15, 30, 45}, run_at_startup=True),
        cron(send_notifications, second={0, 15, 30, 45}),
        cron(purge_ownership_documents, hour={3}, minute={30}),
        cron(remind_unsigned_contracts, minute={5, 35}),
        cron(finish_leases, hour={0, 6, 12, 18}, minute={10}),
        # О 9:00 за Києвом (UTC+2/+3) — нагадування приходять зранку, не вночі.
        cron(payments_daily, hour={6, 7}, minute={0}),
        cron(confirm_auto_payments, minute={20}),
        # Близько 10:00 за Києвом; повтор не дублює (dedupe_key).
        cron(meter_reminders, hour={7, 8}, minute={5}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
