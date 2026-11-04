"""Контейнер залежностей: тут обираються реалізації репозиторіїв і адаптерів (правило 4)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.config import Settings
from domovyk.core.clock import Clock, system_clock
from domovyk.core.db import make_engine, make_sessionmaker
from domovyk.core.tx import SqlTxManager
from domovyk.modules.analytics.repository import SqlEventsRepository
from domovyk.modules.analytics.service import AnalyticsService
from domovyk.modules.moderation.repository import SqlModerationRepository
from domovyk.modules.moderation.service import ModerationService
from domovyk.modules.users.repository import SqlUsersRepository
from domovyk.modules.users.service import UsersConfig, UsersService


@dataclass
class Services:
    users: UsersService
    moderation: ModerationService
    analytics: AnalyticsService


def users_config(settings: Settings) -> UsersConfig:
    return UsersConfig(
        hmac_secret=settings.hmac_secret.get_secret_value(),
        admin_telegram_ids=frozenset(settings.admin_telegram_ids),
        terms_version=settings.terms_version,
        privacy_version=settings.privacy_version,
        terms_url=settings.terms_url,
        privacy_url=settings.privacy_url,
    )


class Container:
    """Справжні залежності: PostgreSQL через SQLAlchemy."""

    def __init__(self, settings: Settings, clock: Clock = system_clock) -> None:
        settings.require("hmac_secret")
        self.settings = settings
        self.engine: AsyncEngine = make_engine(settings.database_url)
        tx = SqlTxManager(make_sessionmaker(self.engine))
        analytics = AnalyticsService(SqlEventsRepository)
        moderation = ModerationService(SqlModerationRepository, clock)
        users = UsersService(
            tx, SqlUsersRepository, moderation, analytics, clock, users_config(settings)
        )
        self.services = Services(users=users, moderation=moderation, analytics=analytics)

    async def close(self) -> None:
        await self.engine.dispose()
