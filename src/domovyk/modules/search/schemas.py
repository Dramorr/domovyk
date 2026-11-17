"""DTO модуля search — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from domovyk.domain.listings import ListingStatus
from domovyk.domain.properties import PropertyKind
from domovyk.domain.search import Amenity
from domovyk.modules.properties.schemas import DistrictOut, PhotoOut, PropertyDetails


class SearchIn(BaseModel):
    """Фільтр орендаря (FR-SRC-2). Без фільтра каталогу немає (FR-SRC-1): місто — завжди."""

    city_code: str | None = Field(default=None, max_length=32)
    kinds: list[PropertyKind] = Field(default_factory=list, max_length=2)
    district_ids: list[int] = Field(default_factory=list, max_length=50)
    rooms: list[int] = Field(default_factory=list, max_length=5)  # 0 — студія, 4 — «4+»
    price_min: int | None = None
    price_max: int | None = None
    area_min: Decimal | None = None
    floor_not_first: bool = False
    floor_not_last: bool = False
    pets: bool = False
    children: bool = False
    registration: bool = False
    amenities: list[Amenity] = Field(default_factory=list, max_length=20)
    completeness_min: int = 0
    offset: int = Field(default=0, ge=0, le=10_000)


class SearchOptions(BaseModel):
    """Що потрібно формі фільтра: райони міста й нижня межа повзунка заповненості."""

    city_code: str
    districts: list[DistrictOut]
    completeness_min: int  # мінімум для публікації: повзунок від нього (FR-SRC-6)
    amenities: list[Amenity]


class ListingSummary(BaseModel):
    """Рядок у результатах і в обраному. Без номера будинку, квартири й поверху (FR-LST-4)."""

    id: uuid.UUID
    status: ListingStatus
    actual: bool  # опубліковане зараз; інакше — «вже неактуальне» (FR-SRC-4)
    price_amount: int
    utilities_included: bool
    kind: PropertyKind
    rooms: int
    area: Decimal | None
    city: str
    district: str | None
    street: str
    completeness: int
    cover_photo_id: uuid.UUID | None
    photos_count: int
    available_from: date | None
    published_at: datetime | None
    is_favorite: bool


class SearchPage(BaseModel):
    items: list[ListingSummary]
    total: int
    offset: int
    page_size: int


class OwnerPublic(BaseModel):
    """Що орендар бачить про власника до схвалення заявки (FR-APP-1): без контактів."""

    user_id: uuid.UUID
    display_name: str
    member_since: datetime
    avatar_version: str | None


class ListingCard(ListingSummary):
    """Картка оголошення для орендаря: умови, анкета, фото. Адреса — лише вулиця й район."""

    deposit_amount: int | None
    min_term_months: int | None
    floors_total: int | None
    details: PropertyDetails
    photos: list[PhotoOut]
    owner: OwnerPublic
    is_mine: bool


class SavedSearchIn(BaseModel):
    """Зберегти пошук (FR-SRC-3). Назву складає інтерфейс із фільтра, людина може змінити."""

    name: str = Field(min_length=1, max_length=60)
    criteria: SearchIn
    notify: bool = True


class SavedSearchOut(BaseModel):
    id: uuid.UUID
    name: str
    criteria: SearchIn
    notify: bool
    created_at: datetime
