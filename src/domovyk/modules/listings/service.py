"""Публічний інтерфейс модуля listings: оголошення власника (FR-LST-1..5, ADR 0018).

Оголошення — умови угоди поверх анкети об'єкта. Публікація — лише з підтвердженою
власністю й анкетою від мінімуму (правило 5, FR-PRP-8), з галочкою «без комісії»
(FR-LST-2) і коли на об'єкті немає оренди. Підозріле (FR-LST-5) не блокує, але не
накопичується тихо: risk_signal як доказ і одразу сповіщення модераторам.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.errors import (
    DeclarationRequired,
    HasLiveListing,
    NotFound,
    UnitBusy,
)
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.listings import (
    LISTING,
    PUBLISHED,
    ListingStatus,
    clean_available_from,
    clean_deposit,
    clean_min_term,
    clean_price,
    find_risk_words,
)
from domovyk.domain.properties import OwnershipStatus, UnitKind
from domovyk.modules.analytics.service import AnalyticsService, EventName, EventSource
from domovyk.modules.leases.service import LeasesService
from domovyk.modules.listings.repository import ListingRecord, ListingsRepository, ListingTerms
from domovyk.modules.listings.schemas import (
    ListingIn,
    ListingOut,
    ListingReadiness,
    ListingState,
    PublishIn,
)
from domovyk.modules.moderation.service import ModerationService, RiskSignal
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.properties.service import PropertiesService, PropertyOut
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "ListingIn",
    "ListingOut",
    "ListingReadiness",
    "ListingState",
    "ListingsConfig",
    "ListingsService",
    "PublishIn",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListingsConfig:
    # FR-LST-5, «багато об'єктів на різних адресах за короткий час»: N адрес за D днів.
    many_addresses_count: int = 3
    many_addresses_days: int = 30


class ListingsService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], ListingsRepository],
        users: UsersService,
        properties: PropertiesService,
        leases: LeasesService,
        moderation: ModerationService,
        notifications: NotificationsService,
        analytics: AnalyticsService,
        clock: Clock,
        config: ListingsConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._leases = leases
        self._moderation = moderation
        self._notifications = notifications
        self._analytics = analytics
        self._clock = clock
        self._config = config

    # ----------------------------------------------------------------- власник

    async def state(self, telegram_uid: int, property_id: uuid.UUID) -> ListingState:
        """LST-EDIT: живе оголошення об'єкта (або останнє з історії) і що бракує."""
        prop = await self._properties.get_mine(telegram_uid, property_id)
        async with self._tx.begin() as tx:
            listing = await self._repo(tx).latest_on_units(_unit_ids(prop))
        return await self._state(prop, listing)

    async def mine(self, telegram_uid: int) -> list[ListingOut]:
        """Оголошення власника — для позначок у списку об'єктів."""
        user = await self._users.require_active(telegram_uid)
        props = {
            u.id: p.id for p in await self._properties.list_mine(telegram_uid) for u in p.units
        }
        async with self._tx.begin() as tx:
            records = await self._repo(tx).by_author(user.id)
        return [_out(r, props[r.unit_id]) for r in records if r.unit_id in props]

    async def save(
        self, telegram_uid: int, property_id: uuid.UUID, data: ListingIn
    ) -> ListingState:
        """Зберегти умови. Немає живого оголошення — з'являється чернетка."""
        user = await self._users.require_active(telegram_uid)
        prop = await self._properties.get_mine(telegram_uid, property_id)
        terms = ListingTerms(
            price_amount=clean_price(data.price_amount),
            utilities_included=data.utilities_included,
            deposit_amount=clean_deposit(data.deposit_amount),
            min_term_months=clean_min_term(data.min_term_months),
            available_from=clean_available_from(
                data.available_from, to_kyiv(self._clock.now()).date()
            ),
        )
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            live = await repo.live_on_units(_unit_ids(prop))
            if live:
                await repo.set_terms(live[0].id, terms)
                listing_id = live[0].id
            else:
                listing_id = await repo.create(_entire(prop), user.id, terms)
            listing = await repo.get(listing_id)
        return await self._state(prop, listing)

    async def publish(
        self,
        telegram_uid: int,
        property_id: uuid.UUID,
        data: PublishIn,
        *,
        source: EventSource = EventSource.MINI_APP,
    ) -> ListingState:
        """Опублікувати чернетку або повернути з паузи (FR-LST-2..5)."""
        user = await self._users.require_active(telegram_uid)
        prop = await self._properties.get_mine(telegram_uid, property_id)
        listing = await self._live(prop)
        if listing.status is ListingStatus.ACTIVE:
            return await self._state(prop, listing)
        LISTING.transition(listing.status, ListingStatus.ACTIVE)
        if listing.owner_declaration_at is None and not data.owner_declaration:
            raise DeclarationRequired("Потрібне підтвердження власника", field="owner_declaration")
        # Правило 5 і FR-PRP-8: власність підтверджена, анкета не порожня.
        prop = await self._properties.require_listable(user.id, property_id)
        if await self._leases.busy_unit_ids(_unit_ids(prop)):
            raise UnitBusy("На об'єкті вже є оренда", field="unit_id")
        now = self._clock.now()
        first = listing.published_at is None
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.update(
                listing.id,
                status=ListingStatus.ACTIVE,
                owner_declaration_at=listing.owner_declaration_at or now,
                published_at=listing.published_at or now,
            )
            if first:
                await self._analytics.record(
                    tx, EventName.LISTING_PUBLISHED, user_id=user.id, source=source
                )
            fresh = await repo.get(listing.id)
        await self._check_risks(user, prop, listing.id)
        return await self._state(prop, fresh)

    async def pause(self, telegram_uid: int, property_id: uuid.UUID) -> ListingState:
        """FR-LST-3: тимчасово сховати з пошуку."""
        return await self._move(telegram_uid, property_id, ListingStatus.PAUSED)

    async def close(self, telegram_uid: int, property_id: uuid.UUID) -> ListingState:
        """Зняти оголошення. Чернетка просто видаляється — історії в неї немає."""
        prop = await self._properties.get_mine(telegram_uid, property_id)
        listing = await self._live(prop)
        if listing.status is ListingStatus.DRAFT:
            async with self._tx.begin() as tx:
                repo = self._repo(tx)
                await repo.delete(listing.id)
                latest = await repo.latest_on_units(_unit_ids(prop))
            return await self._state(prop, latest)
        return await self._move(telegram_uid, property_id, ListingStatus.ARCHIVED)

    # ------------------------------------------------------- для інших модулів

    async def active(self) -> list[ListingOut]:
        """Опубліковані оголошення, новіші спершу — для пошуку (FR-SRC-5)."""
        async with self._tx.begin() as tx:
            records = await self._repo(tx).with_status([ListingStatus.ACTIVE])
        return await self._with_properties(records)

    async def by_ids(self, listing_ids: list[uuid.UUID]) -> list[ListingOut]:
        """Будь-які стани (обране показує «вже неактуальне»). Права — на модулі, що кличе."""
        async with self._tx.begin() as tx:
            records = await self._repo(tx).many(listing_ids)
        return await self._with_properties(records)

    async def author_of(self, listing_id: uuid.UUID) -> uuid.UUID | None:
        async with self._tx.begin() as tx:
            record = await self._repo(tx).get(listing_id)
        return record.created_by if record else None

    async def _with_properties(self, records: list[ListingRecord]) -> list[ListingOut]:
        props = await self._properties.property_ids_of_units([r.unit_id for r in records])
        return [_out(r, props[r.unit_id]) for r in records if r.unit_id in props]

    async def on_units_opened(self, unit_ids: list[uuid.UUID]) -> None:
        """Оренда на об'єкті: опубліковане оголошення стає «здано» (FR-LST-3)."""
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            for listing in await repo.live_on_units(unit_ids):
                if listing.status in PUBLISHED:
                    LISTING.transition(listing.status, ListingStatus.RENTED)
                    await repo.update(listing.id, status=ListingStatus.RENTED, closed_at=now)

    async def guard_property_delete(self, prop: PropertyOut) -> None:
        """FR-PRP-9: об'єкт з опублікованим оголошенням не видаляють. Чернетка зникає з ним."""
        async with self._tx.begin() as tx:
            live = await self._repo(tx).live_on_units(_unit_ids(prop))
        if any(item.status in PUBLISHED for item in live):
            raise HasLiveListing("Спершу зніми оголошення")

    # --------------------------------------------------------------- службове

    async def _move(
        self, telegram_uid: int, property_id: uuid.UUID, target: ListingStatus
    ) -> ListingState:
        prop = await self._properties.get_mine(telegram_uid, property_id)
        listing = await self._live(prop)
        LISTING.transition(listing.status, target)
        values: dict[str, Any] = {"status": target}
        if target is ListingStatus.ARCHIVED:
            values["closed_at"] = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.update(listing.id, **values)
            # Після зняття показуємо щойно зняте: власник бачить «знято» й «нове оголошення».
            fresh = await repo.get(listing.id)
        return await self._state(prop, fresh)

    async def _live(self, prop: PropertyOut) -> ListingRecord:
        async with self._tx.begin() as tx:
            live = await self._repo(tx).live_on_units(_unit_ids(prop))
        if not live:
            raise NotFound("Оголошення не знайдено")
        return live[0]

    async def _state(self, prop: PropertyOut, listing: ListingRecord | None) -> ListingState:
        completeness = prop.completeness
        percent = completeness.percent if completeness else 0
        minimum = completeness.publish_min if completeness else 0
        profile_ok = bool(completeness and completeness.publishable)
        verified = prop.ownership_status is OwnershipStatus.VERIFIED
        busy = bool(await self._leases.busy_unit_ids(_unit_ids(prop)))
        photos = len(await self._properties.photos_of(prop.id))
        return ListingState(
            property_id=prop.id,
            listing=_out(listing, prop.id) if listing else None,
            readiness=ListingReadiness(
                ownership_verified=verified,
                completeness_percent=percent,
                completeness_min=minimum,
                profile_ok=profile_ok,
                photos=photos,
                unit_busy=busy,
                ready=verified and profile_ok and not busy,
            ),
        )

    async def _check_risks(self, user: UserOut, prop: PropertyOut, listing_id: uuid.UUID) -> None:
        """FR-LST-5: підозріле — доказ у risk_signals і одразу модераторам."""
        found: list[tuple[RiskSignal, dict[str, Any]]] = []
        async with self._tx.begin() as tx:
            stems = await self._moderation.risk_words(tx)
        words = find_risk_words(prop.details.description or "", stems)
        if words:
            found.append((RiskSignal.COMMISSION_KEYWORDS, {"words": words}))
        twins = await self._properties.photo_twins(prop.id)
        if twins:
            found.append(
                (RiskSignal.DUPLICATE_PHOTOS, {"other_properties": [str(t) for t in twins]})
            )
        since = self._clock.now() - timedelta(days=self._config.many_addresses_days)
        async with self._tx.begin() as tx:
            units = await self._repo(tx).published_units_since(user.id, since)
        if units >= self._config.many_addresses_count:
            found.append(
                (
                    RiskSignal.MANY_ADDRESSES,
                    {"units": units, "days": self._config.many_addresses_days},
                )
            )
        if not found:
            return
        staff = await self._users.staff_ids()
        async with self._tx.begin() as tx:
            for signal, details in found:
                await self._moderation.add_risk_signal(
                    tx, user.id, signal, {"listing_id": str(listing_id), **details}
                )
                for staff_id in staff:
                    if staff_id == user.id:
                        continue
                    await self._notifications.enqueue(
                        tx,
                        staff_id,
                        NotificationType.MODERATION_ALERT,
                        {
                            "reason": signal.value,
                            "user_name": user.display_name,
                            "telegram_uid": user.telegram_uid,
                        },
                        dedupe_key=f"moderation_alert:{signal.value}:{listing_id}:{staff_id}",
                    )
        log.info("Оголошення %s: сигнали ризику %s", listing_id, [s.value for s, _ in found])


def _unit_ids(prop: PropertyOut) -> list[uuid.UUID]:
    return [u.id for u in prop.units]


def _entire(prop: PropertyOut) -> uuid.UUID:
    """Поки здаємо лише об'єкт цілком (ADR 0013)."""
    unit = next((u for u in prop.units if u.kind is UnitKind.ENTIRE), None)
    if unit is None:
        raise NotFound("Юніт не знайдено")
    return unit.id


def _out(r: ListingRecord, property_id: uuid.UUID) -> ListingOut:
    return ListingOut(
        id=r.id,
        property_id=property_id,
        unit_id=r.unit_id,
        status=r.status,
        price_amount=r.price_amount,
        currency="UAH",
        utilities_included=r.utilities_included,
        deposit_amount=r.deposit_amount,
        min_term_months=r.min_term_months,
        available_from=r.available_from,
        owner_declared=r.owner_declaration_at is not None,
        published_at=r.published_at,
        closed_at=r.closed_at,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )
