"""Підготовка фото (правило 4): Pillow зараз, будь-що інше пізніше."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ImageRejected(Exception):
    """Файл не фото, пошкоджений, завеликий чи замалий. `reason` — стабільний ключ."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PreparedImage:
    data: bytes  # JPEG без EXIF (там бувають GPS-координати й модель телефона)
    width: int
    height: int
    phash: int  # перцептивний хеш, знаковий 64-бітний (для пошуку однакових фото)


class ImageProcessor(Protocol):
    async def prepare(self, data: bytes) -> PreparedImage: ...
