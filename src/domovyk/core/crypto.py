"""HMAC-хеші ідентифікаторів (РНОКПП, телефони) і шифрування реквізитів (Fernet).

Відкритий код тут безпечний: стійкість тримається на секретах із env, а не на алгоритмі.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken

_NON_DIGITS = re.compile(r"\D+")


def normalize_phone(phone: str) -> str:
    """Привести номер до вигляду 380XXXXXXXXX (лише цифри)."""
    digits = _NON_DIGITS.sub("", phone)
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    return digits


def identifier_hash(value: str, secret: str) -> str:
    """HMAC-SHA256 від нормалізованого ідентифікатора, hex. Для банів і пошуку збігів."""
    if not secret:
        raise ValueError("HMAC_SECRET не задано")
    normalized = value.strip().lower()
    return hmac.new(secret.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def token_hash(token: str, secret: str) -> str:
    """HMAC-SHA256 від токена як є (без нормалізації): запрошення, одноразові посилання."""
    if not secret:
        raise ValueError("HMAC_SECRET не задано")
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def phone_hash(phone: str, secret: str) -> str:
    return identifier_hash(normalize_phone(phone), secret)


class DecryptionError(Exception):
    pass


class Cipher:
    """Симетричне шифрування чутливих полів (реквізити оплати)."""

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("FERNET_KEY не задано")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self.decrypt_bytes(token.encode()).decode()

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Для файлів (документи власності у сховищі)."""
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise DecryptionError("Не вдалося розшифрувати значення") from exc
