"""Публічний інтерфейс модуля search: пошук, обране, збережені пошуки (FR-SRC-1..6, ADR 0019).

Без фільтра — усе житло міста, новіші спершу (FR-SRC-1). Оголошення й анкета беруться
через сервіси listings і properties — чужі таблиці тут не читаємо. Адреса для орендаря —
вулиця й будинок; квартира, під'їзд і поверх відкриються після схвалення заявки
(FR-LST-4, ADR 0021).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.errors import Conflict, InvalidInput, NotFound
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.listings import ListingStatus
from domovyk.domain.search import Amenity, Candidate, Criteria, clean_criteria, matches
from domovyk.modules.analytics.service import AnalyticsService, EventName, EventSource
from domovyk.modules.listings.service import ListingOut, ListingsService
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.properties.service import PropertiesService, PropertyOut
from domovyk.modules.search.repository import FilterRecord, SearchRepository
from domovyk.modules.search.schemas import (
    ListingCard,
    ListingSummary,
    OwnerPublic,
    SavedSearchIn,
    SavedSearchOut,
    SearchIn,
    SearchOptions,
    SearchPage,
)
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "ListingCard",
    "ListingSummary",
    "SavedSearchIn",
    "SavedSearchOut",
    "SearchConfig",
    "SearchIn",
    "SearchOptions",
    "SearchPage",
    "SearchService",
]


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchConfig:
    page_size: int = 20
    default_city: str = "lviv"
    # FR-SRC-3: збережених пошуків на людину; миттєвих сповіщень на день, далі — дайджест.
    max_saved: int = 10
    daily_match_limit: int = 10
    digest_hour: int = 19  # за Києвом


class SearchService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], SearchRepository],
        users: UsersService,
        properties: PropertiesService,
        listings: ListingsService,
        notifications: NotificationsService,
        analytics: AnalyticsService,
        clock: Clock,
        config: SearchConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._listings = listings
        self._notifications = notifications
        self._analytics = analytics
        self._clock = clock
        self._config = config

    # ------------------------------------------------------------------ пошук

    async def options(self, telegram_uid: int, city_code: str | None) -> SearchOptions:
        await self._users.require_active(telegram_uid)
        city = await self._city(city_code)
        return SearchOptions(
            city_code=city,
            districts=await self._properties.districts(city),
            completeness_min=self._properties.listing_min_completeness,
            amenities=list(Amenity),
        )

    async def search(self, telegram_uid: int, data: SearchIn) -> SearchPage:
        """SRC-RESULTS: новіші спершу (FR-SRC-5). Своїх оголошень у пошуку не показуємо."""
        user = await self._users.require_active(telegram_uid)
        city = await self._city(data.city_code)
        names = {d.id: d.name for d in await self._properties.districts(city)}
        criteria = self._criteria(data, names)
        listings = await self._listings.active()
        props = await self._props([x.property_id for x in listings])
        found = [
            (x, props[x.property_id])
            for x in listings
            if x.property_id in props
            and props[x.property_id].city_code == city
            and props[x.property_id].owner_user_id != user.id
            and matches(criteria, _candidate(x, props[x.property_id]))
        ]
        page = found[data.offset : data.offset + self._config.page_size]
        favorites = await self._favorite_set(user.id)
        return SearchPage(
            items=[await self._summary(x, p, favorites) for x, p in page],
            total=len(found),
            offset=data.offset,
            page_size=self._config.page_size,
        )

    async def card(self, telegram_uid: int, listing_id: uuid.UUID) -> ListingCard:
        """SRC-CARD: опубліковане бачать усі; інше — автор і ті, в кого воно в обраному."""
        user = await self._users.require_active(telegram_uid)
        listing, prop, favorites = await self._visible(user, listing_id)
        owner = await self._users.get_by_id(prop.owner_user_id)
        if owner is None:
            raise NotFound("Оголошення не знайдено")
        summary = await self._summary(listing, prop, favorites)
        return ListingCard(
            **summary.model_dump(),
            deposit_amount=listing.deposit_amount,
            min_term_months=listing.min_term_months,
            floors_total=prop.details.floors_total,
            details=prop.details,
            owner=OwnerPublic(
                user_id=owner.id,
                display_name=owner.display_name,
                member_since=owner.created_at,
                avatar_version=owner.avatar_version,
            ),
            is_mine=prop.owner_user_id == user.id,
        )

    async def photo(self, telegram_uid: int, listing_id: uuid.UUID, photo_id: uuid.UUID) -> bytes:
        user = await self._users.require_active(telegram_uid)
        _, prop, _ = await self._visible(user, listing_id)
        return await self._properties.photo_data(prop.id, photo_id)

    # ---------------------------------------------------------------- обране

    async def add_favorite(self, telegram_uid: int, listing_id: uuid.UUID) -> None:
        """FR-SRC-4: зберегти можна лише те, що зараз видно в пошуку."""
        user = await self._users.require_active(telegram_uid)
        found = await self._listings.by_ids([listing_id])
        if not found or found[0].status is not ListingStatus.ACTIVE:
            raise NotFound("Оголошення не знайдено")
        async with self._tx.begin() as tx:
            await self._repo(tx).add_favorite(user.id, listing_id)

    async def remove_favorite(self, telegram_uid: int, listing_id: uuid.UUID) -> None:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            await self._repo(tx).remove_favorite(user.id, listing_id)

    async def favorites(self, telegram_uid: int) -> list[ListingSummary]:
        """FAV-LIST: новіші додані спершу; зняте чи здане — з позначкою «неактуальне»."""
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            ids = await self._repo(tx).favorite_ids(user.id)
        by_id = {x.id: x for x in await self._listings.by_ids(ids)}
        props = await self._props([x.property_id for x in by_id.values()])
        favorites = set(ids)
        out: list[ListingSummary] = []
        for listing_id in ids:
            listing = by_id.get(listing_id)
            if listing is not None and listing.property_id in props:
                out.append(await self._summary(listing, props[listing.property_id], favorites))
        return out

    # ------------------------------------------------- збережені пошуки (FR-SRC-3)

    async def saved(self, telegram_uid: int) -> list[SavedSearchOut]:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            records = await self._repo(tx).filters(user.id)
        return [_saved_out(r) for r in records]

    async def save_search(
        self, telegram_uid: int, data: SavedSearchIn, *, source: EventSource = EventSource.MINI_APP
    ) -> SavedSearchOut:
        """SRC-SAVE: фільтр перевіряємо так само, як пошук, і зберігаємо його JSON."""
        user = await self._users.require_active(telegram_uid)
        city = await self._city(data.criteria.city_code)
        names = {d.id: d.name for d in await self._properties.districts(city)}
        self._criteria(data.criteria, names)
        criteria = data.criteria.model_copy(update={"city_code": city, "offset": 0})
        name = " ".join(data.name.split())
        if not name:
            raise InvalidInput("Порожня назва", field="name")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if await repo.count_filters(user.id) >= self._config.max_saved:
                raise Conflict("Забагато збережених пошуків", field="saved", reason="limit")
            filter_id = await repo.add_filter(
                user.id, name, city, criteria.model_dump(mode="json"), data.notify
            )
            await self._analytics.record(tx, EventName.FILTER_SAVED, user_id=user.id, source=source)
            record = await self._own_filter(repo, filter_id, user.id)
        return _saved_out(record)

    async def set_notify(
        self, telegram_uid: int, filter_id: uuid.UUID, notify: bool
    ) -> SavedSearchOut:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._own_filter(repo, filter_id, user.id)
            await repo.update_filter(filter_id, notify=notify)
            record = await self._own_filter(repo, filter_id, user.id)
        return _saved_out(record)

    async def delete_search(self, telegram_uid: int, filter_id: uuid.UUID) -> None:
        user = await self._users.require_active(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await self._own_filter(repo, filter_id, user.id)
            await repo.delete_filter(filter_id)

    async def on_published(self, listing_id: uuid.UUID) -> int:
        """Нове оголошення → сповіщення тим, чий збережений пошук під нього підходить.

        Одне оголошення — одне сповіщення людині, навіть якщо підійшло кілька її пошуків.
        Понад денний ліміт — один вечірній дайджест. Уночі — після тихих годин.
        """
        found = await self._listings.by_ids([listing_id])
        if not found or found[0].status is not ListingStatus.ACTIVE:
            return 0
        listing = found[0]
        prop = (await self._props([listing.property_id])).get(listing.property_id)
        if prop is None:
            return 0
        names = {d.id: d.name for d in await self._properties.districts(prop.city_code)}
        candidate = _candidate(listing, prop)
        async with self._tx.begin() as tx:
            filters = await self._repo(tx).notify_filters(prop.city_code)
        first: dict[uuid.UUID, FilterRecord] = {}
        for f in sorted(filters, key=lambda x: x.created_at):
            if f.user_id == prop.owner_user_id or f.user_id in first:
                continue
            try:
                criteria = self._criteria(SearchIn.model_validate(f.criteria), names)
            except (InvalidInput, ValidationError):
                log.warning("Збережений пошук %s не читається — пропускаю", f.id)
                continue
            if matches(criteria, candidate):
                first[f.user_id] = f
        now = self._clock.now()
        sent = 0
        for user_id, f in first.items():
            async with self._tx.begin() as tx:
                if await self._notify_match(tx, user_id, f, listing, prop, now):
                    sent += 1
        return sent

    async def _notify_match(
        self,
        tx: Tx,
        user_id: uuid.UUID,
        f: FilterRecord,
        listing: ListingOut,
        prop: PropertyOut,
        now: datetime,
    ) -> bool:
        local = to_kyiv(now)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        today = await self._notifications.count_since(
            tx, user_id, NotificationType.NEW_MATCH, day_start.astimezone(now.tzinfo)
        )
        if today >= self._config.daily_match_limit:
            hour = self._config.digest_hour
            evening = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            send_after = evening.astimezone(now.tzinfo)
            if send_after < now:
                send_after = self._notifications.after_quiet_hours(now)
            return await self._notifications.enqueue(
                tx,
                user_id,
                NotificationType.NEW_MATCH,
                {"digest": True},
                dedupe_key=f"new_match_digest:{user_id}:{local.date().isoformat()}",
                send_after=send_after,
            )
        added = await self._notifications.enqueue(
            tx,
            user_id,
            NotificationType.NEW_MATCH,
            {
                "listing_id": str(listing.id),
                "search_name": f.name,
                "price": listing.price_amount,
                "rooms": prop.rooms,
                "district": prop.district,
                "street": prop.street,
            },
            dedupe_key=f"new_match:{user_id}:{listing.id}",
            send_after=self._notifications.after_quiet_hours(now),
        )
        if added:
            await self._analytics.record(
                tx, EventName.MATCH_NOTIFIED, user_id=user_id, source=EventSource.API
            )
        return added

    # --------------------------------------------------------------- службове

    def _criteria(self, data: SearchIn, names: dict[int, str]) -> Criteria:
        criteria = clean_criteria(
            kinds=data.kinds,
            districts=[names[i] for i in data.district_ids if i in names],
            rooms=data.rooms,
            price_min=data.price_min,
            price_max=data.price_max,
            area_min=float(data.area_min) if data.area_min is not None else None,
            floor_not_first=data.floor_not_first,
            floor_not_last=data.floor_not_last,
            pets=data.pets,
            children=data.children,
            registration=data.registration,
            amenities=data.amenities,
            completeness_min=data.completeness_min,
            completeness_floor=self._properties.listing_min_completeness,
        )
        if data.district_ids and not criteria.districts:
            raise InvalidInput("Невідомий район", field="district_ids")
        return criteria

    @staticmethod
    async def _own_filter(
        repo: SearchRepository, filter_id: uuid.UUID, user_id: uuid.UUID
    ) -> FilterRecord:
        record = await repo.filter(filter_id)
        if record is None or record.user_id != user_id:
            raise NotFound("Пошук не знайдено")
        return record

    async def _city(self, code: str | None) -> str:
        code = code or self._config.default_city
        active = {c.code for c in await self._properties.cities() if c.status == "active"}
        if code not in active:
            raise InvalidInput("У цьому місті Домовик поки не працює", field="city_code")
        return code

    async def _props(self, property_ids: list[uuid.UUID]) -> dict[uuid.UUID, PropertyOut]:
        return {p.id: p for p in await self._properties.properties_by_ids(property_ids)}

    async def _favorite_set(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        async with self._tx.begin() as tx:
            return set(await self._repo(tx).favorite_ids(user_id))

    async def _visible(
        self, user: UserOut, listing_id: uuid.UUID
    ) -> tuple[ListingOut, PropertyOut, set[uuid.UUID]]:
        """Чуже неопубліковане виглядає як неіснуюче — не розкриваємо, що воно є."""
        found = await self._listings.by_ids([listing_id])
        props = await self._props([x.property_id for x in found])
        if not found or found[0].property_id not in props:
            raise NotFound("Оголошення не знайдено")
        listing, prop = found[0], props[found[0].property_id]
        favorites = await self._favorite_set(user.id)
        allowed = (
            listing.status is ListingStatus.ACTIVE
            or prop.owner_user_id == user.id
            or listing.id in favorites
        )
        if not allowed:
            raise NotFound("Оголошення не знайдено")
        return listing, prop, favorites

    async def _summary(
        self, listing: ListingOut, prop: PropertyOut, favorites: set[uuid.UUID]
    ) -> ListingSummary:
        photos = await self._properties.photos_of(prop.id)
        return ListingSummary(
            id=listing.id,
            status=listing.status,
            actual=listing.status is ListingStatus.ACTIVE,
            price_amount=listing.price_amount,
            utilities_included=listing.utilities_included,
            kind=prop.kind,
            rooms=prop.rooms,
            area=prop.area,
            city=prop.city,
            district=prop.district,
            street=prop.street,
            house_number=prop.house_number,
            completeness=prop.completeness.percent if prop.completeness else 0,
            cover_photo_id=photos[0].id if photos else None,
            photos_count=len(photos),
            photos=photos,
            available_from=listing.available_from,
            published_at=listing.published_at,
            is_favorite=listing.id in favorites,
        )


def _saved_out(r: FilterRecord) -> SavedSearchOut:
    return SavedSearchOut(
        id=r.id,
        name=r.name,
        criteria=SearchIn.model_validate(r.criteria),
        notify=r.notify,
        created_at=r.created_at,
    )


def _candidate(listing: ListingOut, prop: PropertyOut) -> Candidate:
    return Candidate(
        kind=prop.kind,
        district=prop.district,
        rooms=prop.rooms,
        area=float(prop.area) if prop.area is not None else None,
        floor=prop.floor,
        price=listing.price_amount,
        completeness=prop.completeness.percent if prop.completeness else 0,
        details=prop.details.model_dump(mode="json"),
    )
