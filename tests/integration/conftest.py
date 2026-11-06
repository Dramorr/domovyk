"""Спільний Postgres+PostGIS для інтеграційних тестів (testcontainers). Потрібен Docker."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.adapters.geocoder.manual import ManualGeocoder
from domovyk.container import Services, wire_guards
from domovyk.core.clock import FixedClock
from domovyk.core.crypto import Cipher
from domovyk.core.db import make_engine, make_sessionmaker
from domovyk.core.tx import SqlTxManager
from domovyk.modules.analytics.repository import SqlEventsRepository
from domovyk.modules.analytics.service import AnalyticsService
from domovyk.modules.chat.repository import SqlChatRepository
from domovyk.modules.chat.service import ChatService
from domovyk.modules.leases.repository import SqlLeasesRepository
from domovyk.modules.leases.service import LeasesConfig, LeasesService
from domovyk.modules.moderation.repository import SqlModerationRepository
from domovyk.modules.moderation.service import ModerationService
from domovyk.modules.notifications.repository import SqlNotificationsRepository
from domovyk.modules.notifications.service import NotificationsService
from domovyk.modules.properties.repository import SqlPropertiesRepository
from domovyk.modules.properties.service import PropertiesConfig, PropertiesService
from domovyk.modules.users.repository import SqlUsersRepository
from domovyk.modules.users.service import UsersService
from tests.fakes import fake_config
from tests.fakes_properties import FakeNotifier, MemoryStorage


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
                "TRUNCATE notification_preferences, conversation_reads, messages, "
                "conversations, lease_invites, lease_members, leases, "
                "notifications, risk_signals, verification_documents, "
                "user_verifications, units, properties, buildings, districts, "
                "events, audit_log, bans, user_consents, tenant_profiles, "
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


@pytest.fixture
def it_clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 5, 12, 0, tzinfo=UTC))


@pytest.fixture
def services(engine: AsyncEngine, it_clock: FixedClock) -> Services:
    """Усі сервіси на справжньому Postgres; зовнішнє (сховище, Telegram) — фейки."""
    tx = SqlTxManager(make_sessionmaker(engine))
    moderation = ModerationService(SqlModerationRepository, it_clock)
    analytics = AnalyticsService(SqlEventsRepository)
    users = UsersService(tx, SqlUsersRepository, moderation, analytics, it_clock, fake_config())
    notifications = NotificationsService(tx, SqlNotificationsRepository, users, it_clock)
    properties = PropertiesService(
        tx,
        SqlPropertiesRepository,
        users,
        moderation,
        analytics,
        notifications,
        ManualGeocoder(),
        MemoryStorage(),
        it_clock,
        PropertiesConfig(),
        notifier=FakeNotifier(),
    )
    chat = ChatService(SqlChatRepository)
    leases = LeasesService(
        tx,
        SqlLeasesRepository,
        users,
        properties,
        chat,
        notifications,
        Cipher(Fernet.generate_key().decode()),
        it_clock,
        LeasesConfig(hmac_secret="test-hmac-secret"),
    )
    wire_guards(users, properties, leases)
    return Services(
        users=users,
        moderation=moderation,
        analytics=analytics,
        notifications=notifications,
        properties=properties,
        chat=chat,
        leases=leases,
    )
