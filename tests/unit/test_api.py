from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from domovyk.config import Settings
from domovyk.core.clock import FixedClock
from domovyk.interfaces.api.app import create_app
from tests.conftest import make_init_data
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, FakeDb, make_fake_services


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def client(settings: Settings, clock: FixedClock, db: FakeDb) -> httpx.AsyncClient:
    services, _ = make_fake_services(db)
    app = create_app(settings, clock=clock, services=services)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


def _auth(now: datetime, user_id: int = 42, first_name: str = "Соломія") -> dict[str, str]:
    data = make_init_data(now - timedelta(seconds=30), user_id=user_id, first_name=first_name)
    return {"Authorization": f"tma {data}"}


async def _register(client: httpx.AsyncClient, now: datetime, **kw: object) -> httpx.Response:
    body = {"display_name": "Соломія", "terms_version": TERMS_V, "privacy_version": PRIVACY_V}
    body.update(kw)  # type: ignore[arg-type]
    return await client.post("/api/v1/register", json=body, headers=_auth(now))


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_me_requires_init_data(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_me_rejects_bad_signature(client: httpx.AsyncClient, now: datetime) -> None:
    data = make_init_data(now, bot_token="999:other")
    r = await client.get("/api/v1/me", headers={"Authorization": f"tma {data}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "bad_signature"


async def test_me_before_registration(client: httpx.AsyncClient, now: datetime) -> None:
    r = await client.get("/api/v1/me", headers=_auth(now))
    assert r.status_code == 200
    body = r.json()
    assert body["user"] is None
    assert body["telegram"]["first_name"] == "Соломія"
    assert body["consent"]["terms_version"] == TERMS_V


async def test_register_then_me(client: httpx.AsyncClient, now: datetime) -> None:
    r = await _register(client, now)
    assert r.status_code == 201
    assert r.json()["display_name"] == "Соломія"
    me = (await client.get("/api/v1/me", headers=_auth(now))).json()
    assert me["user"]["role"] == "user"
    assert me["user"]["has_phone"] is False


async def test_register_errors(client: httpx.AsyncClient, now: datetime) -> None:
    r = await _register(client, now, terms_version="old")
    assert (r.status_code, r.json()["error"]) == (409, "consent_outdated")
    r = await _register(client, now, display_name="1")
    assert (r.status_code, r.json()) == (422, {"error": "invalid_input", "field": "display_name"})
    await _register(client, now)
    r = await _register(client, now)
    assert (r.status_code, r.json()["error"]) == (409, "already_registered")


async def test_profile_endpoints(client: httpx.AsyncClient, now: datetime) -> None:
    r = await client.get("/api/v1/me/tenant-profile", headers=_auth(now))
    assert (r.status_code, r.json()["error"]) == (403, "not_registered")
    await _register(client, now)
    r = await client.put(
        "/api/v1/me/tenant-profile",
        json={"about": "Студентка", "household_size": 1, "has_pets": True, "pets_details": "кіт"},
        headers=_auth(now),
    )
    assert r.status_code == 200
    assert (await client.get("/api/v1/me/tenant-profile", headers=_auth(now))).json()[
        "pets_details"
    ] == "кіт"
    r = await client.put(
        "/api/v1/me/tenant-profile", json={"household_size": 50}, headers=_auth(now)
    )
    assert r.status_code == 422
    r = await client.patch("/api/v1/me", json={"display_name": "Соля"}, headers=_auth(now))
    assert r.json()["display_name"] == "Соля"


async def test_delete_account(client: httpx.AsyncClient, now: datetime) -> None:
    await _register(client, now)
    assert (await client.delete("/api/v1/me", headers=_auth(now))).status_code == 204
    me = (await client.get("/api/v1/me", headers=_auth(now))).json()
    assert me["user"] is None


async def test_banned_user_gets_403(client: httpx.AsyncClient, now: datetime, db: FakeDb) -> None:
    await _register(client, now)
    headers = _auth(now, user_id=ADMIN_TG, first_name="Олег")
    body = {"display_name": "Олег", "terms_version": TERMS_V, "privacy_version": PRIVACY_V}
    await client.post("/api/v1/register", json=body, headers=headers)
    services, _ = make_fake_services(db)
    await services.users.ban(ADMIN_TG, 42, "посередник")

    me = (await client.get("/api/v1/me", headers=_auth(now))).json()
    assert me["user"]["status"] == "banned"
    r = await client.get("/api/v1/me/tenant-profile", headers=_auth(now))
    assert (r.status_code, r.json()["error"]) == (403, "banned")


async def test_cors_allows_only_configured_origin(client: httpx.AsyncClient) -> None:
    ok = await client.options(
        "/api/v1/me",
        headers={"Origin": "https://example.test", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "https://example.test"
    bad = await client.options(
        "/api/v1/me",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers
