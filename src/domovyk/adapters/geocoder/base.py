"""Геокодер за інтерфейсом (правило 4). Бізнес-логіка не знає, хто шукає адресу.

Реалізації: `nominatim.py` (OpenStreetMap), `manual.py` (без мережі — для тестів
і як запасний варіант). Вибір — у `container.py` за `GEOCODER` з конфігурації.
Додати Visicom чи Google = новий файл поруч, що реалізує `Geocoder`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GeocodedAddress:
    """Знайдена адреса будинку. Координати й район — якщо геокодер їх знає."""

    city: str
    street: str
    house_number: str
    district: str | None = None
    lat: float | None = None
    lon: float | None = None
    place_id: str | None = None


class GeocoderError(Exception):
    """Геокодер недоступний або відповів з помилкою (не «нічого не знайдено»)."""


class Geocoder(Protocol):
    async def search(self, query: str, *, city: str, limit: int = 5) -> list[GeocodedAddress]:
        """Будинки, що відповідають запиту «вулиця номер» у місті. Порожній список — не знайдено."""
        ...
