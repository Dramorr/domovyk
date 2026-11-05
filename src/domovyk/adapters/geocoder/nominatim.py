"""Nominatim (OpenStreetMap): безкоштовний геокодер без ключа.

Правила публічного сервера (operations.osmfoundation.org/policies/nominatim):
не більше 1 запиту на секунду, зрозумілий User-Agent, кешувати повтори.
Для продакшну з навантаженням — власний Nominatim або інший геокодер (просто
інша реалізація `Geocoder`).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx

from domovyk.adapters.geocoder.base import GeocodedAddress, GeocoderError

DEFAULT_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "Domovyk/0.1 (+https://github.com/Dramorr/domovyk)"
# Прямокутник навколо Львова (lon_min, lat_max, lon_max, lat_min) — шукаємо лише тут.
LVIV_VIEWBOX = "23.90,49.92,24.15,49.76"
_ROAD_KEYS = ("road", "pedestrian", "square", "footway")
_DISTRICT_KEYS = ("city_district", "borough", "suburb")


class NominatimGeocoder:
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        *,
        min_interval: float = 1.0,
        timeout: float = 10.0,
        cache_size: int = 256,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "uk"},
            timeout=timeout,
            transport=transport,
        )
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._cache: OrderedDict[tuple[str, str, int], list[GeocodedAddress]] = OrderedDict()
        self._cache_size = cache_size

    async def search(self, query: str, *, city: str, limit: int = 5) -> list[GeocodedAddress]:
        key = (" ".join(query.lower().split()), city.lower(), limit)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        params: dict[str, str | int] = {
            "q": f"{query}, {city}",
            "format": "jsonv2",
            "addressdetails": 1,
            "countrycodes": "ua",
            "limit": max(limit * 2, 10),  # частина результатів — не будинки, їх відсіємо
            "viewbox": LVIV_VIEWBOX,
            "bounded": 1,
        }
        async with self._lock:  # не частіше за min_interval — вимога публічного сервера
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._client.get("/search", params=params)
            except httpx.HTTPError as exc:
                raise GeocoderError(f"Nominatim недоступний: {exc.__class__.__name__}") from exc
            finally:
                self._last_call = time.monotonic()
        if response.status_code != httpx.codes.OK:
            raise GeocoderError(f"Nominatim відповів {response.status_code}")
        try:
            items = response.json()
        except ValueError as exc:
            raise GeocoderError("Nominatim повернув не JSON") from exc
        result = _parse(items, city=city, limit=limit)
        self._cache[key] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse(items: Any, *, city: str, limit: int) -> list[GeocodedAddress]:
    if not isinstance(items, list):
        return []
    found: list[GeocodedAddress] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address") or {}
        house = address.get("house_number")
        street = next((address[k] for k in _ROAD_KEYS if address.get(k)), None)
        if not house or not street:
            continue  # не будинок (вулиця, район, POI без номера)
        if (street, house) in seen:
            continue
        seen.add((street, house))
        district = next((address[k] for k in _DISTRICT_KEYS if address.get(k)), None)
        lat: float | None
        lon: float | None
        try:
            lat, lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            lat = lon = None
        found.append(
            GeocodedAddress(
                city=city,
                street=str(street),
                house_number=str(house),
                district=str(district) if district else None,
                lat=lat,
                lon=lon,
                place_id=f"osm:{item.get('osm_type', '')}:{item.get('osm_id', '')}",
            )
        )
        if len(found) >= limit:
            break
    return found
