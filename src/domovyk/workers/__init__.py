"""Фоновий воркер (arq + Redis): нарахування, нагадування, автопідтвердження, outbox.

Запуск: `uv run arq domovyk.workers.WorkerSettings`.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from domovyk.config import get_settings
from domovyk.core.clock import system_clock
from domovyk.core.logging import setup_logging

log = logging.getLogger(__name__)

# Налаштування читаються під час імпорту: небезпечна конфігурація
# (наприклад, DEV_SKIP_OWNERSHIP_CHECK поза dev) не дасть воркеру стартувати.
_settings = get_settings()


async def heartbeat(ctx: dict[str, Any]) -> str:
    """Перевірка, що воркер живий. Реальні задачі з'являються з M3–M5."""
    now = system_clock.now().isoformat()
    log.info("heartbeat %s", now)
    return now


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    log.info("Воркер стартує (APP_ENV=%s)", _settings.app_env)


class WorkerSettings:
    functions: ClassVar[list[Any]] = [heartbeat]
    cron_jobs: ClassVar[list[Any]] = [cron(heartbeat, minute={0, 15, 30, 45}, run_at_startup=True)]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
