"""Ідентифікатори: UUID у БД, base62 — у callback_data та посиланнях."""

from __future__ import annotations

import secrets
import uuid

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_BASE = len(_ALPHABET)
_UUID_B62_LEN = 22


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def uuid_to_b62(value: uuid.UUID) -> str:
    n = value.int
    out = []
    while n:
        n, rem = divmod(n, _BASE)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out)).rjust(_UUID_B62_LEN, "0")


def b62_to_uuid(text: str) -> uuid.UUID:
    if not text or len(text) > _UUID_B62_LEN:
        raise ValueError("Некоректний base62-ідентифікатор")
    n = 0
    for ch in text:
        idx = _ALPHABET.find(ch)
        if idx < 0:
            raise ValueError("Некоректний base62-ідентифікатор")
        n = n * _BASE + idx
    if n >= 1 << 128:
        raise ValueError("Некоректний base62-ідентифікатор")
    return uuid.UUID(int=n)


def new_token(nbytes: int = 24) -> str:
    """Випадковий токен для посилань-запрошень (у БД зберігаємо лише його хеш)."""
    return secrets.token_urlsafe(nbytes)
