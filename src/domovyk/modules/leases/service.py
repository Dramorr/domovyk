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
    HasOpenLease,
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
    clean_reason,
    end_for_term,
    is_open,
    mask_card,
    mask_iban,
    months_between,
    normalize_card,
    normalize_iban,
    normalize_jar,
    short_name,
    termination_window,
    validate_money,
    validate_term,
    validate_termination,
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
    Agreement,
    AgreementState,
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
    ProposalIn,
    ProposalOut,
    RespondIn,
    TerminationIn,
    TerminationOut,
)
from domovyk.modules.notifications.service import (
    LeaseNotificationPrefs,
    NotificationsService,
    NotificationType,
)
from domovyk.modules.properties.service import PhotoOut, PropertiesService, PropertyOut
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
    "ProposalIn",
    "RespondIn",
    "TerminationIn",
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

    async def release_property(self, prop: PropertyOut) -> bool:
        """Перед видаленням об'єкта (FR-PRP-9): звільнити його від оренд.

        Жива оренда — HasOpenLease. Скасовані стираються повністю, як у FR-LSE-9.
        Повертає True, якщо лишилась завершена оренда: її історія потрібна учасникам,
        тому об'єкт не стирається, а архівується.
        """
        async with self._tx.begin() as tx:
            leases = await self._repo(tx).statuses_on_units([u.id for u in prop.units])
        if any(is_open(status) for _, status in leases):
            raise HasOpenLease("На об'єкті є оренда")
        for lease_id, status in leases:
            if status is LeaseStatus.CANCELLED:
                if self._purge_hook is not None:
                    await self._purge_hook(lease_id)
                async with self._tx.begin() as tx:
                    await self._repo(tx).delete(lease_id)
        return any(status is LeaseStatus.ENDED for _, status in leases)

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
        await repo.update(
            lease_id,
            status=LEASE.transition(lease.status, LeaseStatus.ACTIVE),
            activated_at=self._clock.now(),
        )
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

    async def billable_ids(self) -> list[uuid.UUID]:
        """Оренди, за якими нараховуємо оплату (M5): активні й ті, що завершуються."""
        async with self._tx.begin() as tx:
            return await self._repo(tx).ids_with_status([LeaseStatus.ACTIVE, LeaseStatus.ENDING])

    async def pay_requisites(self, tx: Tx, lease_id: uuid.UUID) -> dict[str, str]:
        """Повні реквізити для «Оплатити» (FR-PAY-8). Права перевіряє модуль payments."""
        lease = await self._repo(tx).get(lease_id)
        if lease is None or not lease.pay_details_enc:
            return {}
        data: dict[str, str] = json.loads(self._cipher.decrypt(lease.pay_details_enc))
        return data

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
            # Власник, задавши умови, тим самим їх погоджує (FR-LSE-10).
            await repo.update(lease_id, landlord_accepted_at=now)
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
            # Нові умови від власника = його згода; пропозиція орендаря стає неактуальною.
            values.update(
                landlord_accepted_at=self._clock.now(),
                proposal=None,
                proposal_by=None,
                proposal_at=None,
                declined_by=None,
                declined_at=None,
            )
            await repo.update(lease_id, **values)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(tx, conversation, SystemCode.TERMS_UPDATED)
            responsible = _responsible(ctx.members)
            if responsible is not None:
                await self._notify_lease(tx, ctx.lease, [responsible.user_id], "terms_updated", {})
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def accept_terms(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseOut:
        """LSE-TERMS ✅: сторона погоджує поточні умови (FR-LSE-10).

        Погодили обидві й немає відкритої пропозиції змін → чекаємо договір (M4).
        """
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role == "co_tenant":
                raise PermissionDenied("Умови погоджують власник і відповідальний орендар")
            if ctx.lease.status is not LeaseStatus.AWAITING_TENANT:
                raise Conflict("Умови вже погоджено", field="status")
            if ctx.lease.proposal is not None:
                raise Conflict("Спершу дайте відповідь на запропоновані зміни", field="proposal")
            values: dict[str, object] = (
                {"landlord_accepted_at": now}
                if ctx.my_role == "landlord"
                else {"terms_accepted_at": now}
            )
            if ctx.lease.declined_by == user.id:
                values.update(declined_by=None, declined_at=None)
            await repo.update(lease_id, **values)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(
                tx, conversation, SystemCode.TERMS_ACCEPTED, {"name": user.display_name}
            )
            other = _other_side(ctx)
            event = "landlord_agreed" if ctx.my_role == "landlord" else "terms_accepted"
            if other is not None:
                await self._notify_lease(tx, ctx.lease, [other], event, {})
            await self._maybe_ready(repo, lease_id)
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def propose_changes(
        self, telegram_uid: int, lease_id: uuid.UUID, data: ProposalIn
    ) -> LeaseOut:
        """Орендар пропонує зміни умов: обидві згоди знімаються, власник погоджує чи ні."""
        user = await self._users.require_active(telegram_uid)
        end = end_for_term(data.start_date, data.months)
        validate_term(data.start_date, end)
        validate_money(data.rent_amount, data.rent_due_day, data.deposit_amount)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role != "responsible":
                raise PermissionDenied("Зміни пропонує відповідальний орендар")
            if ctx.lease.status not in EDITABLE_STATUSES:
                raise Conflict("Оренда вже активна", field="status")
            values: dict[str, object] = {
                "proposal": {
                    "start_date": data.start_date.isoformat(),
                    "end_date": end.isoformat(),
                    "months": data.months,
                    "rent_amount": data.rent_amount,
                    "rent_due_day": data.rent_due_day,
                    "deposit_amount": data.deposit_amount,
                    "terms": data.terms.model_dump(),
                },
                "proposal_by": user.id,
                "proposal_at": now,
                "terms_accepted_at": None,
                "landlord_accepted_at": None,
                "declined_by": None,
                "declined_at": None,
            }
            if ctx.lease.status is LeaseStatus.AWAITING_SIGNATURES:
                values["status"] = LEASE.transition(ctx.lease.status, LeaseStatus.AWAITING_TENANT)
            await repo.update(lease_id, **values)
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(
                tx, conversation, SystemCode.TERMS_PROPOSED, {"name": user.display_name}
            )
            await self._notify_lease(
                tx, ctx.lease, [ctx.lease.landlord_user_id], "terms_proposed", {}
            )
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def respond_proposal(
        self, telegram_uid: int, lease_id: uuid.UUID, accept: bool
    ) -> LeaseOut:
        """Власник погоджує запропоновані зміни (вони стають умовами) або відхиляє їх."""
        user = await self._users.require_active(telegram_uid)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role != "landlord":
                raise PermissionDenied("На пропозицію відповідає власник")
            p = ctx.lease.proposal
            if p is None:
                raise Conflict("Пропозиції змін немає", field="proposal")
            cleared: dict[str, object] = {
                "proposal": None,
                "proposal_by": None,
                "proposal_at": None,
            }
            if accept:
                await repo.update(
                    lease_id,
                    start_date=date.fromisoformat(p["start_date"]),
                    end_date=date.fromisoformat(p["end_date"]),
                    rent_amount=p["rent_amount"],
                    rent_due_day=p["rent_due_day"],
                    deposit_amount=p["deposit_amount"],
                    terms=p["terms"],
                    # Автор пропозиції її погодив; власник — щойно.
                    terms_accepted_at=now,
                    landlord_accepted_at=now,
                    declined_by=None,
                    declined_at=None,
                    **cleared,
                )
                code, event = SystemCode.PROPOSAL_ACCEPTED, "proposal_accepted"
            else:
                await repo.update(
                    lease_id,
                    terms_accepted_at=None,
                    landlord_accepted_at=None,
                    declined_by=user.id,
                    declined_at=now,
                    **cleared,
                )
                code, event = SystemCode.PROPOSAL_DECLINED, "proposal_declined"
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(tx, conversation, code, {"name": user.display_name})
            responsible = _responsible(ctx.members)
            if responsible is not None:
                await self._notify_lease(tx, ctx.lease, [responsible.user_id], event, {})
            await self._maybe_ready(repo, lease_id)
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def _maybe_ready(self, repo: LeasesRepository, lease_id: uuid.UUID) -> None:
        """Обидві сторони погодили, пропозицій немає → «чекаємо договір»."""
        lease = await repo.get(lease_id)
        if lease is None or lease.status is not LeaseStatus.AWAITING_TENANT:
            return
        if (
            lease.proposal is not None
            or not lease.terms_accepted_at
            or not lease.landlord_accepted_at
        ):
            return
        if _responsible(await repo.members(lease_id)) is None:
            return
        await repo.update(
            lease_id, status=LEASE.transition(lease.status, LeaseStatus.AWAITING_SIGNATURES)
        )

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

    # ------------------------------------------------- розірвання (FR-LSE-7)

    async def request_termination(
        self, telegram_uid: int, lease_id: uuid.UUID, data: TerminationIn
    ) -> LeaseOut:
        """Сторона ініціює розірвання: дата (з попередженням за договором) і причина."""
        user = await self._users.require_active(telegram_uid)
        reason = clean_reason(data.reason)
        now = self._clock.now()
        today = to_kyiv(now).date()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.my_role == "co_tenant":
                raise PermissionDenied("Розриває власник або відповідальний орендар")
            if ctx.lease.status is not LeaseStatus.ACTIVE:
                raise Conflict("Розірвати можна лише активну оренду", field="status")
            validate_termination(data.date, today, ctx.lease.end_date, _notice_days(ctx.lease))
            await repo.update(
                lease_id,
                status=LEASE.transition(ctx.lease.status, LeaseStatus.ENDING),
                termination_date=data.date,
                termination_reason=reason,
                termination_by=user.id,
                termination_requested_at=now,
            )
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(
                tx,
                conversation,
                SystemCode.TERMINATION_REQUESTED,
                {"name": user.display_name, "date": data.date.isoformat()},
            )
            others = [ctx.lease.landlord_user_id] + [m.user_id for m in _active(ctx.members)]
            await self._notify_lease(
                tx,
                ctx.lease,
                [u for u in others if u != user.id],
                "termination_requested",
                {
                    "name": user.display_name,
                    "date": data.date.strftime("%d.%m.%Y"),
                    "reason": reason,
                },
            )
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def withdraw_termination(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseOut:
        """Ініціатор передумав до настання дати — оренда знову активна."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.lease.status is not LeaseStatus.ENDING:
                raise Conflict("Оренда не завершується", field="status")
            if ctx.lease.termination_by != user.id:
                raise PermissionDenied("Скасувати розірвання може лише той, хто його ініціював")
            await repo.update(
                lease_id,
                status=LEASE.transition(ctx.lease.status, LeaseStatus.ACTIVE),
                termination_date=None,
                termination_reason=None,
                termination_by=None,
                termination_requested_at=None,
            )
            conversation = await self._chat.ensure_for_lease(tx, lease_id)
            await self._chat.system(
                tx, conversation, SystemCode.TERMINATION_WITHDRAWN, {"name": user.display_name}
            )
            others = [ctx.lease.landlord_user_id] + [m.user_id for m in _active(ctx.members)]
            await self._notify_lease(
                tx,
                ctx.lease,
                [u for u in others if u != user.id],
                "termination_withdrawn",
                {"name": user.display_name},
            )
            return await self._out(tx, await self._ctx(tx, lease_id, user))

    async def finish_due(self) -> int:
        """Фонова задача: настала дата розірвання або минув строк договору → ended."""
        now = self._clock.now()
        today = to_kyiv(now).date()
        async with self._tx.begin() as tx:
            ids = await self._repo(tx).due_to_finish(today)
        for lease_id in ids:
            async with self._tx.begin() as tx:
                repo = self._repo(tx)
                lease = await repo.get(lease_id)
                if lease is None or lease.status not in (LeaseStatus.ACTIVE, LeaseStatus.ENDING):
                    continue
                status: LeaseStatus = lease.status
                if status is LeaseStatus.ACTIVE:  # строк минув без розірвання
                    status = LEASE.transition(status, LeaseStatus.ENDING)
                await repo.update(
                    lease_id, status=LEASE.transition(status, LeaseStatus.ENDED), ended_at=now
                )
                conversation = await self._chat.ensure_for_lease(tx, lease_id)
                await self._chat.system(tx, conversation, SystemCode.LEASE_ENDED)
                members = await repo.members(lease_id)
                await self._notify_lease(
                    tx,
                    lease,
                    [lease.landlord_user_id] + [m.user_id for m in _active(members)],
                    "lease_ended",
                    {},
                )
        return len(ids)

    async def hide(self, telegram_uid: int, lease_id: uuid.UUID) -> None:
        """FR-LSE-9: «Видалити» скасовану оренду — лише зі свого списку.

        Повністю (з чатом і файлами договору) оренда стирається, коли її прибрали всі
        учасники або минув строк зберігання (`purge_cancelled`).
        """
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
            if ctx.lease.status not in (LeaseStatus.CANCELLED, LeaseStatus.ENDED):
                raise Conflict(
                    "Прибрати зі списку можна скасовану або завершену оренду", field="status"
                )
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

    # ------------------------------------------------- фото об'єкта (FR-PRP-10)

    async def photos(self, telegram_uid: int, lease_id: uuid.UUID) -> list[PhotoOut]:
        """Фото об'єкта бачать усі учасники оренди."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
        return await self._properties.photos_of(ctx.prop.id)

    async def photo(self, telegram_uid: int, lease_id: uuid.UUID, photo_id: uuid.UUID) -> bytes:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ctx = await self._ctx(tx, lease_id, user)
        return await self._properties.photo_data(ctx.prop.id, photo_id)

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
            agreement=_agreement(lease, ctx.members),
            proposal=_proposal_out(lease),
            termination=_termination_out(lease, ctx.members),
            can_terminate=ctx.my_role in ("landlord", "responsible")
            and lease.status is LeaseStatus.ACTIVE,
            termination_earliest=termination_window(today, lease.end_date, _notice_days(lease))[0]
            if lease.status is LeaseStatus.ACTIVE
            else None,
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


def _other_side(ctx: _Ctx) -> uuid.UUID | None:
    if ctx.my_role == "landlord":
        r = _responsible(ctx.members)
        return r.user_id if r else None
    return ctx.lease.landlord_user_id


def _agreement(lease: LeaseRecord, members: list[MemberRecord]) -> Agreement:
    """Стан кожної половини смужки: погодив / пропонує зміни / відхилив / ще ні."""
    responsible = _responsible(members)
    tenant_id = responsible.user_id if responsible else None

    def side(user_id: uuid.UUID | None, accepted: datetime | None) -> AgreementState:
        if user_id is not None and lease.proposal is not None and lease.proposal_by == user_id:
            return "proposed"
        if user_id is not None and lease.declined_by == user_id:
            return "declined"
        return "agreed" if accepted else "none"

    after = lease.status not in (LeaseStatus.DRAFT, LeaseStatus.AWAITING_TENANT)
    return Agreement(
        tenant="agreed" if after else side(tenant_id, lease.terms_accepted_at),
        landlord="agreed" if after else side(lease.landlord_user_id, lease.landlord_accepted_at),
    )


def _notice_days(lease: LeaseRecord) -> int:
    return int(lease.terms.get("early_termination_notice_days", 30))


def _termination_out(lease: LeaseRecord, members: list[MemberRecord]) -> TerminationOut | None:
    if lease.termination_date is None or lease.termination_by is None:
        return None
    by: PartyRole
    if lease.termination_by == lease.landlord_user_id:
        by = "landlord"
    else:
        member = next((m for m in members if m.user_id == lease.termination_by), None)
        by = member.role.value if member else "responsible"
    return TerminationOut(
        date=lease.termination_date,
        reason=lease.termination_reason or "",
        by=by,
        requested_at=lease.termination_requested_at or lease.created_at,
    )


def _proposal_out(lease: LeaseRecord) -> ProposalOut | None:
    p = lease.proposal
    if p is None:
        return None
    return ProposalOut(
        by="landlord" if lease.proposal_by == lease.landlord_user_id else "responsible",
        start_date=date.fromisoformat(p["start_date"]),
        end_date=date.fromisoformat(p["end_date"]),
        months=p["months"],
        rent_amount=p["rent_amount"],
        rent_due_day=p["rent_due_day"],
        deposit_amount=p["deposit_amount"],
        terms=LeaseTerms.model_validate(p["terms"]),
        proposed_at=lease.proposal_at or lease.created_at,
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
