"""Геокодер без мережі: розбирає «вулиця номер» сам, без координат і району.

Для тестів і для запуску без доступу до Nominatim (`GEOCODER=manual`).
"""

from __future__ import annotations

import re

from domovyk.adapters.geocoder.base import GeocodedAddress

# «Шевченка 12», «вул. Шевченка, 12а», «Шевченка, буд. 5/1»
_QUERY_RE = re.compile(
    r"^(?P<street>.+?)[,\s]+(?:буд(?:инок)?\.?\s*)?(?P<house>\d{1,4}\s*[а-яіїєґa-z]?(?:/\d{1,4}[а-яіїєґa-z]?)?)$",
    re.IGNORECASE,
)


class ManualGeocoder:
    async def search(self, query: str, *, city: str, limit: int = 5) -> list[GeocodedAddress]:
        match = _QUERY_RE.match(" ".join(query.split()).strip(" ,"))
        if not match:
            return []
        street = match["street"].strip(" ,")
        if len(street) < 2:
            return []
        return [GeocodedAddress(city=city, street=street, house_number=match["house"])]
