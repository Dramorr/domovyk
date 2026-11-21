"""Стрічка сповіщень для Mini App: той самий текст, що прийшов у бот, без HTML.

Текст рендерить бот (`interfaces/telegram/notifications.py`), щоб формулювання
в стрічці й у чаті з ботом збігались. Перехід — `start` з кнопки Mini App
(той самий deep link, що й у кнопці під повідомленням).
"""

import html
import re
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel

from domovyk.modules.notifications.service import FeedEntry, PendingNotification, Renderer

_TAG = re.compile(r"<[^>]+>")
# Рендереру потрібні лише тип і payload; чий це запис, для тексту не важить.
_NO_USER = uuid.UUID(int=0)


class FeedItemOut(BaseModel):
    id: int
    text: str
    # Куди вести з Mini App (prop_…, lease_…, chat_…); None — сповіщення без переходу.
    start: str | None
    at: datetime
    read: bool


class FeedOut(BaseModel):
    items: list[FeedItemOut]
    unread: int


class UnreadOut(BaseModel):
    unread: int


def feed_item(entry: FeedEntry, render: Renderer) -> FeedItemOut | None:
    message = render(PendingNotification(entry.id, _NO_USER, entry.type, entry.payload))
    if message is None:
        return None
    text = html.unescape(_TAG.sub("", message.text)).strip()
    start = None
    for button in message.buttons:
        if button.web_app_url:
            start = parse_qs(urlsplit(button.web_app_url).query).get("start", [None])[0]
            if start:
                break
    return FeedItemOut(id=entry.id, text=text, start=start, at=entry.at, read=entry.read)
