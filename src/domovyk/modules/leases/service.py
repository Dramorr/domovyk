"""Публічний інтерфейс модуля leases: оренда за запрошенням, учасники, умови, чат (M3).

FR-LSE-1..5, FR-CHT-1..2, FR-NTF-1. Оренда в M3 доходить до «чекаємо підписи»;
активною її робить договір (M4, FR-LSE-6).
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.crypto import Cipher, token_hash
from domovyk.core.errors import (
    Conflict,
    InvalidInput,
    InviteInvalid,
    LeaseLimitReached,
    NotFound,
    PermissionDenied,
)
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.contracts import conditions_fingerprint
from domovyk.domain.leases import (
    EDITABLE_STATUSES,
    INVITE_TTL_DAYS,
    LEASE,
    LeaseOrigin,
    LeaseStatus,
    MemberRole,
    PayMethod,
    clean_message,
    end_for_term,
    is_open,
    mask_card,
    mask_iban,
    months_between,
    normalize_card,
    normalize_iban,
    normalize_jar,
    short_name,
    validate_money,
    validate_term,
)
from domovyk.domain.properties import UnitKind
from domovyk.modules.chat.service import ChatService, MessageOut, MessagesPage, SystemCode
from domovyk.modules.leases.repository import (
    InviteRecord,
    LeaseRecord,
    LeasesRepository,
    MemberRecord,
    NewLease,
)
from domovyk.modules.leases.schemas import (
    InviteOut,
    InvitePreview,
    LeaseConditions,
    LeaseCreate,
    LeaseForContract,
    LeaseOut,
    LeaseSummary,
    LeaseTerms,
    MemberOut,
    PartyRole,
    PayDetailsIn,
    PayDisplay,
    PendingInvite,
)
from domovyk.modules.notifications.service import (
    LeaseNotificationPrefs,
    NotificationsService,
    NotificationType,
)
from domovyk.modules.properties.service import PropertiesService, PropertyOut
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "InviteOut",
    "InvitePreview",
    "LeaseConditions",
    "LeaseCreate",
    "LeaseForContract",
    "LeaseNotificationPrefs",
    "LeaseOut",
    "LeaseSummary",
    "LeaseTerms",
    "LeasesConfig",
    "LeasesService",
    "MessageOut",
    "MessagesPage",
    "PayDetailsIn",
]

INVITE_PREFIX = "inv_"
CHAT_NOTIFY_BUCKET_MIN = 10  # одне сповіщення про чат на людину за 10 хв


@dataclass(frozen=True)
class LeasesConfig:
    hmac_secret: str
    invite_ttl_days: int = INVITE_TTL_DAYS
    # FR-LSE-8: живих оренд одночасно як відповідальний орендар. Більше — заблоковано.
    tenant_max_open_leases: int = 2
    # FR-LSE-9: скасовану оренду стираємо повністю через N днів (або коли всі її прибрали).
    cancelled_retention_days: int = 30


@dataclass(frozen=True)
class _Conditions:
    start_date: date
    end_date: date
    rent_amount: int
    rent_due_day: int
    deposit_amount: int | None
    pay_method: PayMethod
    pay_details: dict[str, Any]
    pay_details_enc: str | None
    terms: dict[str, Any]


@dataclass
class _Ctx:
    lease: LeaseRecord
    prop: PropertyOut
    members: list[MemberRecord]
    my_role: PartyRole


class LeasesService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], LeasesRepository],
        users: UsersService,
        properties: PropertiesService,
        chat: ChatService,
        notifications: NotificationsService,
        cipher: Cipher,
        clock: Clock,
        config: LeasesConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._chat = chat
        self._notifications = notifications
        self._cipher = cipher
        self._clock = clock
        self._config = config
        self._purge_hook: Callable[[uuid.UUID], Awaitable[None]] | None = None

    # ------------------------------------------------------- для інших модулів

    async def has_open_leases(self, user_id: uuid.UUID) -> bool:
        """Видалення акаунта заборонене, поки є жива оренда (FR-USR-5)."""
        async with self._tx.begin() as tx:
            return await self._repo(tx).count_open_for_user(user_id) > 0

    async def has_open_lease_on_property(self, prop: PropertyOut) -> bool:
        """Об'єкт з живою орендою не видаляється."""
        async with self._tx.begin() as tx:
            return bool(await self._repo(tx).open_unit_ids([u.id for u in prop.units]))

    # ------------------------------------------------------- для договорів (M4)

    async def for_contract(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseForContract:
        """Оренда очима учасника: для договору й підписів (FR-CTR). Чужа — NotFound."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
            return await self._for_contract(tx, ctx, user.id)

    async def for_contract_by_id(self, tx: Tx, lease_id: uuid.UUID) -> LeaseForContract:
        """Без перевірки ролі — для модератора й фонових задач модуля contracts."""
        lease = await self._repo(tx).get(lease_id)
        if lease is None:
            raise NotFound("Оренду не знайдено")
        members = await self._repo(tx).members(lease_id)
        ctx = _Ctx(lease, await self._property_of(lease), members, "landlord")
        return await self._for_contract(tx, ctx, None)

    async def _for_contract(self, tx: Tx, ctx: _Ctx, viewer: uuid.UUID | None) -> LeaseForContract:
        responsible = _responsible(ctx.members)
        return LeaseForContract(
            lease=await self._out(tx, ctx),
            property=ctx.prop,
            origin=ctx.lease.origin,
            landlord_user_id=ctx.lease.landlord_user_id,
            responsible_user_id=responsible.user_id if responsible else None,
            viewer_user_id=viewer,
            conditions_hash=conditions_fingerprint(_agreed(ctx.lease)),
        )

    async def activate(self, tx: Tx, lease_id: uuid.UUID) -> None:
        """FR-LSE-6: договір підписано обома сторонами (або підтверджено CTR-EXT) → active."""
        repo = self._repo(tx)
        lease = await repo.get(lease_id)
        if lease is None:
            raise NotFound("Оренду не знайдено")
        await repo.update(lease_id, status=LEASE.transition(lease.status, LeaseStatus.ACTIVE))
        conversation = await self._chat.ensure_for_lease(tx, lease_id)
        await self._chat.system(
            tx, conversation, SystemCode.LEASE_ACTIVATED, {"start": lease.start_date.isoformat()}
        )
        members = await repo.members(lease_id)
        await self._notify_lease(
            tx,
            lease,
            [lease.landlord_user_id] + [m.user_id for m in _active(members)],
            "lease_active",
            {"start": lease.start_date.isoformat()},
        )

    async def open_lease_ids(self, lease_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Які з оренд ще живі — для ліміту оренд за РНОКПП (FR-LSE-8)."""
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            out: set[uuid.UUID] = set()
            for lease_id in lease_ids:
                lease = await repo.get(lease_id)
                if lease is not None and is_open(lease.status):
                    out.add(lease_id)
            return out

    @property
    def tenant_max_open_leases(self) -> int:
        return self._config.tenant_max_open_leases

    # ----------------------------------------------------------- створення

    async def create_lease(
        self, telegram_uid: int, data: LeaseCreate
    ) -> tuple[LeaseOut, InviteOut]:
        """LSE-NEW за запрошенням: умови → посилання для відповідального орендаря."""
        user = await self._users.require_active(telegram_uid)
        # Правило 5: без підтвердженої власності оренди не буде.
        prop = await self._properties.require_verified_owner(user.id, data.property_id)
        unit = self._pick_unit(prop, data.unit_id)
        conditions = self._conditions(data)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._check_unit_free(repo, prop, unit)
            lease_id = await repo.create(
                NewLease(
                    unit_id=unit,
                    landlord_user_id=user.id,
                    origin=LeaseOrigin.INVITE,
                    status=LeaseStatus.AWAITING_TENANT,
                    **asdict(conditions),
                )
            )
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(tx, conversation, SystemCode.LEASE_CREATED)
            invite = await self._new_invite(repo, lease_id, MemberRole.RESPONSIBLE, user.id, now)
            out = await self._out(tx, await self._ctx(tx, lease_id, user))
        return out, invite

    def _pick_unit(self, prop: PropertyOut, unit_id: uuid.UUID | None) -> uuid.UUID:
        if unit_id is None:
            entire = next((u for u in prop.units if u.kind is UnitKind.ENTIRE), None)
            if entire is None:
                raise NotFound("Юніт не знайдено")
            return entire.id
        if not any(u.id == unit_id for u in prop.units):
            raise NotFound("Юніт не знайдено")
        return unit_id

    async def _check_unit_free(
        self, repo: LeasesRepository, prop: PropertyOut, unit_id: uuid.UUID
    ) -> None:
        """FR-PRP-3: не можна здавати одночасно весь об'єкт і кімнату в ньому."""
        unit = next(u for u in prop.units if u.id == unit_id)
        if unit.kind is UnitKind.ENTIRE:
            blocking = [u.id for u in prop.units]
        else:
            blocking = [u.id for u in prop.units if u.kind is UnitKind.ENTIRE] + [unit_id]
        if await repo.open_unit_ids(blocking):
            raise Conflict("Цей об'єкт або кімната вже в оренді", field="unit_id")

    def _conditions(self, data: LeaseConditions) -> _Conditions:
        end = end_for_term(data.start_date, data.months)
        validate_term(data.start_date, end)
        validate_money(data.rent_amount, data.rent_due_day, data.deposit_amount)
        display, secret = self._pay(data.pay_method, data.pay_details)
        return _Conditions(
            start_date=data.start_date,
            end_date=end,
            rent_amount=data.rent_amount,
            rent_due_day=data.rent_due_day,
            deposit_amount=data.deposit_amount,
            pay_method=data.pay_method,
            pay_details=display,
            pay_details_enc=secret,
            terms=data.terms.model_dump(),
        )

    def _pay(
        self, method: PayMethod, details: PayDetailsIn | None
    ) -> tuple[dict[str, Any], str | None]:
        """Повні реквізити — лише зашифровані; відкрито — маска й скорочене ім'я."""
        if method is PayMethod.CASH:
            return {"masked": None, "recipient": None}, None
        d = details or PayDetailsIn()
        recipient = " ".join((d.recipient_name or "").split())
        if len(recipient) < 3:
            raise InvalidInput("Вкажи отримувача платежу", field="recipient_name")
        if method is PayMethod.CARD:
            value = normalize_card(d.card or "")
            masked: str | None = mask_card(value)
            secret = {"card": value}
        elif method is PayMethod.IBAN:
            value = normalize_iban(d.iban or "")
            masked = mask_iban(value)
            secret = {"iban": value}
        else:
            value = normalize_jar(d.jar_url or "")
            masked = "monobank"
            secret = {"jar_url": value}
        secret["recipient_name"] = recipient
        enc = self._cipher.encrypt(json.dumps(secret, ensure_ascii=False))
        return {"masked": masked, "recipient": short_name(recipient)}, enc

    # ------------------------------------------------------------- читання

    async def list_mine(self, telegram_uid: int) -> list[LeaseSummary]:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            leases = await self._repo(tx).for_user(user.id)
            out: list[LeaseSummary] = []
            for lease in leases:
                ctx = await self._ctx(tx, lease.id, user)
                full = await self._out(tx, ctx)
                out.append(LeaseSummary.model_validate(full.model_dump()))
            return out

    async def get(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    # ---------------------------------------------------------- запрошення

    async def new_invite(
        self, telegram_uid: int, lease_id: uuid.UUID, role: MemberRole
    ) -> InviteOut:
        """INV-SHARE: нове посилання; попереднє для тієї ж ролі перестає діяти."""
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if not is_open(ctx.lease.status):
                raise Conflict("Оренда вже завершена", field="status")
            if role is MemberRole.RESPONSIBLE:
                if ctx.my_role != "landlord":
                    raise PermissionDenied("Запрошує власник")
                if _responsible(ctx.members) is not None:
                    raise Conflict("Відповідальний орендар уже є", field="role")
            elif ctx.my_role not in ("landlord", "responsible"):
                raise PermissionDenied("Запрошують власник або відповідальний")
            return await self._new_invite(repo, lease_id, role, user.id, now)

    async def revoke_invite(self, telegram_uid: int, lease_id: uuid.UUID, role: MemberRole) -> None:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role not in ("landlord", "responsible"):
                raise PermissionDenied("Недостатньо прав")
            await self._repo(tx).revoke_invites(lease_id, role, self._clock.now())

    async def _new_invite(
        self,
        repo: LeasesRepository,
        lease_id: uuid.UUID,
        role: MemberRole,
        created_by: uuid.UUID,
        now: datetime,
    ) -> InviteOut:
        await repo.revoke_invites(lease_id, role, now)
        token = secrets.token_urlsafe(18)
        expires = now + timedelta(days=self._config.invite_ttl_days)
        await repo.add_invite(lease_id, self._hash(token), role, created_by, expires)
        return InviteOut(token=token, role=role, expires_at=expires)

    async def _over_limit(self, repo: LeasesRepository, user_id: uuid.UUID) -> bool:
        """FR-LSE-8: співмешканцем — без обмежень; відповідальним — до N живих оренд."""
        current = await repo.count_open_as_responsible(user_id)
        return current >= self._config.tenant_max_open_leases

    def _hash(self, token: str) -> str:
        return token_hash(token, self._config.hmac_secret)

    async def preview_invite(self, telegram_uid: int, token: str) -> InvitePreview:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            invite, reason = await self._find_invite(repo, token)
            if invite is None:
                return InvitePreview(valid=False, reason=reason)
            lease = await repo.get(invite.lease_id)
            if lease is None:
                return InvitePreview(valid=False, reason="not_found")
            members = await repo.members(lease.id)
            if lease.landlord_user_id == user.id:
                reason = "own_lease"
            elif any(m.user_id == user.id and m.left_at is None for m in members):
                reason = "already_member"
            elif invite.role is MemberRole.RESPONSIBLE and await self._over_limit(repo, user.id):
                reason = "lease_limit"
            prop = await self._property_of(lease)
            landlord = await self._users.get_by_id(lease.landlord_user_id)
            return InvitePreview(
                valid=reason == "ok",
                reason=reason,
                role=invite.role,
                landlord_name=landlord.display_name if landlord else None,
                street=prop.street,
                house_number=prop.house_number,
                apartment=prop.apartment,
                rent_amount=lease.rent_amount,
                start_date=lease.start_date,
                end_date=lease.end_date,
                lease_id=lease.id,
            )

    async def _find_invite(
        self, repo: LeasesRepository, token: str
    ) -> tuple[InviteRecord | None, str]:
        token = token.removeprefix(INVITE_PREFIX).strip()
        if not 10 <= len(token) <= 64:
            return None, "not_found"
        invite = await repo.invite_by_hash(self._hash(token))
        if invite is None:
            return None, "not_found"
        if invite.revoked_at is not None:
            return None, "revoked"
        if invite.used_at is not None:
            return None, "used"
        if invite.expires_at <= self._clock.now():
            return None, "expired"
        return invite, "ok"

    async def accept_invite(self, telegram_uid: int, token: str) -> LeaseOut:
        """INV-ACCEPT: приєднатися відповідальним орендарем або співмешканцем."""
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            invite, reason = await self._find_invite(repo, token)
            if invite is None:
                raise InviteInvalid("Запрошення недійсне", reason=reason)
            lease = await repo.get(invite.lease_id)
            if lease is None or not is_open(lease.status):
                raise InviteInvalid("Оренда вже недоступна", reason="not_found")
            if lease.landlord_user_id == user.id:
                raise InviteInvalid("Це твоя оренда", reason="own_lease")
            members = await repo.members(lease.id)
            if any(m.user_id == user.id and m.left_at is None for m in members):
                raise InviteInvalid("Ти вже в цій оренді", reason="already_member")
            if invite.role is MemberRole.RESPONSIBLE and _responsible(members) is not None:
                raise InviteInvalid("Відповідальний уже є", reason="used")
            if invite.role is MemberRole.RESPONSIBLE and await self._over_limit(repo, user.id):
                raise LeaseLimitReached(
                    "Забагато одночасних оренд", limit=self._config.tenant_max_open_leases
                )
            await repo.add_member(lease.id, user.id, invite.role, now)
            await repo.use_invite(invite.id, user.id, now)
            conversation = await self._chat.ensure_for_lease(tx, lease.id)
            joined = await self._chat.system(
                tx,
                conversation,
                SystemCode.MEMBER_JOINED,
                {"name": user.display_name, "role": invite.role.value},
            )
            # Усе, що було до приходу людини, — не «нове» для неї.
            await self._chat.mark_read(tx, conversation, user.id, joined.id)
            await self._notify_lease(
                tx,
                lease,
                [lease.landlord_user_id],
                "member_joined",
                {"name": user.display_name, "role": invite.role.value},
            )
            return await self._out(tx, await self._ctx(tx, lease.id, user))

    # --------------------------------------------------------------- умови

    async def update_conditions(
        self, telegram_uid: int, lease_id: uuid.UUID, data: LeaseConditions
    ) -> LeaseOut:
        """Власник змінює умови до активації. Погоджені — треба погодити знову."""
        user = await self._users.require_active(telegram_uid)
        conditions = self._conditions(data)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role != "landlord":
                raise PermissionDenied("Умови змінює власник")
            if ctx.lease.status not in EDITABLE_STATUSES:
                # Після активації — лише додаткова угода (FR-LSE-3, M4).
                raise Conflict("Умови активної оренди змінюються додатковою угодою", field="status")
            values: dict[str, object] = asdict(conditions)
            if ctx.lease.status is LeaseStatus.AWAITING_SIGNATURES:
                values["status"] = LEASE.transition(ctx.lease.status, LeaseStatus.AWAITING_TENANT)
            values["terms_accepted_at"] = None
            await repo.update(lease_id, **values)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(tx, conversation, SystemCode.TERMS_UPDATED)
            responsible = _responsible(ctx.members)
            if responsible is not None:
                await self._notify_lease(tx, ctx.lease, [responsible.user_id], "terms_updated", {})
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def accept_terms(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseOut:
        """LSE-TERMS ✅: відповідальний погоджує умови → чекаємо договір (M4)."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role != "responsible":
                raise PermissionDenied("Умови погоджує відповідальний орендар")
            status = LEASE.transition(ctx.lease.status, LeaseStatus.AWAITING_SIGNATURES)
            await repo.update(lease_id, status=status, terms_accepted_at=self._clock.now())
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(
                tx, conversation, SystemCode.TERMS_ACCEPTED, {"name": user.display_name}
            )
            await self._notify_lease(
                tx, ctx.lease, [ctx.lease.landlord_user_id], "terms_accepted", {}
            )
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def request_changes(self, telegram_uid: int, lease_id: uuid.UUID, text: str) -> LeaseOut:
        """LSE-TERMS ✏️: прохання змін іде власнику повідомленням у чат."""
        user = await self._users.require_active(telegram_uid)
        body = clean_message(text)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role != "responsible":
                raise PermissionDenied("Змін просить відповідальний орендар")
            if ctx.lease.status not in EDITABLE_STATUSES:
                raise Conflict("Оренда вже активна", field="status")
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(
                tx, conversation, SystemCode.TERMS_CHANGE_REQUESTED, {"name": user.display_name}
            )
            await self._chat.post(tx, conversation, user.id, body)
            await self._notify_lease(
                tx, ctx.lease, [ctx.lease.landlord_user_id], "terms_change_requested", {}
            )
            return await self._out(tx, ctx)

    async def decline(self, telegram_uid: int, lease_id: uuid.UUID) -> None:
        """LSE-TERMS ❌ або співмешканець виходить (MEM). Відповідальний виходить до активації."""
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role == "landlord":
                raise PermissionDenied("Власник скасовує оренду, а не виходить")
            if ctx.my_role == "responsible" and ctx.lease.status not in EDITABLE_STATUSES:
                # Після активації відповідальний не виходить, а завершує оренду (FR-LSE-7).
                raise Conflict("Відповідальний завершує оренду, а не виходить", field="status")
            await repo.mark_left(lease_id, user.id, now)
            if ctx.my_role == "responsible" and ctx.lease.status is LeaseStatus.AWAITING_SIGNATURES:
                await repo.update(
                    lease_id,
                    status=LEASE.transition(ctx.lease.status, LeaseStatus.AWAITING_TENANT),
                    terms_accepted_at=None,
                )
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            code = (
                SystemCode.TERMS_DECLINED
                if ctx.my_role == "responsible"
                else SystemCode.MEMBER_LEFT
            )
            await self._chat.system(tx, conversation, code, {"name": user.display_name})
            await self._notify_lease(
                tx, ctx.lease, [ctx.lease.landlord_user_id], code.value, {"name": user.display_name}
            )

    async def hide(self, telegram_uid: int, lease_id: uuid.UUID) -> None:
        """FR-LSE-9: «Видалити» скасовану оренду — лише зі свого списку.

        Повністю (з чатом і файлами договору) оренда стирається, коли її прибрали всі
        учасники або минув строк зберігання (`purge_cancelled`).
        """
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.lease.status is not LeaseStatus.CANCELLED:
                raise Conflict("Видалити можна лише скасовану оренду", field="status")
            await self._repo(tx).hide(lease_id, user.id, self._clock.now())
        await self.purge_cancelled()

    def set_purge_hook(self, hook: Callable[[uuid.UUID], Awaitable[None]]) -> None:
        """Модуль contracts прибирає файли договорів (без циклічних імпортів модулів)."""
        self._purge_hook = hook

    async def purge_cancelled(self) -> int:
        """Стерти скасовані оренди: їх прибрали всі учасники або минув строк зберігання."""
        before = self._clock.now() - timedelta(days=self._config.cancelled_retention_days)
        async with self._tx.begin() as tx:
            ids = await self._repo(tx).purgeable(before)
        for lease_id in ids:
            if self._purge_hook is not None:
                await self._purge_hook(lease_id)
            async with self._tx.begin() as tx:
                await self._repo(tx).delete(lease_id)
        return len(ids)

    async def cancel(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseOut:
        """Власник скасовує оренду до активації."""
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role != "landlord":
                raise PermissionDenied("Скасовує власник")
            status = LEASE.transition(ctx.lease.status, LeaseStatus.CANCELLED)
            await repo.update(lease_id, status=status, cancelled_at=now)
            await repo.revoke_invites(lease_id, None, now)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(tx, conversation, SystemCode.LEASE_CANCELLED)
            await self._notify_lease(
                tx, ctx.lease, [m.user_id for m in _active(ctx.members)], "lease_cancelled", {}
            )
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    # ---------------------------------------------------------------- чат

    async def messages(
        self,
        telegram_uid: int,
        lease_id: uuid.UUID,
        *,
        after_id: int = 0,
        before_id: int | None = None,
    ) -> MessagesPage:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            await self._ctx(tx, lease_id, user)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            return await self._chat.page(
                tx, conversation, user.id, after_id=after_id, before_id=before_id
            )

    async def post_message(self, telegram_uid: int, lease_id: uuid.UUID, body: str) -> MessageOut:
        """FR-CHT-1: повідомлення бачать усі учасники; іншим — сповіщення (не частіше 10 хв)."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.lease.status in (LeaseStatus.CANCELLED, LeaseStatus.ENDED):
                raise Conflict("Оренда вже закрита", field="status")
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            message = await self._chat.post(tx, conversation, user.id, body)
            now = self._clock.now()
            bucket = int(now.timestamp() // (CHAT_NOTIFY_BUCKET_MIN * 60))
            others = [ctx.lease.landlord_user_id] + [m.user_id for m in _active(ctx.members)]
            for uid in others:
                if uid == user.id:
                    continue
                await self._notifications.enqueue_for_lease(
                    tx,
                    uid,
                    lease_id,
                    NotificationType.CHAT_MESSAGE,
                    {
                        "lease_id": str(lease_id),
                        "sender": user.display_name,
                        "preview": message.body[:120] if message.body else "",
                        "street": ctx.prop.street,
                        "house_number": ctx.prop.house_number,
                    },
                    dedupe_key=f"chat:{conversation}:{uid}:{bucket}",
                )
            return message

    async def mark_read(self, telegram_uid: int, lease_id: uuid.UUID, message_id: int) -> None:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            await self._ctx(tx, lease_id, user)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.mark_read(tx, conversation, user.id, message_id)

    # ---------------------------------------------------- налаштування сповіщень

    async def notification_prefs(
        self, telegram_uid: int, lease_id: uuid.UUID
    ) -> LeaseNotificationPrefs:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            await self._ctx(tx, lease_id, user)
            return await self._notifications.lease_prefs(tx, user.id, lease_id)

    async def set_notification_prefs(
        self, telegram_uid: int, lease_id: uuid.UUID, prefs: LeaseNotificationPrefs
    ) -> LeaseNotificationPrefs:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            await self._ctx(tx, lease_id, user)
            return await self._notifications.set_lease_prefs(tx, user.id, lease_id, prefs)

    # --------------------------------------------------------------- службове

    async def _property_of(self, lease: LeaseRecord) -> PropertyOut:
        prop = await self._properties.property_of_unit(lease.unit_id)
        if prop is None:
            raise NotFound("Об'єкт не знайдено")
        return prop

    async def _ctx(self, tx: Tx, lease_id: uuid.UUID, user: UserOut) -> _Ctx:
        """Оренда + роль людини в ній. Чужа оренда виглядає як неіснуюча."""
        repo = self._repo(tx)
        lease = await repo.get(lease_id)
        if lease is None:
            raise NotFound("Оренду не знайдено")
        members = await repo.members(lease_id)
        role: PartyRole
        if lease.landlord_user_id == user.id:
            role = "landlord"
        else:
            me = next((m for m in members if m.user_id == user.id and m.left_at is None), None)
            if me is None:
                raise NotFound("Оренду не знайдено")
            role = me.role.value
        return _Ctx(lease, await self._property_of(lease), members, role)

    async def _out(self, tx: Tx, ctx: _Ctx) -> LeaseOut:
        lease, prop = ctx.lease, ctx.prop
        unit = next((u for u in prop.units if u.id == lease.unit_id), None)
        landlord = await self._users.get_by_id(lease.landlord_user_id)
        members = [
            MemberOut(
                user_id=lease.landlord_user_id,
                display_name=landlord.display_name if landlord else "",
                role="landlord",
                joined_at=None,
            )
        ]
        for m in _active(ctx.members):
            u = await self._users.get_by_id(m.user_id)
            members.append(
                MemberOut(
                    user_id=m.user_id,
                    display_name=u.display_name if u else "",
                    role=m.role.value,
                    joined_at=m.joined_at,
                )
            )
        conversation = await self._chat.conversation_for_lease(tx, lease.id)
        me = next((m.user_id for m in members if m.role == ctx.my_role), lease.landlord_user_id)
        unread = await self._chat.unread(tx, conversation, me) if conversation else 0
        invites = await self._repo(tx).pending_invites(lease.id, self._clock.now())
        can_manage = ctx.my_role in ("landlord", "responsible")
        today = to_kyiv(self._clock.now()).date()
        return LeaseOut(
            id=lease.id,
            status=lease.status,
            my_role=ctx.my_role,
            street=prop.street,
            house_number=prop.house_number,
            apartment=prop.apartment,
            unit_kind=unit.kind if unit else UnitKind.ENTIRE,
            unit_name=unit.name if unit else "",
            start_date=lease.start_date,
            end_date=lease.end_date,
            rent_amount=lease.rent_amount,
            unread=unread,
            property_id=prop.id,
            months_total=months_between(lease.start_date, lease.end_date) + 1,
            months_left=months_between(max(today, lease.start_date), lease.end_date),
            currency=lease.currency,
            rent_due_day=lease.rent_due_day,
            deposit_amount=lease.deposit_amount,
            pay=PayDisplay(
                method=lease.pay_method,
                masked=lease.pay_details.get("masked"),
                recipient=lease.pay_details.get("recipient"),
            ),
            terms=LeaseTerms.model_validate(lease.terms),
            terms_accepted_at=lease.terms_accepted_at,
            members=members,
            pending_invites=(
                [PendingInvite(role=i.role, expires_at=i.expires_at) for i in invites]
                if can_manage
                else []
            ),
            can_edit=ctx.my_role == "landlord" and lease.status in EDITABLE_STATUSES,
            can_invite_co_tenant=can_manage and is_open(lease.status),
            created_at=lease.created_at,
        )

    async def _notify_lease(
        self,
        tx: Tx,
        lease: LeaseRecord,
        user_ids: list[uuid.UUID],
        event: str,
        params: dict[str, object],
    ) -> None:
        prop = await self._property_of(lease)
        stamp = self._clock.now().strftime("%Y%m%d%H%M%S%f")
        for uid in user_ids:
            await self._notifications.enqueue_for_lease(
                tx,
                uid,
                lease.id,
                NotificationType.LEASE_UPDATE,
                {
                    "lease_id": str(lease.id),
                    "event": event,
                    "street": prop.street,
                    "house_number": prop.house_number,
                    **params,
                },
                dedupe_key=f"lease:{lease.id}:{event}:{uid}:{stamp}",
            )


def _agreed(lease: LeaseRecord) -> dict[str, Any]:
    """Погоджені умови для відбитка договору: будь-яка зміна → договір заново."""
    return {
        "unit_id": str(lease.unit_id),
        "start_date": lease.start_date.isoformat(),
        "end_date": lease.end_date.isoformat(),
        "rent_amount": lease.rent_amount,
        "currency": lease.currency,
        "rent_due_day": lease.rent_due_day,
        "deposit_amount": lease.deposit_amount,
        "pay_method": lease.pay_method.value,
        "pay_details": lease.pay_details,
        "pay_details_enc": lease.pay_details_enc,
        "terms": lease.terms,
        "terms_accepted_at": lease.terms_accepted_at.isoformat()
        if lease.terms_accepted_at
        else None,
    }


def _active(members: list[MemberRecord]) -> list[MemberRecord]:
    return [m for m in members if m.left_at is None]


def _responsible(members: list[MemberRecord]) -> MemberRecord | None:
    return next(
        (m for m in _active(members) if m.role is MemberRole.RESPONSIBLE),
        None,
    )
