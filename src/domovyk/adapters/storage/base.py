"""Сховище файлів за інтерфейсом (правило 4): папка зараз, S3-сумісне пізніше."""

from __future__ import annotations

from typing import Protocol


class FileNotFoundInStorage(Exception):
    pass


class FileStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None:
        """Видалити, якщо є. Відсутній файл — не помилка."""
        ...
