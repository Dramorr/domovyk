"""Доставка повідомлень користувачу (правило 4). Зараз — Telegram-бот."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Button:
    """Кнопка під повідомленням: відкрити Mini App (web_app_url) або посилання (url)."""

    text: str
    web_app_url: str | None = None
    url: str | None = None


class NotifierError(Exception):
    """`permanent` — повторювати марно (бот заблоковано, чат не існує)."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Notifier(Protocol):
    async def send_message(
        self, chat_id: int, text: str, buttons: Sequence[Button] = ()
    ) -> None: ...

    async def send_document(
        self, chat_id: int, filename: str, data: bytes, caption: str | None = None
    ) -> None: ...
