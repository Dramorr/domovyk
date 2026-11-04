"""Спільні фікстури. Лише вигадані дані — репозиторій публічний."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from domovyk.config import Settings
from domovyk.core.clock import FixedClock

FAKE_BOT_TOKEN = "123456:TEST-not-a-real-token"


def sign_init_data(fields: dict[str, str], bot_token: str = FAKE_BOT_TOKEN) -> str:
    """Підписати initData так, як це робить Telegram (для тестів)."""
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def make_init_data(
    auth_date: datetime,
    *,
    user_id: int = 42,
    first_name: str = "Тарас",
    start_param: str | None = None,
    bot_token: str = FAKE_BOT_TOKEN,
) -> str:
    user = {"id": user_id, "first_name": first_name, "language_code": "uk"}
    fields = {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": "AAE-test",
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    if start_param:
        fields["start_param"] = start_param
    return sign_init_data(fields, bot_token)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock(now: datetime) -> FixedClock:
    return FixedClock(now)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        telegram_bot_token=SecretStr(FAKE_BOT_TOKEN),
        cors_origins=["https://example.test"],
    )
