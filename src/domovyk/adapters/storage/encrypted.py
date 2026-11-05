"""Обгортка над будь-яким сховищем: шифрує вміст (Fernet) перед записом.

Документи власності містять персональні дані — у сховищі вони лежать лише зашифрованими.
"""

from __future__ import annotations

from domovyk.adapters.storage.base import FileStorage
from domovyk.core.crypto import Cipher


class EncryptedFileStorage:
    def __init__(self, inner: FileStorage, cipher: Cipher) -> None:
        self._inner = inner
        self._cipher = cipher

    async def put(self, key: str, data: bytes) -> None:
        await self._inner.put(key, self._cipher.encrypt_bytes(data))

    async def get(self, key: str) -> bytes:
        return self._cipher.decrypt_bytes(await self._inner.get(key))

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)
