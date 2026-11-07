"""Контейнер залежностей: тут обираються реалізації репозиторіїв і адаптерів (правило 4)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.adapters.geocoder.base import Geocoder
from domovyk.adapters.geocoder.manual import ManualGeocoder
from domovyk.adapters.geocoder.nominatim import NominatimGeocoder
from domovyk.adapters.notifier.telegram import TelegramNotifier
from domovyk.adapters.pdf.weasy import WeasyPrintRenderer
from domovyk.adapters.signing.diia_portal import DiiaPortalVerifier
from domovyk.adapters.storage.encrypted import EncryptedFileStorage
from domovyk.adapters.storage.local import LocalFileStorage
from domovyk.config import Settings
from domovyk.core.clock import Clock, system_clock
from domovyk.core.crypto import Cipher
from domovyk.core.db import make_engine, make_sessionmaker
from domovyk.core.tx import SqlTxManager
from domovyk.modules.analytics.repository import SqlEventsRepository
from domovyk.modules.analytics.service import AnalyticsService
from domovyk.modules.chat.repository import SqlChatRepository
from domovyk.modules.chat.service import ChatService
from domovyk.modules.contracts.repository import SqlContractsRepository
from domovyk.modules.contracts.service import ContractsConfig, ContractsService
from domovyk.modules.leases.repository import SqlLeasesRepository
from domovyk.modules.leases.service import LeasesConfig, LeasesService
from domovyk.modules.moderation.repository import SqlModerationRepository
from domovyk.modules.moderation.service import ModerationService
from domovyk.modules.notifications.repository import SqlNotificationsRepository
from domovyk.modules.notifications.service import NotificationsService
from domovyk.modules.payments.repository import SqlPaymentsRepository
from domovyk.modules.payments.service import PaymentsConfig, PaymentsService
from domovyk.modules.properties.repository import SqlPropertiesRepository
from domovyk.modules.properties.service import PropertiesConfig, PropertiesService
from domovyk.modules.users.repository import SqlUsersRepository
from domovyk.modules.users.service import UsersConfig, UsersService


@dataclass
class Services:
    users: UsersService
    moderation: ModerationService
    analytics: AnalyticsService
    notifications: NotificationsService
    properties: PropertiesService
    chat: ChatService
    leases: LeasesService
    contracts: ContractsService
    payments: PaymentsService


def users_config(settings: Settings) -> UsersConfig:
    return UsersConfig(
        hmac_secret=settings.hmac_secret.get_secret_value(),
        admin_telegram_ids=frozenset(settings.admin_telegram_ids),
        terms_version=settings.terms_version,
        privacy_version=settings.privacy_version,
        terms_url=settings.terms_url,
        privacy_url=settings.privacy_url,
    )


def properties_config(settings: Settings) -> PropertiesConfig:
    return PropertiesConfig(
        dev_skip_ownership_check=settings.dev_skip_ownership_check and settings.is_dev,
        allow_self_review=settings.is_dev,
        max_document_bytes=settings.ownership_doc_max_mb * 1024 * 1024,
        document_retention_days=settings.ownership_doc_retention_days,
        completeness_medium_from=settings.profile_medium_from,
        completeness_high_from=settings.profile_high_from,
        listing_min_completeness=settings.listing_min_completeness,
        default_city=settings.default_city,
    )


def contracts_config(settings: Settings) -> ContractsConfig:
    return ContractsConfig(
        hmac_secret=settings.hmac_secret.get_secret_value(),
        require_moderator=settings.contracts_require_moderator,
        allow_self_review=settings.is_dev,
        diia_sign_url=settings.diia_sign_url,
    )


def wire_contracts(leases: LeasesService, contracts: ContractsService) -> None:
    """Повне видалення скасованої оренди прибирає й файли договорів (FR-LSE-9)."""
    leases.set_purge_hook(contracts.delete_files_for_lease)


def wire_guards(users: UsersService, properties: PropertiesService, leases: LeasesService) -> None:
    """Заборони видаляти акаунт і об'єкт з живою орендою — без циклічних імпортів модулів."""
    users.set_delete_guard(leases.has_open_leases)
    properties.set_delete_guard(leases.has_open_lease_on_property)


def make_geocoder(settings: Settings) -> Geocoder:
    if settings.geocoder == "manual":
        return ManualGeocoder()
    return NominatimGeocoder(settings.nominatim_url)


class Container:
    """Справжні залежності: PostgreSQL, геокодер, сховище файлів, Telegram."""

    def __init__(self, settings: Settings, clock: Clock = system_clock) -> None:
        settings.require("hmac_secret", "fernet_key")
        self.settings = settings
        self.engine: AsyncEngine = make_engine(settings.database_url)
        tx = SqlTxManager(make_sessionmaker(self.engine))
        self.geocoder = make_geocoder(settings)
        cipher = Cipher(settings.fernet_key.get_secret_value())
        storage = EncryptedFileStorage(LocalFileStorage(settings.storage_dir), cipher)
        token = settings.telegram_bot_token.get_secret_value()
        self.notifier = TelegramNotifier.from_token(token) if token else None

        analytics = AnalyticsService(SqlEventsRepository)
        moderation = ModerationService(SqlModerationRepository, clock)
        users = UsersService(
            tx, SqlUsersRepository, moderation, analytics, clock, users_config(settings)
        )
        notifications = NotificationsService(tx, SqlNotificationsRepository, users, clock)
        properties = PropertiesService(
            tx,
            SqlPropertiesRepository,
            users,
            moderation,
            analytics,
            notifications,
            self.geocoder,
            storage,
            clock,
            properties_config(settings),
            notifier=self.notifier,
        )
        chat = ChatService(SqlChatRepository)
        leases = LeasesService(
            tx,
            SqlLeasesRepository,
            users,
            properties,
            chat,
            notifications,
            cipher,
            clock,
            LeasesConfig(
                hmac_secret=settings.hmac_secret.get_secret_value(),
                tenant_max_open_leases=settings.tenant_max_open_leases,
                cancelled_retention_days=settings.lease_cancelled_retention_days,
            ),
        )
        contracts = ContractsService(
            tx,
            SqlContractsRepository,
            users,
            properties,
            leases,
            chat,
            notifications,
            moderation,
            storage,
            WeasyPrintRenderer(settings.weasyprint_dll_directories),
            DiiaPortalVerifier(),
            clock,
            contracts_config(settings),
            notifier=self.notifier,
        )
        payments = PaymentsService(
            tx,
            SqlPaymentsRepository,
            users,
            leases,
            chat,
            notifications,
            storage,
            clock,
            PaymentsConfig(),
            notifier=self.notifier,
        )
        wire_guards(users, properties, leases)
        wire_contracts(leases, contracts)
        self.services = Services(
            users=users,
            moderation=moderation,
            analytics=analytics,
            notifications=notifications,
            properties=properties,
            chat=chat,
            leases=leases,
            contracts=contracts,
            payments=payments,
        )

    async def close(self) -> None:
        if isinstance(self.geocoder, NominatimGeocoder):
            await self.geocoder.aclose()
        if self.notifier is not None:
            await self.notifier.close()
        await self.engine.dispose()
