"""Адаптери M2: геокодери, сховище файлів (без мережі)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from domovyk.adapters.geocoder.base import GeocoderError
from domovyk.adapters.geocoder.manual import ManualGeocoder
from domovyk.adapters.geocoder.nominatim import USER_AGENT, NominatimGeocoder
from domovyk.adapters.storage.base import FileNotFoundInStorage
from domovyk.adapters.storage.encrypted import EncryptedFileStorage
from domovyk.adapters.storage.local import LocalFileStorage
from domovyk.core.crypto import Cipher

# Вигадана відповідь у форматі Nominatim jsonv2.
NOMINATIM_ITEMS = [
    {
        "osm_type": "way",
        "osm_id": 1,
        "lat": "49.8397",
        "lon": "24.0297",
        "address": {
            "house_number": "12",
            "road": "вулиця Тараса Шевченка",
            "city_district": "Шевченківський район",
            "city": "Львів",
        },
    },
    {
        "osm_type": "way",
        "osm_id": 2,
        "lat": "49.8",
        "lon": "24.0",
        "address": {"road": "без номера"},
    },
    {
        "osm_type": "node",
        "osm_id": 3,
        "lat": "49.8398",
        "lon": "24.0298",
        "address": {"house_number": "12", "road": "вулиця Тараса Шевченка"},
    },
]


async def test_nominatim_parses_houses_and_respects_policy() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=NOMINATIM_ITEMS)

    geo = NominatimGeocoder(transport=httpx.MockTransport(handler), min_interval=0)
    found = await geo.search("Шевченка 12", city="Львів")
    assert len(found) == 1  # без номера — відкинуто, дубль — відкинуто
    (a,) = found
    assert (a.street, a.house_number, a.district) == (
        "вулиця Тараса Шевченка",
        "12",
        "Шевченківський район",
    )
    assert a.lat == pytest.approx(49.8397)
    req = seen[0]
    assert req.headers["user-agent"] == USER_AGENT
    assert req.url.params["bounded"] == "1" and req.url.params["countrycodes"] == "ua"
    # Повтор — з кешу, без нового запиту.
    await geo.search("шевченка   12", city="Львів")
    assert len(seen) == 1
    await geo.aclose()


async def test_nominatim_errors() -> None:
    geo = NominatimGeocoder(
        transport=httpx.MockTransport(lambda r: httpx.Response(503)), min_interval=0
    )
    with pytest.raises(GeocoderError):
        await geo.search("Шевченка 12", city="Львів")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    geo2 = NominatimGeocoder(transport=httpx.MockTransport(boom), min_interval=0)
    with pytest.raises(GeocoderError):
        await geo2.search("Шевченка 12", city="Львів")


@pytest.mark.parametrize(
    ("query", "street", "house"),
    [
        ("Шевченка 12", "Шевченка", "12"),
        ("вул. Січових Стрільців, 5а", "вул. Січових Стрільців", "5а"),
        ("Зелена, буд. 40/2", "Зелена", "40/2"),
    ],
)
async def test_manual_geocoder(query: str, street: str, house: str) -> None:
    (a,) = await ManualGeocoder().search(query, city="Львів")
    assert (a.street, a.house_number, a.lat) == (street, house, None)


async def test_manual_geocoder_needs_number() -> None:
    assert await ManualGeocoder().search("Шевченка", city="Львів") == []


async def test_local_storage_roundtrip_and_key_safety(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    await storage.put("ownership/abc/doc.pdf", b"data")
    assert await storage.get("ownership/abc/doc.pdf") == b"data"
    await storage.delete("ownership/abc/doc.pdf")
    await storage.delete("ownership/abc/doc.pdf")  # повторне видалення — не помилка
    with pytest.raises(FileNotFoundInStorage):
        await storage.get("ownership/abc/doc.pdf")
    for bad in ("../secret", "/etc/passwd", "a/../../b", "a\\b"):
        with pytest.raises(ValueError):
            await storage.put(bad, b"x")


async def test_encrypted_storage_keeps_ciphertext_on_disk(tmp_path: Path) -> None:
    inner = LocalFileStorage(tmp_path)
    storage = EncryptedFileStorage(inner, Cipher(Fernet.generate_key().decode()))
    await storage.put("ownership/v/d.pdf", b"%PDF-secret")
    assert await storage.get("ownership/v/d.pdf") == b"%PDF-secret"
    raw = (tmp_path / "ownership" / "v" / "d.pdf").read_bytes()
    assert b"secret" not in raw
