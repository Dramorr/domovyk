"""API об'єктів, власності й модерації (M2) через ASGI-транспорт на фейках."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from domovyk.config import Settings
from domovyk.core.clock import FixedClock
from domovyk.interfaces.api.app import create_app
from tests.conftest import make_init_data
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.fakes_properties import PDF

OWNER = 42


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
async def client(settings: Settings, clock: FixedClock, db: FakeDb) -> httpx.AsyncClient:
    services, _ = make_fake_services(db, clock=clock)
    for tg in (ADMIN_TG, OWNER):
        await services.users.register(tg, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await services.users.add_phone(tg, f"+38067{tg:07d}")
    app = create_app(settings, clock=clock, services=services)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def _auth(now: datetime, user_id: int = OWNER) -> dict[str, str]:
    data = make_init_data(now - timedelta(seconds=30), user_id=user_id, first_name="Тарас")
    return {"Authorization": f"tma {data}"}


FLAT = {
    "kind": "apartment",
    "address": {"street": "вул. Шевченка", "house_number": "12"},
    "apartment": "5",
    "rooms": 2,
    "area": "54",
}


async def test_property_lifecycle(client: httpx.AsyncClient, now: datetime) -> None:
    h = _auth(now)
    r = await client.get("/api/v1/addresses", params={"q": "Шевченка 12"}, headers=h)
    assert r.status_code == 200 and r.json()[0]["house_number"] == "12"

    r = await client.post("/api/v1/properties", json=FLAT, headers=h)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["units"][0]["kind"] == "entire"

    r = await client.post("/api/v1/properties", json=FLAT, headers=h)
    assert (r.status_code, r.json()["error"]) == (409, "property_already_yours")

    r = await client.put(
        f"/api/v1/properties/{pid}", json={"rooms": 3, "floor": 4, "entrance": "1"}, headers=h
    )
    assert r.json()["rooms"] == 3

    r = await client.post(f"/api/v1/properties/{pid}/rooms", json={"name": "Мала"}, headers=h)
    assert r.status_code == 201 and len(r.json()["units"]) == 2

    assert len((await client.get("/api/v1/properties", headers=h)).json()) == 1
    r = await client.delete(f"/api/v1/properties/{pid}", headers=h)
    assert r.status_code == 204


async def test_ownership_flow_with_moderation(client: httpx.AsyncClient, now: datetime) -> None:
    h, admin = _auth(now), _auth(now, ADMIN_TG)
    pid = (await client.post("/api/v1/properties", json=FLAT, headers=h)).json()["id"]

    r = await client.post(
        f"/api/v1/properties/{pid}/ownership/documents",
        files={"file": ("довідка.pdf", PDF, "application/pdf")},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["verification"]["status"] == "draft"

    r = await client.post(
        f"/api/v1/properties/{pid}/ownership/documents",
        files={"file": ("photo.jpg", b"\xff\xd8", "image/jpeg")},
        headers=h,
    )
    assert (r.status_code, r.json()) == (422, {"error": "invalid_input", "field": "file"})

    r = await client.post(f"/api/v1/properties/{pid}/ownership/submit", headers=h)
    assert r.json()["ownership_status"] == "pending"

    # Не модератор — без доступу до черги.
    r = await client.get("/api/v1/moderation/ownership", headers=h)
    assert (r.status_code, r.json()["error"]) == (403, "not_staff")

    assert (await client.get("/api/v1/moderation/summary", headers=admin)).json() == {
        "ownership_pending": 1
    }
    queue = (await client.get("/api/v1/moderation/ownership", headers=admin)).json()
    vid = queue[0]["verification_id"]
    assert queue[0]["owner"]["display_name"] == "Тарас"

    r = await client.post(f"/api/v1/moderation/ownership/{vid}/send-documents", headers=admin)
    assert r.json() == {"sent": 1}

    r = await client.post(
        f"/api/v1/moderation/ownership/{vid}/approve", json={"owner_name": "Т"}, headers=admin
    )
    assert (r.status_code, r.json()["field"]) == (422, "owner_name")
    r = await client.post(
        f"/api/v1/moderation/ownership/{vid}/approve",
        json={"owner_name": "Шевченко Тарас"},
        headers=admin,
    )
    assert r.json()["property"]["ownership_status"] == "verified"

    r = await client.get(f"/api/v1/properties/{pid}/ownership", headers=h)
    assert r.json()["ownership_status"] == "verified"


async def test_foreign_property_is_404(client: httpx.AsyncClient, now: datetime) -> None:
    pid = (await client.post("/api/v1/properties", json=FLAT, headers=_auth(now))).json()["id"]
    r = await client.get(f"/api/v1/properties/{pid}", headers=_auth(now, ADMIN_TG))
    assert r.status_code == 404


async def test_dev_verify_disabled_by_default(client: httpx.AsyncClient, now: datetime) -> None:
    pid = (await client.post("/api/v1/properties", json=FLAT, headers=_auth(now))).json()["id"]
    r = await client.post(f"/api/v1/properties/{pid}/ownership/dev-verify", headers=_auth(now))
    assert r.status_code == 403


async def test_profile_endpoint(client: httpx.AsyncClient, now: datetime) -> None:
    h = _auth(now)
    pid = (await client.post("/api/v1/properties", json=FLAT, headers=h)).json()["id"]
    r = await client.put(
        f"/api/v1/properties/{pid}/details",
        json={"heating": "individual_gas", "pets": "cats", "utilities_winter_uah": 2500},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["details"]["heating"] == "individual_gas"
    assert body["completeness"]["level"] == "low"
    assert "heating" not in body["completeness"]["missing_important"]
    r = await client.put(f"/api/v1/properties/{pid}/details", json={"heating": "дрова"}, headers=h)
    assert r.status_code == 422
