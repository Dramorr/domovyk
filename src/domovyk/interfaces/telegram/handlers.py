"""Хендлери бота. Тонкі: тексти з texts/uk.py, логіка — у сервісах модулів."""

from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from domovyk.interfaces.telegram.keyboards import open_app_keyboard
from domovyk.interfaces.telegram.texts import uk


def make_router(mini_app_url: str) -> Router:
    router = Router(name="entry")

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        name = escape(message.from_user.first_name) if message.from_user else ""
        await message.answer(
            uk.ONB_WELCOME.format(name=name),
            reply_markup=open_app_keyboard(mini_app_url, command.args),
        )

    @router.message(Command("menu"))
    async def menu(message: Message) -> None:
        await message.answer(uk.MENU_MAIN, reply_markup=open_app_keyboard(mini_app_url))

    return router
