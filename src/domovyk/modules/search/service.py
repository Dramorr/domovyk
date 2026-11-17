"""Публічний інтерфейс модуля search: пошук оголошень і обране (FR-SRC-1..6, ADR 0019).

Повного каталогу немає: спершу фільтр, потім результати (FR-SRC-1). Оголошення й
анкета беруться через сервіси listings і properties — чужі таблиці тут не читаємо.
Адреса для орендаря — лише вулиця й район; будинок, квартира й поверх відкриються
після схвалення заявки (FR-LST-4).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from domovyk.core.errors import InvalidInput, NotFound
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.listings import ListingStatus
from domovyk.domain.search import Amenity, Candidate, clean_criteria, matches
from domovyk.modules.listings.service import ListingOut, ListingsService
from domovyk.modules.properties.service import PropertiesService, PropertyOut
from domovyk.modules.search.repository import SearchRepository
from domovyk.modules.search.schemas import (
    ListingCard,
    ListingSummary,
    OwnerPublic,
    SearchIn,
    SearchOptions,
    SearchPage,
)
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "ListingCard",
    "ListingSummary",
    "SearchConfig",
    "SearchIn",
    "SearchOptions",
    "SearchPage",
    "SearchService",
]


@dataclass(frozen=True)
class SearchConfig:
    page_size: int = 20
    default_city: str = "lviv"


class SearchService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], SearchRepository],
        users: UsersService,
        properties: PropertiesService,
        listings: ListingsService,
        config: SearchConfig,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._listings = listings
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
            photos=await self._properties.photos_of(prop.id),
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

    # --------------------------------------------------------------- службове

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
            completeness=prop.completeness.percent if prop.completeness else 0,
            cover_photo_id=photos[0].id if photos else None,
            photos_count=len(photos),
            available_from=listing.available_from,
            published_at=listing.published_at,
            is_favorite=listing.id in favorites,
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
