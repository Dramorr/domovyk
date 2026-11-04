"""Перевірка initData Telegram Mini App (FR-TMA-2).

Підпис перевіряє `aiogram.utils.web_app` (HMAC-SHA256, ключ із токена бота).
Тут додатково — свіжість `auth_date` і перетворення в наш DTO.
"""

from __future__ import annotations

import json
from datetime import timedelta

from aiogram.utils.web_app import safe_parse_webapp_init_data
from pydantic import BaseModel

from domovyk.core.clock import Clock

# Допуск на розбіжність годинників між Telegram і сервером.
_FUTURE_SKEW = timedelta(seconds=60)


class InitDataError(Exception):
    """initData відсутній, підроблений або застарілий."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TelegramUser(BaseModel):
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class TelegramAuth(BaseModel):
    user: TelegramUser
    start_param: str | None = None


def validate_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age: timedelta,
    clock: Clock,
) -> TelegramAuth:
    if not init_data:
        raise InitDataError("missing")
    try:
        parsed = safe_parse_webapp_init_data(token=bot_token, init_data=init_data, loads=json.loads)
    except ValueError as exc:
        raise InitDataError("bad_signature") from exc

    now = clock.now()
    auth_date = parsed.auth_date
    if auth_date > now + _FUTURE_SKEW:
        raise InitDataError("from_future")
    if now - auth_date > max_age:
        raise InitDataError("expired")
    if parsed.user is None:
        raise InitDataError("no_user")

    u = parsed.user
    return TelegramAuth(
        user=TelegramUser(
            id=u.id,
            first_name=u.first_name or "",
            last_name=u.last_name,
            username=u.username,
            language_code=u.language_code,
        ),
        start_param=parsed.start_param,
    )
