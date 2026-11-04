from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import pytest
from cryptography.fernet import Fernet

from domovyk.core.clock import FixedClock, to_kyiv
from domovyk.core.crypto import Cipher, DecryptionError, normalize_phone, phone_hash
from domovyk.core.errors import InvalidTransition
from domovyk.core.ids import b62_to_uuid, uuid_to_b62
from domovyk.core.logging import PiiMaskingFilter, mask_pii
from domovyk.domain.state_machine import TransitionTable

SECRET = "test-secret"


def test_phone_hash_is_stable_across_formats() -> None:
    assert normalize_phone("+38 (067) 000-00-01") == "380670000001"
    assert phone_hash("0670000001", SECRET) == phone_hash("+380 67 000 00 01", SECRET)
    assert phone_hash("0670000001", SECRET) != phone_hash("0670000001", "other")


def test_phone_hash_requires_secret() -> None:
    with pytest.raises(ValueError):
        phone_hash("0670000001", "")


def test_cipher_roundtrip_and_wrong_key() -> None:
    key = Fernet.generate_key().decode()
    token = Cipher(key).encrypt("UA00 0000 0000 0000 0000 0000 000")
    assert Cipher(key).decrypt(token).startswith("UA00")
    with pytest.raises(DecryptionError):
        Cipher(Fernet.generate_key().decode()).decrypt(token)


def test_b62_roundtrip() -> None:
    for value in (uuid.uuid4(), uuid.UUID(int=0), uuid.UUID(int=(1 << 128) - 1)):
        encoded = uuid_to_b62(value)
        assert len(encoded) == 22
        assert b62_to_uuid(encoded) == value
    with pytest.raises(ValueError):
        b62_to_uuid("not/valid")


def test_fixed_clock_and_kyiv() -> None:
    clock = FixedClock(datetime(2026, 11, 4, 10, 0, tzinfo=UTC))
    clock.advance(timedelta(hours=1))
    assert clock.now().hour == 11
    assert to_kyiv(clock.now()).hour == 13  # листопад — UTC+2


def test_fixed_clock_rejects_naive() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 4))


@pytest.mark.parametrize(
    ("raw", "hidden"),
    [
        ("дзвони +380671234567", "671234567"),
        ("тел 067 123 45 67", "067 123 45"),
        ("картка 1234 5678 9012 3456", "1234 5678"),
        ("IBAN UA123000000000000000000000001", "3000000000000000000000"),
    ],
)
def test_mask_pii(raw: str, hidden: str) -> None:
    assert hidden not in mask_pii(raw)


def test_logging_filter_masks(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.pii")
    logger.addFilter(PiiMaskingFilter())
    with caplog.at_level(logging.INFO, logger="test.pii"):
        logger.info("номер %s", "+380671234567")
    assert "671234567" not in caplog.text


class _S(StrEnum):
    A = "a"
    B = "b"
    C = "c"


def test_transition_table() -> None:
    table = TransitionTable("demo", {_S.A: frozenset({_S.B}), _S.B: frozenset({_S.C})})
    assert table.transition(_S.A, _S.B) is _S.B
    with pytest.raises(InvalidTransition):
        table.transition(_S.A, _S.C)
