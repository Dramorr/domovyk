"""Публічний інтерфейс модуля applications: заявки орендарів (FR-APP-1..3, 6, 7, ADR 0020).

Орендар відгукується на опубліковане оголошення. До схвалення сторони бачать одна одну
(ім'я, вік, з якого часу на Домовику), але не контакти й не точну адресу (правило 9).
Після схвалення відкриваються телефон, адреса й чат заявки, а власник одним кроком
оформлює оренду. Власник мовчить 72 год — заявка закривається (FR-APP-3).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from domovyk.core.clock import Clock
from domovyk.core.errors import Conflict, NotFound, PermissionDenied
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.applications import (
    APPLICATION,
    OPEN,
    REVEALED,
    ApplicationStatus,
    DeclineReason,
    clean_application_message,
)
from domovyk.domain.listings import ListingStatus
from domovyk.modules.analytics.service import AnalyticsService, EventName, EventSource
from domovyk.modules.applications.repository import ApplicationRecord, ApplicationsRepository
from domovyk.modules.applications.schemas import (
    ApplicationCard,
    ApplicationIn,
    ApplicationOut,
    ContactsOut,
    DeclineIn,
    ListingBrief,
)
from domovyk.modules.chat.service import ChatService, MessageOut, MessagesPage, SystemCode
from domovyk.modules.leases.service import LeaseConditions, LeaseCreate, LeaseOut, LeasesService
from domovyk.modules.listings.service import ListingOut, ListingsService
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.properties.service import PropertiesService, PropertyOut
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "ApplicationCard",
    "ApplicationIn",
    "ApplicationOut",
    "ApplicationsConfig",
    "ApplicationsService",
    "DeclineIn",
]

log = logging.getLogger(__name__)

CHAT_NOTIFY_BUCKET_MIN = 10  # як у чаті оренди: не частіше разу на 10 хв


@dataclass(frozen=True)
class ApplicationsConfig:
    expire_hours: int = 72  # FR-APP-3
    max_open_per_tenant: int = 10  # одночасно відкритих заявок (захист від розсилки «всім»)


@dataclass(frozen=True)
class _Ctx:
    app: ApplicationRecord
    listing: ListingOut
    prop: PropertyOut
    role: str  # "tenant" | "landlord"


class ApplicationsService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], ApplicationsRepository],
        users: UsersService,
        properties: PropertiesService,
        listings: ListingsService,
        leases: LeasesService,
        chat: ChatService,
        notifications: NotificationsService,
        analytics: AnalyticsService,
        clock: Clock,
        config: ApplicationsConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._listings = listings
        self._leases = leases
        self._chat = chat
        self._notifications = notifications
        self._analytics = analytics
        self._clock = clock
        self._config = config
        # Заявки, з яких саме зараз оформлюють оренду: хук «здано» їх не закриває.
        self._converting: set[uuid.UUID] = set()

    # ------------------------------------------------------------- орендар

    async def apply(
        self,
        telegram_uid: int,
        listing_id: uuid.UUID,
        data: ApplicationIn,
        *,
        source: EventSource = EventSource.MINI_APP,
    ) -> ApplicationCard:
        """APP-NEW (FR-APP-1): відгук на опубліковане оголошення."""
        user = await self._users.require_active(telegram_uid)
        message = clean_application_message(data.message)
        found = await self._listings.by_ids([listing_id])
        if not found or found[0].status is not ListingStatus.ACTIVE:
            raise NotFound("Оголошення не знайдено")
        listing = found[0]
        prop = await self._prop(listing.property_id)
        if prop.owner_user_id == user.id:
            raise PermissionDenied("Це твоє оголошення", reason="own_listing")
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if await repo.open_for(listing_id, user.id) is not None:
                raise Conflict("Заявка вже є", reason="already_applied")
            open_now = [a for a in await repo.by_tenant(user.id) if a.status in OPEN]
            if len(open_now) >= self._config.max_open_per_tenant:
                raise Conflict("Забагато відкритих заявок", reason="too_many")
            app_id = await repo.create(
                listing_id,
                user.id,
                message,
                expires_at=now + timedelta(hours=self._config.expire_hours),
                created_at=now,
            )
            await repo.add_history(app_id, None, ApplicationStatus.PENDING, user.id, now)
            await self._analytics.record(
                tx, EventName.APPLICATION_SENT, user_id=user.id, source=source
            )
            await self._notify(
                tx, prop.owner_user_id, "new", app_id, prop, listing, {"name": user.display_name}
            )
        return await self.card(telegram_uid, app_id)

    async def mine(self, telegram_uid: int) -> list[ApplicationOut]:
        """APP-MY: заявки орендаря, новіші спершу."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            records = await self._repo(tx).by_tenant(user.id)
        return await self._outs(user, records)

    async def withdraw(self, telegram_uid: int, application_id: uuid.UUID) -> ApplicationCard:
        user = await self._users.require_active(telegram_uid)
        ctx = await self._ctx(user, application_id)
        if ctx.role != "tenant":
            raise PermissionDenied("Відкликати може лише орендар")
        await self._move(ctx, ApplicationStatus.WITHDRAWN, user.id)
        async with self._tx.begin() as tx:
            await self._notify(
                tx,
                ctx.prop.owner_user_id,
                "withdrawn",
                ctx.app.id,
                ctx.prop,
                ctx.listing,
                {"name": user.display_name},
            )
        return await self.card(telegram_uid, application_id)

    # ------------------------------------------------------------- власник

    async def incoming(
        self, telegram_uid: int, property_id: uuid.UUID | None = None
    ) -> list[ApplicationOut]:
        """Заявки на оголошення власника (усі або одного об'єкта): відкриті спершу."""
        user = await self._users.require_active(telegram_uid)
        props = await self._properties.list_mine(telegram_uid)
        if property_id is not None:
            props = [p for p in props if p.id == property_id]
        # Усі оголошення об'єктів (і зняті, і здані) — щоб історія заявок не губилась.
        listing_ids = await self._listings.ids_on_units([u.id for p in props for u in p.units])
        async with self._tx.begin() as tx:
            records = await self._repo(tx).by_listings(listing_ids)
        outs = await self._outs(user, records)
        return sorted(outs, key=lambda a: (a.status not in OPEN, -a.created_at.timestamp()))

    async def approve(self, telegram_uid: int, application_id: uuid.UUID) -> ApplicationCard:
        """FR-APP-2: відкриваються контакти, точна адреса й чат заявки."""
        user = await self._users.require_active(telegram_uid)
        ctx = await self._landlord_ctx(user, application_id)
        now = self._clock.now()
        await self._move(
            ctx, ApplicationStatus.APPROVED, user.id, contacts_revealed_at=now, decided_at=now
        )
        async with self._tx.begin() as tx:
            conversation = await self._chat.ensure_for_application(tx, ctx.app.id)
            await self._chat.system(tx, conversation, SystemCode.APPLICATION_APPROVED)
            await self._notify(
                tx,
                ctx.app.tenant_user_id,
                "approved",
                ctx.app.id,
                ctx.prop,
                ctx.listing,
                {"name": user.display_name},
            )
        return await self.card(telegram_uid, application_id)

    async def decline(
        self, telegram_uid: int, application_id: uuid.UUID, data: DeclineIn
    ) -> ApplicationCard:
        """Ввічлива відмова без деталей; причина — для статистики й тексту «вже здано»."""
        user = await self._users.require_active(telegram_uid)
        ctx = await self._landlord_ctx(user, application_id)
        await self._decline(ctx, data.reason or DeclineReason.OTHER, user.id)
        return await self.card(telegram_uid, application_id)

    async def create_lease(
        self, telegram_uid: int, application_id: uuid.UUID, data: LeaseConditions
    ) -> LeaseOut:
        """FR-APP-6: оренда з погодженої заявки — заявник одразу відповідальний орендар."""
        user = await self._users.require_active(telegram_uid)
        ctx = await self._landlord_ctx(user, application_id)
        APPLICATION.transition(ctx.app.status, ApplicationStatus.LEASE_CREATED)
        self._converting.add(ctx.app.id)
        try:
            lease = await self._leases.create_from_application(
                telegram_uid,
                LeaseCreate(property_id=ctx.prop.id, **data.model_dump()),
                tenant_user_id=ctx.app.tenant_user_id,
                application_id=ctx.app.id,
            )
            await self._move(ctx, ApplicationStatus.LEASE_CREATED, user.id, lease_id=lease.id)
        finally:
            self._converting.discard(ctx.app.id)
        return lease

    # ---------------------------------------------------------------- обидві

    async def card(self, telegram_uid: int, application_id: uuid.UUID) -> ApplicationCard:
        user = await self._users.require_active(telegram_uid)
        ctx = await self._ctx(user, application_id)
        out = await self._out(user, ctx)
        status = ctx.app.status
        revealed = status in REVEALED
        contacts = None
        if revealed:
            other_id = ctx.app.tenant_user_id if ctx.role == "landlord" else ctx.prop.owner_user_id
            contacts = ContactsOut(
                phone=await self._users.contact_phone(other_id),
                house_number=ctx.prop.house_number,
                apartment=ctx.prop.apartment,
                entrance=ctx.prop.entrance,
                floor=ctx.prop.floor,
            )
        landlord = ctx.role == "landlord"
        return ApplicationCard(
            **out.model_dump(),
            contacts=contacts,
            can_approve=landlord and status is ApplicationStatus.PENDING,
            can_decline=landlord and APPLICATION.can(status, ApplicationStatus.DECLINED),
            can_withdraw=not landlord and APPLICATION.can(status, ApplicationStatus.WITHDRAWN),
            can_create_lease=landlord and APPLICATION.can(status, ApplicationStatus.LEASE_CREATED),
            can_chat=revealed,
        )

    async def messages(
        self,
        telegram_uid: int,
        application_id: uuid.UUID,
        *,
        after_id: int = 0,
        before_id: int | None = None,
    ) -> MessagesPage:
        user = await self._users.require_active(telegram_uid)
        ctx = await self._ctx(user, application_id)
        self._require_chat(ctx)
        async with self._tx.begin() as tx:
            conversation = await self._chat.ensure_for_application(tx, application_id)
            return await self._chat.page(
                tx, conversation, user.id, after_id=after_id, before_id=before_id
            )

    async def post_message(
        self, telegram_uid: int, application_id: uuid.UUID, body: str
    ) -> MessageOut:
        user = await self._users.require_active(telegram_uid)
        ctx = await self._ctx(user, application_id)
        self._require_chat(ctx)
        if ctx.app.status not in OPEN:
            raise Conflict("Заявку вже закрито", field="status")
        other = ctx.prop.owner_user_id if ctx.role == "tenant" else ctx.app.tenant_user_id
        async with self._tx.begin() as tx:
            conversation = await self._chat.ensure_for_application(tx, application_id)
            message = await self._chat.post(tx, conversation, user.id, body)
            bucket = int(self._clock.now().timestamp() // (CHAT_NOTIFY_BUCKET_MIN * 60))
            await self._notify(
                tx,
                other,
                "message",
                ctx.app.id,
                ctx.prop,
                ctx.listing,
                {
                    "name": user.display_name,
                    "preview": message.body[:120] if message.body else "",
                },
                dedupe=f"application:{ctx.app.id}:message:{other}:{bucket}",
            )
        return message

    async def mark_read(
        self, telegram_uid: int, application_id: uuid.UUID, message_id: int
    ) -> None:
        user = await self._users.require_active(telegram_uid)
        ctx = await self._ctx(user, application_id)
        self._require_chat(ctx)
        async with self._tx.begin() as tx:
            conversation = await self._chat.ensure_for_application(tx, application_id)
            await self._chat.mark_read(tx, conversation, user.id, message_id)

    # ---------------------------------------------------- воркер і хуки

    async def expire_due(self) -> int:
        """FR-APP-3: власник мовчить 72 год — заявка закривається, орендар дізнається."""
        now = self._clock.now()
        async with self._tx.begin() as tx:
            due = await self._repo(tx).expired_pending(now)
        done = 0
        for app in due:
            try:
                listing = (await self._listings.by_ids([app.listing_id]))[0]
                prop = await self._prop(listing.property_id)
            except (IndexError, NotFound):
                continue
            ctx = _Ctx(app, listing, prop, "landlord")
            await self._move(ctx, ApplicationStatus.EXPIRED, None)
            async with self._tx.begin() as tx:
                await self._notify(tx, app.tenant_user_id, "expired", app.id, prop, listing, {})
            done += 1
        return done

    async def on_listings_rented(self, listing_ids: list[uuid.UUID]) -> None:
        """Оголошення здано: інші відкриті заявки закриваються «вже здано»."""
        async with self._tx.begin() as tx:
            open_apps = await self._repo(tx).open_on_listings(listing_ids)
        listings = {x.id: x for x in await self._listings.by_ids(listing_ids)}
        for app in open_apps:
            listing = listings.get(app.listing_id)
            if listing is None or app.id in self._converting:
                continue
            prop = await self._prop(listing.property_id)
            ctx = _Ctx(app, listing, prop, "landlord")
            await self._decline(ctx, DeclineReason.RENTED, None)

    # --------------------------------------------------------------- службове

    async def _decline(self, ctx: _Ctx, reason: DeclineReason, actor: uuid.UUID | None) -> None:
        await self._move(
            ctx,
            ApplicationStatus.DECLINED,
            actor,
            decline_reason=reason.value,
            decided_at=self._clock.now(),
        )
        async with self._tx.begin() as tx:
            await self._notify(
                tx,
                ctx.app.tenant_user_id,
                "declined",
                ctx.app.id,
                ctx.prop,
                ctx.listing,
                {"reason": reason.value},
            )

    async def _move(
        self,
        ctx: _Ctx,
        target: ApplicationStatus,
        actor: uuid.UUID | None,
        **values: Any,
    ) -> None:
        APPLICATION.transition(ctx.app.status, target)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            fresh = await repo.get(ctx.app.id)
            if fresh is None or fresh.status is not ctx.app.status:
                raise Conflict("Заявку щойно змінено", field="status")
            await repo.update(ctx.app.id, status=target, **values)
            await repo.add_history(ctx.app.id, ctx.app.status, target, actor, now)

    async def _ctx(self, user: UserOut, application_id: uuid.UUID) -> _Ctx:
        """Чужа заявка виглядає як неіснуюча."""
        async with self._tx.begin() as tx:
            app = await self._repo(tx).get(application_id)
        if app is None:
            raise NotFound("Заявку не знайдено")
        found = await self._listings.by_ids([app.listing_id])
        if not found:
            raise NotFound("Заявку не знайдено")
        listing = found[0]
        prop = await self._prop(listing.property_id)
        if app.tenant_user_id == user.id:
            role = "tenant"
        elif prop.owner_user_id == user.id:
            role = "landlord"
        else:
            raise NotFound("Заявку не знайдено")
        return _Ctx(app, listing, prop, role)

    async def _landlord_ctx(self, user: UserOut, application_id: uuid.UUID) -> _Ctx:
        ctx = await self._ctx(user, application_id)
        if ctx.role != "landlord":
            raise PermissionDenied("Рішення ухвалює власник")
        return ctx

    @staticmethod
    def _require_chat(ctx: _Ctx) -> None:
        if ctx.app.status not in REVEALED:
            raise PermissionDenied("Чат з'явиться після схвалення заявки", reason="not_approved")

    async def _prop(self, property_id: uuid.UUID) -> PropertyOut:
        prop = await self._properties.property_by_id(property_id)
        if prop is None:
            raise NotFound("Оголошення не знайдено")
        return prop

    async def _outs(self, user: UserOut, records: list[ApplicationRecord]) -> list[ApplicationOut]:
        if not records:
            return []
        listings = {x.id: x for x in await self._listings.by_ids([r.listing_id for r in records])}
        out: list[ApplicationOut] = []
        for app in records:
            listing = listings.get(app.listing_id)
            if listing is None:
                continue
            prop = await self._properties.property_by_id(listing.property_id)
            if prop is None:
                continue
            role = "tenant" if app.tenant_user_id == user.id else "landlord"
            out.append(await self._out(user, _Ctx(app, listing, prop, role)))
        return out

    async def _out(self, user: UserOut, ctx: _Ctx) -> ApplicationOut:
        other_id = ctx.prop.owner_user_id if ctx.role == "tenant" else ctx.app.tenant_user_id
        other = await self._users.party_profile(other_id)
        if other is None:
            raise NotFound("Заявку не знайдено")
        photos = await self._properties.photos_of(ctx.prop.id)
        unread = 0
        if ctx.app.status in REVEALED:
            async with self._tx.begin() as tx:
                conversation = await self._chat.conversation_for_application(tx, ctx.app.id)
                if conversation is not None:
                    unread = await self._chat.unread(tx, conversation, user.id)
        return ApplicationOut(
            id=ctx.app.id,
            status=ctx.app.status,
            my_role="tenant" if ctx.role == "tenant" else "landlord",
            message=ctx.app.message,
            decline_reason=DeclineReason(ctx.app.decline_reason)
            if ctx.app.decline_reason
            else None,
            created_at=ctx.app.created_at,
            decided_at=ctx.app.decided_at,
            expires_at=ctx.app.expires_at,
            listing=ListingBrief(
                listing_id=ctx.listing.id,
                property_id=ctx.prop.id,
                status=ctx.listing.status,
                price_amount=ctx.listing.price_amount,
                utilities_included=ctx.listing.utilities_included,
                rooms=ctx.prop.rooms,
                area=ctx.prop.area,
                district=ctx.prop.district,
                street=ctx.prop.street,
                cover_photo_id=photos[0].id if photos else None,
            ),
            other=other,
            unread=unread,
            lease_id=ctx.app.lease_id,
        )

    async def _notify(
        self,
        tx: Tx,
        user_id: uuid.UUID,
        event: str,
        application_id: uuid.UUID,
        prop: PropertyOut,
        listing: ListingOut,
        params: dict[str, object],
        *,
        dedupe: str | None = None,
    ) -> None:
        """Адреса в тексті — лише вулиця (до схвалення будинку не видно)."""
        await self._notifications.enqueue(
            tx,
            user_id,
            NotificationType.APPLICATION,
            {
                "application_id": str(application_id),
                "event": event,
                "street": prop.street,
                "rooms": prop.rooms,
                "price": listing.price_amount,
                **params,
            },
            dedupe_key=dedupe or f"application:{application_id}:{event}:{user_id}",
        )
