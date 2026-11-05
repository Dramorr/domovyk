"""Відправка через Bot API (aiogram). Тексти приходять готовими (HTML)."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from domovyk.adapters.notifier.base import Button, NotifierError


def _markup(buttons: Sequence[Button]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = []
    for b in buttons:
        if b.web_app_url:
            rows.append([InlineKeyboardButton(text=b.text, web_app=WebAppInfo(url=b.web_app_url))])
        elif b.url:
            rows.append([InlineKeyboardButton(text=b.text, url=b.url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    @classmethod
    def from_token(cls, token: str) -> TelegramNotifier:
        return cls(Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)))

    async def send_message(self, chat_id: int, text: str, buttons: Sequence[Button] = ()) -> None:
        try:
            await self._bot.send_message(chat_id, text, reply_markup=_markup(buttons))
        except Exception as exc:
            raise _wrap(exc) from exc

    async def send_document(
        self, chat_id: int, filename: str, data: bytes, caption: str | None = None
    ) -> None:
        try:
            await self._bot.send_document(
                chat_id, BufferedInputFile(data, filename=filename), caption=caption
            )
        except Exception as exc:
            raise _wrap(exc) from exc

    async def close(self) -> None:
        await self._bot.session.close()


def _wrap(exc: Exception) -> NotifierError:
    if isinstance(exc, TelegramForbiddenError | TelegramBadRequest):
        return NotifierError(exc.__class__.__name__, permanent=True)
    if isinstance(exc, TelegramRetryAfter | TelegramNetworkError | TelegramServerError):
        return NotifierError(exc.__class__.__name__)
    return NotifierError(exc.__class__.__name__)
