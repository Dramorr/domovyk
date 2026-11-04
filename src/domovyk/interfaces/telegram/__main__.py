"""Запуск бота: `uv run python -m domovyk.interfaces.telegram` (long polling у dev)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from domovyk.config import Settings, get_settings
from domovyk.container import Container, Services
from domovyk.core.logging import setup_logging
from domovyk.interfaces.telegram.handlers import make_router
from domovyk.interfaces.telegram.texts import uk

log = logging.getLogger(__name__)


def _storage(settings: Settings) -> BaseStorage:
    if settings.bot_fsm_storage == "memory":
        return MemoryStorage()
    return RedisStorage.from_url(settings.redis_url)


async def run_bot(settings: Settings, services: Services | None = None) -> None:
    """Підняти бота й крутити long polling, доки не скасують."""
    settings.require("telegram_bot_token")
    container = None
    if services is None:
        container = Container(settings)
        services = container.services
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=_storage(settings), users=services.users)
    dp.include_router(make_router(settings.mini_app_url))

    await bot.set_my_commands(
        [
            BotCommand(command="start", description=uk.CMD_START),
            BotCommand(command="menu", description=uk.CMD_MENU),
            BotCommand(command="phone", description=uk.CMD_PHONE),
            BotCommand(command="whoami", description=uk.CMD_WHOAMI),
        ]
    )
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text=uk.MENU_BUTTON, web_app=WebAppInfo(url=settings.mini_app_url)
        )
    )
    me = await bot.get_me()
    log.info(
        "Бот @%s стартує (APP_ENV=%s), Mini App: %s",
        me.username,
        settings.app_env,
        settings.mini_app_url,
    )
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
        if container is not None:
            await container.close()


def main() -> None:
    settings = get_settings()
    setup_logging()
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
