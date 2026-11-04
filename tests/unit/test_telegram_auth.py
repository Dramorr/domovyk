"""FR-TMA-2: перевірка initData."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from domovyk.core.clock import FixedClock
from domovyk.interfaces.api.telegram_auth import InitDataError, validate_init_data
from tests.conftest import FAKE_BOT_TOKEN, make_init_data

MAX_AGE = timedelta(hours=24)


def _validate(init_data: str, clock: FixedClock, token: str = FAKE_BOT_TOKEN):  # type: ignore[no-untyped-def]
    return validate_init_data(init_data, bot_token=token, max_age=MAX_AGE, clock=clock)


def test_valid_init_data(now: datetime, clock: FixedClock) -> None:
    auth = _validate(make_init_data(now - timedelta(minutes=5), start_param="lst_abc"), clock)
    assert auth.user.id == 42
    assert auth.user.first_name == "Тарас"
    assert auth.start_param == "lst_abc"


def test_wrong_bot_token_rejected(now: datetime, clock: FixedClock) -> None:
    with pytest.raises(InitDataError) as exc:
        _validate(make_init_data(now, bot_token="999:other"), clock)
    assert exc.value.reason == "bad_signature"


def test_tampered_user_rejected(now: datetime, clock: FixedClock) -> None:
    data = make_init_data(now).replace("42", "43")
    with pytest.raises(InitDataError):
        _validate(data, clock)


def test_expired_rejected(now: datetime, clock: FixedClock) -> None:
    with pytest.raises(InitDataError) as exc:
        _validate(make_init_data(now - MAX_AGE - timedelta(seconds=1)), clock)
    assert exc.value.reason == "expired"


def test_from_future_rejected(now: datetime, clock: FixedClock) -> None:
    with pytest.raises(InitDataError) as exc:
        _validate(make_init_data(now + timedelta(minutes=10)), clock)
    assert exc.value.reason == "from_future"


def test_empty_rejected(clock: FixedClock) -> None:
    with pytest.raises(InitDataError) as exc:
        _validate("", clock)
    assert exc.value.reason == "missing"
