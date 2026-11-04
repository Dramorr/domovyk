"""Спільний Postgres+PostGIS для інтеграційних тестів (testcontainers). Потрібен Docker."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.core.clock import FixedClock
from domovyk.core.db import make_engine, make_sessionmaker
from domovyk.core.tx import SqlTxManager
from domovyk.modules.analytics.repository import SqlEventsRepository
from domovyk.modules.analytics.service import AnalyticsService
from domovyk.modules.moderation.repository import SqlModerationRepository
from domovyk.modules.moderation.service import ModerationService
from domovyk.modules.users.repository import SqlUsersRepository
from domovyk.modules.users.service import UsersService
from tests.fakes import fake_config


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """URL з драйвером psycopg (синхронний) — для alembic-тестів і перевірок."""
    if shutil.which("docker") is None:
        pytest.skip("Docker недоступний")
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgis/postgis:16-3.5", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def migrated_url(pg_url: str) -> str:
    async_url = pg_url.replace("+psycopg", "+asyncpg")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = async_url
    command.upgrade(cfg, "head")
    return async_url


@pytest.fixture
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(migrated_url)
    yield eng
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE events, audit_log, bans, user_consents, tenant_profiles, "
                "auth_identities, users RESTART IDENTITY CASCADE"
            )
        )
    await eng.dispose()


@pytest.fixture
def users(engine: AsyncEngine) -> UsersService:
    clock = FixedClock(datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    tx = SqlTxManager(make_sessionmaker(engine))
    moderation = ModerationService(SqlModerationRepository, clock)
    analytics = AnalyticsService(SqlEventsRepository)
    return UsersService(tx, SqlUsersRepository, moderation, analytics, clock, fake_config())
