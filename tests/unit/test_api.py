from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from domovyk.config import Settings
from domovyk.core.clock import FixedClock
from domovyk.interfaces.api.app import create_app
from tests.conftest import make_init_data


@pytest.fixture
def client(settings: Settings, clock: FixedClock) -> httpx.AsyncClient:
    app = create_app(settings, clock=clock)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_me_requires_init_data(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/me")
    assert r.status_code == 401


async def test_me_rejects_bad_signature(client: httpx.AsyncClient, now: datetime) -> None:
    data = make_init_data(now, bot_token="999:other")
    r = await client.get("/api/v1/me", headers={"Authorization": f"tma {data}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "bad_signature"


async def test_me_returns_user(client: httpx.AsyncClient, now: datetime) -> None:
    data = make_init_data(now - timedelta(seconds=30), first_name="Соломія")
    r = await client.get("/api/v1/me", headers={"Authorization": f"tma {data}"})
    assert r.status_code == 200
    assert r.json()["first_name"] == "Соломія"
    assert r.json()["telegram_id"] == 42


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
