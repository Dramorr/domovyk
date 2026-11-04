"""Публічний інтерфейс модуля users: реєстрація, телефон, профіль, ролі, видалення.

FR-USR-1..5. Інші модулі й інтерфейси працюють з акаунтами лише через цей файл.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from domovyk.core.clock import Clock
from domovyk.core.crypto import normalize_phone, phone_hash
from domovyk.core.errors import (
    AccountInactive,
    AlreadyRegistered,
    Banned,
    Conflict,
    ConsentOutdated,
    InvalidInput,
    NotFound,
    NotRegistered,
    ProtectedRole,
)
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.users import (
    ConsentDocument,
    IdentityProvider,
    UserRole,
    UserStatus,
    can_use_app,
    new_role_for_moderator_change,
    normalize_display_name,
    require_admin,
)
from domovyk.modules.analytics.service import AnalyticsService, EventName, EventSource
from domovyk.modules.moderation.service import AuditAction, BanIdentifiers, ModerationService
from domovyk.modules.users.repository import UsersRepository
from domovyk.modules.users.schemas import ConsentInfo, TenantProfile, UserOut

__all__ = ["ConsentInfo", "TenantProfile", "UserOut", "UsersConfig", "UsersService"]

# Ім'я, яке лишається замість справжнього після видалення акаунта (FR-USR-5).
DELETED_NAME = "deleted"
# Причина автоматичного бану, коли забанена людина реєструється знову.
REREGISTRATION_REASON = "reregistration_attempt"


@dataclass(frozen=True)
class UsersConfig:
    hmac_secret: str
    admin_telegram_ids: frozenset[int]
    terms_version: str
    privacy_version: str
    terms_url: str
    privacy_url: str


class UsersService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], UsersRepository],
        moderation: ModerationService,
        analytics: AnalyticsService,
        clock: Clock,
        config: UsersConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._moderation = moderation
        self._analytics = analytics
        self._clock = clock
        self._config = config

    # ------------------------------------------------------------------ читання

    def consent_info(self) -> ConsentInfo:
        c = self._config
        return ConsentInfo(
            terms_version=c.terms_version,
            privacy_version=c.privacy_version,
            terms_url=c.terms_url,
            privacy_url=c.privacy_url,
        )

    async def find_by_telegram(self, telegram_uid: int) -> UserOut | None:
        """Акаунт за Telegram ID або None. Бутстрап-адмін отримує роль тут же."""
        async with self._tx.begin() as tx:
            user = await self._repo(tx).get_by_identity(
                IdentityProvider.TELEGRAM, str(telegram_uid)
            )
            if user is None:
                return None
            return await self._sync_bootstrap_admin(tx, user)

    async def require_active(self, telegram_uid: int) -> UserOut:
        user = await self.find_by_telegram(telegram_uid)
        if user is None:
            raise NotRegistered()
        if user.status is UserStatus.BANNED:
            raise Banned()
        if not can_use_app(user.status):
            raise AccountInactive()
        return user

    async def get_tenant_profile(self, telegram_uid: int) -> TenantProfile:
        user = await self.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            return await self._repo(tx).get_tenant_profile(user.id) or TenantProfile()

    # --------------------------------------------------------------- реєстрація

    async def register(
        self,
        telegram_uid: int,
        display_name: str,
        *,
        terms_version: str,
        privacy_version: str,
        source: EventSource = EventSource.MINI_APP,
    ) -> UserOut:
        """ONB-1/ONB-2: згода з поточними версіями документів + ім'я (FR-USR-1, FR-USR-2)."""
        c = self._config
        if terms_version != c.terms_version or privacy_version != c.privacy_version:
            raise ConsentOutdated()
        name = normalize_display_name(display_name)

        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ban = await self._moderation.find_active_ban(
                tx, BanIdentifiers(telegram_uid=str(telegram_uid))
            )
            if ban is not None:
                raise Banned()
            if await repo.get_by_identity(IdentityProvider.TELEGRAM, str(telegram_uid)):
                raise AlreadyRegistered()

            is_admin = telegram_uid in c.admin_telegram_ids
            user_id = await repo.create(name, UserRole.ADMIN if is_admin else UserRole.USER)
            now = self._clock.now()
            await repo.add_identity(
                user_id,
                IdentityProvider.TELEGRAM,
                str(telegram_uid),
                is_contact=False,
                verified_at=now,
            )
            await repo.add_consent(user_id, ConsentDocument.TERMS, c.terms_version, now)
            await repo.add_consent(user_id, ConsentDocument.PRIVACY, c.privacy_version, now)
            if is_admin:
                await self._moderation.audit(
                    tx, None, AuditAction.ROLE_BOOTSTRAP, "user", str(user_id), {"role": "admin"}
                )
            await self._analytics.record(tx, EventName.REGISTERED, user_id=user_id, source=source)
            return await self._must_get(repo, user_id)

    async def add_phone(self, telegram_uid: int, phone: str) -> UserOut:
        """ONB-3: номер із контакту Telegram, який людина надіслала сама (FR-USR-1, FR-USR-2).

        Номер для контактів зберігаємо, для банів — лише його HMAC-хеш.
        """
        digits = normalize_phone(phone)
        if not 8 <= len(digits) <= 15:
            raise InvalidInput("Некоректний номер", field="phone")
        e164 = "+" + digits
        hashed = phone_hash(digits, self._config.hmac_secret)

        banned_now = False
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            user = await repo.get_by_identity(IdentityProvider.TELEGRAM, str(telegram_uid))
            if user is None:
                raise NotRegistered()
            if not can_use_app(user.status):
                raise Banned() if user.status is UserStatus.BANNED else AccountInactive()

            ban = await self._moderation.find_active_ban(tx, BanIdentifiers(phone_hash=hashed))
            if ban is not None:
                # Забанена людина під новим Telegram: блокуємо й цей акаунт (FR-USR-2).
                await repo.set_status(user.id, UserStatus.BANNED)
                await self._moderation.ban(
                    tx,
                    BanIdentifiers(telegram_uid=str(telegram_uid), phone_hash=hashed),
                    reason=REREGISTRATION_REASON,
                    user_id=user.id,
                    actor_id=None,
                )
                banned_now = True
            else:
                owner = await repo.get_by_identity(IdentityProvider.PHONE, e164)
                if owner is not None and owner.id != user.id:
                    raise Conflict("Номер уже прив'язано до іншого акаунта", field="phone")
                if owner is None:
                    await repo.remove_identities(user.id, [IdentityProvider.PHONE])
                    await repo.add_identity(
                        user.id,
                        IdentityProvider.PHONE,
                        e164,
                        is_contact=True,
                        verified_at=self._clock.now(),
                    )
                result = await self._must_get(repo, user.id)
        if banned_now:
            raise Banned()
        return result

    # ------------------------------------------------------------------ профіль

    async def update_display_name(self, telegram_uid: int, display_name: str) -> UserOut:
        user = await self.require_active(telegram_uid)
        name = normalize_display_name(display_name)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.set_display_name(user.id, name)
            return await self._must_get(repo, user.id)

    async def update_tenant_profile(
        self, telegram_uid: int, profile: TenantProfile
    ) -> TenantProfile:
        user = await self.require_active(telegram_uid)
        cleaned = profile.model_copy(
            update={
                "about": _clean(profile.about),
                "occupation": _clean(profile.occupation),
                "pets_details": _clean(profile.pets_details) if profile.has_pets else None,
            }
        )
        async with self._tx.begin() as tx:
            await self._repo(tx).upsert_tenant_profile(user.id, cleaned)
        return cleaned

    async def delete_account(self, telegram_uid: int) -> None:
        """FR-USR-5: м'яке видалення й анонімізація. Бани лишаються — вони на хешах.

        TODO(M3): заборонити, поки є активна оренда (через leases/service.py).
        """
        user = await self.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.set_display_name(user.id, DELETED_NAME)
            await repo.delete_tenant_profile(user.id)
            await repo.remove_identities(user.id)
            await repo.set_status(user.id, UserStatus.DELETED, deleted_at=self._clock.now())
            await self._moderation.audit(
                tx, user.id, AuditAction.ACCOUNT_DELETE, "user", str(user.id)
            )

    # -------------------------------------------------------------------- адмін

    async def set_moderator(
        self, actor_telegram_uid: int, target_telegram_uid: int, *, grant: bool
    ) -> UserOut:
        """FR-USR-4: адмін видає або забирає роль модератора. Пишеться в audit_log."""
        actor = await self.require_active(actor_telegram_uid)
        require_admin(actor.role)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            target = await repo.get_by_identity(IdentityProvider.TELEGRAM, str(target_telegram_uid))
            if target is None:
                raise NotFound("Користувача не знайдено")
            new_role = new_role_for_moderator_change(target.role, grant=grant)
            if new_role is not target.role:
                await repo.set_role(target.id, new_role)
                await self._moderation.audit(
                    tx,
                    actor.id,
                    AuditAction.ROLE_GRANT if grant else AuditAction.ROLE_REVOKE,
                    "user",
                    str(target.id),
                    {"from": target.role.value, "to": new_role.value},
                )
            return await self._must_get(repo, target.id)

    async def ban(self, actor_telegram_uid: int, target_telegram_uid: int, reason: str) -> None:
        """Бан за всіма відомими ідентифікаторами (FR-CMP-4). Працює й до реєстрації людини."""
        actor = await self.require_active(actor_telegram_uid)
        require_admin(actor.role)
        if target_telegram_uid == actor_telegram_uid:
            raise InvalidInput("Не можна забанити себе")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            target = await repo.get_by_identity(IdentityProvider.TELEGRAM, str(target_telegram_uid))
            ids = BanIdentifiers(telegram_uid=str(target_telegram_uid))
            if target is not None:
                if target.role is UserRole.ADMIN:
                    raise ProtectedRole("Адміністратора не можна забанити")
                phone = await repo.identity_uid(target.id, IdentityProvider.PHONE)
                if phone:
                    ids.phone_hash = phone_hash(phone, self._config.hmac_secret)
                await repo.set_status(target.id, UserStatus.BANNED)
            await self._moderation.ban(
                tx,
                ids,
                reason=reason,
                user_id=target.id if target else None,
                actor_id=actor.id,
            )

    async def unban(self, actor_telegram_uid: int, target_telegram_uid: int) -> int:
        actor = await self.require_active(actor_telegram_uid)
        require_admin(actor.role)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            target = await repo.get_by_identity(IdentityProvider.TELEGRAM, str(target_telegram_uid))
            ids = BanIdentifiers(telegram_uid=str(target_telegram_uid))
            if target is not None:
                phone = await repo.identity_uid(target.id, IdentityProvider.PHONE)
                if phone:
                    ids.phone_hash = phone_hash(phone, self._config.hmac_secret)
            lifted = await self._moderation.lift(
                tx, ids, actor_id=actor.id, user_id=target.id if target else None
            )
            if target is not None and target.status is UserStatus.BANNED:
                await repo.set_status(target.id, UserStatus.ACTIVE)
            return lifted

    # ---------------------------------------------------------------- службове

    async def _sync_bootstrap_admin(self, tx: Tx, user: UserOut) -> UserOut:
        if (
            user.telegram_uid in self._config.admin_telegram_ids
            and user.role is not UserRole.ADMIN
            and can_use_app(user.status)
        ):
            repo = self._repo(tx)
            await repo.set_role(user.id, UserRole.ADMIN)
            await self._moderation.audit(
                tx,
                None,
                AuditAction.ROLE_BOOTSTRAP,
                "user",
                str(user.id),
                {"from": user.role.value, "to": "admin"},
            )
            return await self._must_get(repo, user.id)
        return user

    @staticmethod
    async def _must_get(repo: UsersRepository, user_id: uuid.UUID) -> UserOut:
        user = await repo.get(user_id)
        if user is None:
            raise NotFound()
        return user


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
