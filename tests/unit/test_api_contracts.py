"""API договорів і модерації підписів (M4) через ASGI-транспорт на фейках."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from domovyk.config import Settings
from domovyk.core.clock import FixedClock
from domovyk.domain.properties import OwnershipStatus
from domovyk.interfaces.api.app import create_app
from tests.conftest import make_init_data
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.signing_fixtures import cades

OWNER, TENANT = 42, 43


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
async def client(settings: Settings, clock: FixedClock, db: FakeDb) -> httpx.AsyncClient:
    services, _ = make_fake_services(db, clock=clock)
    for tg in (ADMIN_TG, OWNER, TENANT):
        await services.users.register(tg, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await services.users.add_phone(tg, f"+38067{tg:07d}")
    app = create_app(settings, clock=clock, services=services)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def _auth(now: datetime, user_id: int) -> dict[str, str]:
    data = make_init_data(now - timedelta(seconds=30), user_id=user_id, first_name="Тарас")
    return {"Authorization": f"tma {data}"}


async def test_contract_flow(client: httpx.AsyncClient, db: FakeDb, now: datetime) -> None:
    owner, tenant, admin = _auth(now, OWNER), _auth(now, TENANT), _auth(now, ADMIN_TG)
    flat = {"kind": "apartment", "address": {"street": "вул. Шевченка", "house_number": "12"}}
    r = await client.post(
        "/api/v1/properties", json={**flat, "apartment": "5", "rooms": 2}, headers=owner
    )
    pid = r.json()["id"]
    db.prop.properties[next(iter(db.prop.properties))].ownership = OwnershipStatus.VERIFIED
    r = await client.post(
        "/api/v1/leases",
        json={
            "property_id": pid,
            "start_date": "2026-12-01",
            "months": 11,
            "rent_amount": 15000,
            "rent_due_day": 1,
            "pay_method": "cash",
        },
        headers=owner,
    )
    assert r.status_code == 201, r.text
    lease_id, token = r.json()["lease"]["id"], r.json()["invite"]["token"]
    await client.post(f"/api/v1/invites/{token}/accept", headers=tenant)
    await client.post(f"/api/v1/leases/{lease_id}/accept-terms", headers=tenant)

    r = await client.post(f"/api/v1/leases/{lease_id}/contract/generate", headers=owner)
    assert (r.status_code, r.json()["field"]) == (422, "full_name")
    r = await client.put(
        f"/api/v1/leases/{lease_id}/contract/name", json={"full_name": "Іван"}, headers=owner
    )
    assert r.status_code == 422
    for h, name in ((owner, "Коваль Ірина Петрівна"), (tenant, "Петренко Андрій")):
        r = await client.put(
            f"/api/v1/leases/{lease_id}/contract/name", json={"full_name": name}, headers=h
        )
        assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/leases/{lease_id}/contract/generate", headers=owner)
    contract_id = r.json()["contract"]["id"]

    r = await client.get(f"/api/v1/contracts/{contract_id}/pdf", headers=tenant)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    pdf = r.content
    r = await client.post(
        f"/api/v1/contracts/{contract_id}/signature",
        files={"file": ("x.p7s", b"garbage", "application/octet-stream")},
        headers=tenant,
    )
    assert (r.status_code, r.json()["reason"]) == (422, "unreadable")
    for h, name in ((tenant, "Петренко Андрій"), (owner, "Коваль Ірина Петрівна")):
        r = await client.post(
            f"/api/v1/contracts/{contract_id}/signature",
            files={"file": ("s.p7s", cades(pdf, name), "application/octet-stream")},
            headers=h,
        )
        assert r.status_code == 200, r.text

    r = await client.get("/api/v1/moderation/summary", headers=admin)
    assert r.json()["signatures_pending"] == 2
    assert (await client.get("/api/v1/moderation/signatures", headers=tenant)).status_code == 403
    for case in (await client.get("/api/v1/moderation/signatures", headers=admin)).json():
        r = await client.post(
            f"/api/v1/moderation/signatures/{case['signature_id']}/review",
            json={"approve": True},
            headers=admin,
        )
        assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/leases/{lease_id}", headers=owner)).json()[
        "status"
    ] == "active"
    r = await client.get(f"/api/v1/contracts/{contract_id}/package", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    r = await client.post(
        f"/api/v1/contracts/{contract_id}/send", json={"what": "package"}, headers=owner
    )
    assert r.json()["sent"] == 1


async def test_unexpected_error_is_json_with_cors(
    settings: Settings, clock: FixedClock, db: FakeDb, now: datetime
) -> None:
    """Несподівана помилка → 500 JSON з CORS: інакше Mini App пише «немає зв'язку»."""
    services, _ = make_fake_services(db, clock=clock)
    await services.users.register(OWNER, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)

    async def boom(*_: object) -> None:
        raise RuntimeError("тест")

    services.properties.list_mine = boom  # type: ignore[method-assign]
    app = create_app(settings, clock=clock, services=services)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")
    origin = settings.cors_origins[0]
    r = await client.get("/api/v1/properties", headers={**_auth(now, OWNER), "Origin": origin})
    assert (r.status_code, r.json()) == (500, {"error": "internal"})
    assert r.headers.get("access-control-allow-origin") == origin
