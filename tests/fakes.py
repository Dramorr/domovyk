"""Фейкові репозиторії в пам'яті для юніт-тестів сервісів. Лише вигадані дані."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet

from domovyk.adapters.geocoder.base import Geocoder
from domovyk.adapters.geocoder.manual import ManualGeocoder
from domovyk.adapters.images.pillow import PillowImageProcessor
from domovyk.adapters.receipts.pdf import PdfReceiptReader
from domovyk.adapters.signing.diia_portal import DiiaPortalVerifier
from domovyk.container import Services, wire_contracts, wire_guards
from domovyk.core.clock import Clock, FixedClock
from domovyk.core.crypto import Cipher
from domovyk.core.tx import NullTxManager, Tx
from domovyk.domain.users import (
    AppMode,
    ConsentDocument,
    IdentityProvider,
    UserRole,
    UserStatus,
)
from domovyk.modules.analytics.service import AnalyticsService
from domovyk.modules.chat.service import ChatService
from domovyk.modules.contracts.service import ContractsConfig, ContractsService
from domovyk.modules.leases.service import LeasesConfig, LeasesService
from domovyk.modules.moderation.schemas import BanIdentifiers, BanOut
from domovyk.modules.moderation.service import ModerationService
from domovyk.modules.notifications.service import NotificationsService
from domovyk.modules.payments.service import PaymentsConfig, PaymentsService
from domovyk.modules.properties.service import PropertiesConfig, PropertiesService
from domovyk.modules.users.schemas import PersonalIn, TenantProfile, UserOut
from domovyk.modules.users.service import UsersConfig, UsersService
from tests.fakes_contracts import ContractDb, FakeContractsRepository, FakePdfRenderer
from tests.fakes_leases import FakeChatRepository, FakeLeasesRepository, LeaseDb
from tests.fakes_payments import FakePaymentsRepository, PaymentDb
from tests.fakes_properties import (
    FakeNotificationsRepository,
    FakeNotifier,
    FakePropertiesRepository,
    MemoryStorage,
    PropDb,
)

TEST_HMAC = "test-hmac-secret"
ADMIN_TG = 1000
TERMS_V = "t-1"
PRIVACY_V = "p-1"


@dataclass
class _User:
    id: uuid.UUID
    display_name: str
    role: UserRole
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime(2026, 11, 4, tzinfo=UTC))
    deleted_at: datetime | None = None
    mode: AppMode | None = None
    personal: PersonalIn = field(default_factory=PersonalIn)
    avatar_key: str | None = None
    avatar_at: datetime | None = None


@dataclass
class _Ban:
    id: uuid.UUID
    ids: BanIdentifiers
    user_id: uuid.UUID | None
    reason: str
    created_by: uuid.UUID | None
    created_at: datetime
    lifted_at: datetime | None = None


class FakeDb:
    def __init__(self) -> None:
        self.users: dict[uuid.UUID, _User] = {}
        self.identities: dict[tuple[IdentityProvider, str], uuid.UUID] = {}
        self.consents: list[tuple[uuid.UUID, ConsentDocument, str]] = []
        self.profiles: dict[uuid.UUID, TenantProfile] = {}
        self.bans: list[_Ban] = []
        self.audit: list[dict[str, Any]] = []
        self.events: list[tuple[uuid.UUID | None, str, str]] = []
        self.risk_signals: list[tuple[uuid.UUID, str, dict[str, Any]]] = []
        self.prop = PropDb()
        self.lease = LeaseDb()
        self.contract = ContractDb()
        self.payment = PaymentDb()
        self.pdf = FakePdfRenderer()
        self.cipher = Cipher(Fernet.generate_key().decode())
        self.storage = MemoryStorage()
        self.notifier = FakeNotifier()


class FakeUsersRepository:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def _out(self, u: _User) -> UserOut:
        tg = self._uid(u.id, IdentityProvider.TELEGRAM)
        return UserOut(
            id=u.id,
            display_name=u.display_name,
            role=u.role,
            status=u.status,
            telegram_uid=int(tg) if tg else None,
            has_phone=self._uid(u.id, IdentityProvider.PHONE) is not None,
            created_at=u.created_at,
            mode=u.mode,
            avatar_version=str(int(u.avatar_at.timestamp()))
            if u.avatar_key and u.avatar_at
            else None,
        )

    async def get_personal(self, user_id: uuid.UUID) -> PersonalIn:
        return self.db.users[user_id].personal.model_copy()

    async def set_personal(self, user_id: uuid.UUID, data: PersonalIn) -> None:
        self.db.users[user_id].personal = data.model_copy()

    async def avatar_key(self, user_id: uuid.UUID) -> str | None:
        u = self.db.users.get(user_id)
        return u.avatar_key if u else None

    async def set_avatar(self, user_id: uuid.UUID, key: str | None, at: datetime | None) -> None:
        u = self.db.users[user_id]
        u.avatar_key, u.avatar_at = key, at

    def _uid(self, user_id: uuid.UUID, provider: IdentityProvider) -> str | None:
        for (p, uid), owner in self.db.identities.items():
            if p is provider and owner == user_id:
                return uid
        return None

    async def get(self, user_id: uuid.UUID) -> UserOut | None:
        u = self.db.users.get(user_id)
        return self._out(u) if u else None

    async def get_by_identity(self, provider: IdentityProvider, uid: str) -> UserOut | None:
        owner = self.db.identities.get((provider, uid))
        return await self.get(owner) if owner else None

    async def create(self, display_name: str, role: UserRole) -> uuid.UUID:
        user = _User(id=uuid.uuid4(), display_name=display_name, role=role)
        self.db.users[user.id] = user
        return user.id

    async def add_identity(
        self,
        user_id: uuid.UUID,
        provider: IdentityProvider,
        uid: str,
        *,
        is_contact: bool,
        verified_at: datetime | None,
    ) -> None:
        if (provider, uid) in self.db.identities:
            raise AssertionError("unique (provider, provider_uid) порушено")
        self.db.identities[(provider, uid)] = user_id

    async def identity_uid(self, user_id: uuid.UUID, provider: IdentityProvider) -> str | None:
        return self._uid(user_id, provider)

    async def remove_identities(
        self, user_id: uuid.UUID, providers: list[IdentityProvider] | None = None
    ) -> None:
        for key, owner in list(self.db.identities.items()):
            if owner == user_id and (providers is None or key[0] in providers):
                del self.db.identities[key]

    async def set_role(self, user_id: uuid.UUID, role: UserRole) -> None:
        self.db.users[user_id].role = role

    async def set_status(
        self, user_id: uuid.UUID, status: UserStatus, *, deleted_at: datetime | None = None
    ) -> None:
        self.db.users[user_id].status = status
        if deleted_at:
            self.db.users[user_id].deleted_at = deleted_at

    async def set_display_name(self, user_id: uuid.UUID, name: str) -> None:
        self.db.users[user_id].display_name = name

    async def set_mode(self, user_id: uuid.UUID, mode: AppMode) -> None:
        self.db.users[user_id].mode = mode

    async def add_consent(
        self, user_id: uuid.UUID, document: ConsentDocument, version: str, at: datetime
    ) -> None:
        self.db.consents.append((user_id, document, version))

    async def get_tenant_profile(self, user_id: uuid.UUID) -> TenantProfile | None:
        return self.db.profiles.get(user_id)

    async def upsert_tenant_profile(self, user_id: uuid.UUID, profile: TenantProfile) -> None:
        self.db.profiles[user_id] = profile

    async def delete_tenant_profile(self, user_id: uuid.UUID) -> None:
        self.db.profiles.pop(user_id, None)

    async def list_staff_ids(self) -> list[uuid.UUID]:
        return [
            u.id
            for u in self.db.users.values()
            if u.role in (UserRole.MODERATOR, UserRole.ADMIN) and u.status is UserStatus.ACTIVE
        ]


class FakeModerationRepository:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    @staticmethod
    def _hit(ban: _Ban, ids: BanIdentifiers) -> bool:
        return any(v is not None and v == getattr(ban.ids, k) for k, v in ids.model_dump().items())

    async def find_active_ban(self, ids: BanIdentifiers, now: datetime) -> BanOut | None:
        for ban in self.db.bans:
            if ban.lifted_at is None and self._hit(ban, ids):
                return BanOut(
                    id=ban.id, user_id=ban.user_id, reason=ban.reason, created_at=ban.created_at
                )
        return None

    async def add_ban(
        self,
        ids: BanIdentifiers,
        *,
        user_id: uuid.UUID | None,
        reason: str,
        created_by: uuid.UUID | None,
    ) -> uuid.UUID:
        ban = _Ban(uuid.uuid4(), ids.model_copy(), user_id, reason, created_by, datetime.now(UTC))
        self.db.bans.append(ban)
        return ban.id

    async def lift_bans(self, ids: BanIdentifiers, now: datetime) -> int:
        count = 0
        for ban in self.db.bans:
            if ban.lifted_at is None and self._hit(ban, ids):
                ban.lifted_at = now
                count += 1
        return count

    async def add_audit(
        self,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any],
    ) -> None:
        self.db.audit.append(
            {"actor_id": actor_id, "action": action, "entity_id": entity_id, "details": details}
        )

    async def add_risk_signal(
        self, user_id: uuid.UUID, signal_type: str, weight: int, details: dict[str, Any]
    ) -> None:
        self.db.risk_signals.append((user_id, signal_type, details))

    async def count_risk_signals(self, user_id: uuid.UUID) -> int:
        return sum(1 for uid, _, _ in self.db.risk_signals if uid == user_id)


class FakeEventsRepository:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def add(
        self, user_id: uuid.UUID | None, name: str, props: dict[str, Any], source: str
    ) -> None:
        self.db.events.append((user_id, name, source))


def fake_config(**overrides: Any) -> UsersConfig:
    values: dict[str, Any] = {
        "hmac_secret": TEST_HMAC,
        "admin_telegram_ids": frozenset({ADMIN_TG}),
        "terms_version": TERMS_V,
        "privacy_version": PRIVACY_V,
        "terms_url": "https://example.test/terms",
        "privacy_url": "https://example.test/privacy",
    }
    values.update(overrides)
    return UsersConfig(**values)


def make_services(
    db: FakeDb | None = None, clock: Clock | None = None, **config: Any
) -> tuple[UsersService, FakeDb]:
    db = db or FakeDb()
    clock = clock or FixedClock(datetime(2026, 11, 4, 12, 0, tzinfo=UTC))

    def users_repo(_: Tx) -> FakeUsersRepository:
        return FakeUsersRepository(db)

    def mod_repo(_: Tx) -> FakeModerationRepository:
        return FakeModerationRepository(db)

    def events_repo(_: Tx) -> FakeEventsRepository:
        return FakeEventsRepository(db)

    moderation = ModerationService(mod_repo, clock)
    analytics = AnalyticsService(events_repo)
    users = UsersService(
        NullTxManager(),
        users_repo,
        moderation,
        analytics,
        clock,
        fake_config(**config),
        storage=db.storage,
        images=PillowImageProcessor(),
    )
    return users, db


def make_fake_services(
    db: FakeDb | None = None,
    clock: Clock | None = None,
    *,
    properties_config: PropertiesConfig | None = None,
    payments_config: PaymentsConfig | None = None,
    geocoder: Geocoder | None = None,
    contracts_config: ContractsConfig | None = None,
    **config: Any,
) -> tuple[Services, FakeDb]:
    """Повний набір сервісів на фейках (як у Container): для тестів сервісів, API й бота."""
    db = db or FakeDb()
    clock = clock or FixedClock(datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    users, db = make_services(db, clock, **config)
    moderation = ModerationService(lambda _: FakeModerationRepository(db), clock)
    analytics = AnalyticsService(lambda _: FakeEventsRepository(db))
    notifications = NotificationsService(
        NullTxManager(), lambda _: FakeNotificationsRepository(db.prop), users, clock
    )
    properties = PropertiesService(
        NullTxManager(),
        lambda _: FakePropertiesRepository(db.prop),
        users,
        moderation,
        analytics,
        notifications,
        geocoder or ManualGeocoder(),
        db.storage,
        clock,
        properties_config or PropertiesConfig(),
        notifier=db.notifier,
        images=PillowImageProcessor(),
    )
    users.set_city_check(properties.has_city)
    chat = ChatService(lambda _: FakeChatRepository(db.lease))
    leases = LeasesService(
        NullTxManager(),
        lambda _: FakeLeasesRepository(db.lease),
        users,
        properties,
        chat,
        notifications,
        db.cipher,
        clock,
        LeasesConfig(hmac_secret=TEST_HMAC),
    )
    contracts = ContractsService(
        NullTxManager(),
        lambda _: FakeContractsRepository(db.contract, clock.now),
        users,
        properties,
        leases,
        chat,
        notifications,
        moderation,
        db.storage,
        db.pdf,
        DiiaPortalVerifier(),
        clock,
        contracts_config or ContractsConfig(hmac_secret=TEST_HMAC),
        notifier=db.notifier,
    )
    payments = PaymentsService(
        NullTxManager(),
        lambda _: FakePaymentsRepository(db.payment),
        users,
        leases,
        chat,
        notifications,
        db.storage,
        clock,
        payments_config or PaymentsConfig(),
        notifier=db.notifier,
        receipts=PdfReceiptReader(),
    )
    wire_guards(users, properties, leases)
    wire_contracts(leases, contracts)
    services = Services(
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
    return services, db
