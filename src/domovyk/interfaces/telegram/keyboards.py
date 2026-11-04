"""Клавіатури бота."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from domovyk.interfaces.telegram.texts import uk

# Deep-link payload Telegram: до 64 символів [A-Za-z0-9_-] (BOT_FLOWS §0.4).
_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def mini_app_url(base_url: str, start: str | None = None) -> str:
    """URL Mini App; валідний deep-link payload передаємо як ?start=."""
    if not start or not _PAYLOAD_RE.fullmatch(start):
        return base_url
    parts = urlsplit(base_url)
    query = f"{parts.query}&" if parts.query else ""
    return urlunsplit(parts._replace(query=query + urlencode({"start": start})))


def open_app_keyboard(base_url: str, start: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=uk.BTN_OPEN_APP,
                    web_app=WebAppInfo(url=mini_app_url(base_url, start)),
                )
            ]
        ]
    )
