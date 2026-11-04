"""Dev-запуск однією командою: API + тунель ngrok + бот (docs/TUNNEL.md).

`uv run python -m domovyk.dev`

Mini App на GitHub Pages ходить до API на ноуті через сталий домен ngrok.
Ця адреса — значення змінної репозиторію API_URL.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

from domovyk.adapters.tunnel.ngrok import NgrokTunnel
from domovyk.config import ConfigError, get_settings
from domovyk.container import Container
from domovyk.core.logging import setup_logging
from domovyk.interfaces.api.app import create_app
from domovyk.interfaces.telegram.__main__ import run_bot

log = logging.getLogger("domovyk.dev")


async def run() -> None:
    settings = get_settings()
    if not settings.is_dev:
        raise ConfigError("domovyk.dev — лише для APP_ENV=dev")
    settings.require("telegram_bot_token", "ngrok_domain")

    # Один контейнер (одне з'єднання з БД) на API і бота.
    container = Container(settings)
    # API слухає лише 127.0.0.1: назовні його відкриває тільки тунель.
    app = create_app(settings, services=container.services)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.api_port))
    api_task = asyncio.create_task(server.serve())
    while not server.started:
        if api_task.done():
            await api_task  # підніме помилку старту (наприклад, зайнятий порт)
        await asyncio.sleep(0.1)

    tunnel = NgrokTunnel(
        domain=settings.ngrok_domain,
        authtoken=settings.ngrok_authtoken.get_secret_value(),
        exe=settings.ngrok_path,
    )
    try:
        url = await tunnel.start(settings.api_port)
        log.info("API доступне з інтернету: %s  (це значення API_URL у GitHub)", url)
        await run_bot(settings, container.services)
    finally:
        await tunnel.stop()
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await api_task
        await container.close()


def main() -> None:
    setup_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
