"""Запуск бота: `uv run python -m domovyk.interfaces.telegram` (long polling у dev)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from domovyk.config import get_settings
from domovyk.core.logging import setup_logging
from domovyk.interfaces.telegram.handlers import make_router
from domovyk.interfaces.telegram.texts import uk

log = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    settings.require("telegram_bot_token")
    setup_logging()

    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=RedisStorage.from_url(settings.redis_url))
    dp.include_router(make_router(settings.mini_app_url))

    await bot.set_my_commands(
        [
            BotCommand(command="start", description=uk.CMD_START),
            BotCommand(command="menu", description=uk.CMD_MENU),
        ]
    )
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text=uk.MENU_BUTTON, web_app=WebAppInfo(url=settings.mini_app_url)
        )
    )
    log.info("Бот стартує (APP_ENV=%s), Mini App: %s", settings.app_env, settings.mini_app_url)
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
