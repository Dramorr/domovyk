"""DTO модуля properties — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from domovyk.domain.properties import (
    OwnershipStatus,
    PropertyKind,
    UnitKind,
    VerificationStatus,
)
from domovyk.domain.property_profile import (
    Accessibility,
    ApartmentBackupPower,
    Appliance,
    BathroomType,
    BlackoutReady,
    BuildingFeature,
    BuildingType,
    ChildrenPolicy,
    Comfort,
    CompletenessLevel,
    Furnishing,
    Heating,
    HotWater,
    Internet,
    Layout,
    Meter,
    Parking,
    PetsPolicy,
    Repair,
    Shelter,
    SmokingPolicy,
    Stove,
    WallMaterial,
    WindowsView,
)


class CityOut(BaseModel):
    """Місто з довідника (ADR 0008). active — працюємо; soon — скоро."""

    code: str
    name: str
    region: str
    status: Literal["active", "soon"]


class AddressCandidate(BaseModel):
    """Будинок, знайдений геокодером (PRP-ADDR-CONFIRM)."""

    city_code: str
    city: str
    street: str
    house_number: str
    district: str | None = None
    lat: float | None = None
    lon: float | None = None
    place_id: str | None = None


class AddressIn(BaseModel):
    """Адреса будинку: підтверджений кандидат геокодера або введена вручну."""

    # Код міста з довідника; None — типове місто з конфігурації.
    city_code: str | None = Field(default=None, max_length=32)
    street: str = Field(max_length=100)
    house_number: str = Field(max_length=20)
    district: str | None = Field(default=None, max_length=80)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    place_id: str | None = Field(default=None, max_length=100)


class PropertyCreate(BaseModel):
    kind: PropertyKind
    address: AddressIn
    apartment: str | None = Field(default=None, max_length=20)
    entrance: str | None = Field(default=None, max_length=20)
    floor: int | None = None
    rooms: int
    area: Decimal | None = None


class PropertyUpdate(BaseModel):
    """Що можна змінити без повторної перевірки. Адреса не змінюється (лише новий об'єкт)."""

    entrance: str | None = Field(default=None, max_length=20)
    floor: int | None = None
    rooms: int
    area: Decimal | None = None


class RoomIn(BaseModel):
    name: str = Field(max_length=100)
    area: Decimal | None = None


class PropertyDetails(BaseModel):
    """Анкета об'єкта (FR-PRP-8, ADR 0003). Усе необов'язкове: None — «ще не відповів».

    False, 0 чи порожній перелік — теж відповідь («ні», «нічого з переліку»).
    Анкета не фінальна: нові поля додаються зі збільшенням PROFILE_VERSION.
    """

    description: str | None = Field(default=None, max_length=3000)
    # будинок
    building_type: BuildingType | None = None
    wall_material: WallMaterial | None = None
    year_built: int | None = Field(default=None, ge=1700, le=2035)
    floors_total: int | None = Field(default=None, ge=1, le=60)
    has_elevator: bool | None = None
    parking: Parking | None = None
    shelter: Shelter | None = None
    building_features: list[BuildingFeature] | None = None
    # квартира
    area_living: Decimal | None = Field(default=None, gt=0, le=1000)
    area_kitchen: Decimal | None = Field(default=None, gt=0, le=200)
    layout: Layout | None = None
    bathroom: BathroomType | None = None
    ceiling_height: Decimal | None = Field(default=None, ge=2, le=8)
    balconies: int | None = Field(default=None, ge=0, le=6)
    windows_view: WindowsView | None = None
    repair: Repair | None = None
    first_rental: bool | None = None
    # інженерія й комуналка
    heating: Heating | None = None
    hot_water: HotWater | None = None
    stove: Stove | None = None
    internet: Internet | None = None
    meters: list[Meter] | None = None
    utilities_winter_uah: int | None = Field(default=None, ge=0, le=100_000)
    utilities_summer_uah: int | None = Field(default=None, ge=0, le=100_000)
    # блекаут
    apartment_backup_power: ApartmentBackupPower | None = None
    blackout_ready: list[BlackoutReady] | None = None
    # обладнання
    furnishing: Furnishing | None = None
    appliances: list[Appliance] | None = None
    comfort: list[Comfort] | None = None
    accessibility: list[Accessibility] | None = None
    # правила (типові для оголошень цього об'єкта)
    pets: PetsPolicy | None = None
    children: ChildrenPolicy | None = None
    smoking: SmokingPolicy | None = None
    max_occupants: int | None = Field(default=None, ge=1, le=20)
    residence_registration: bool | None = None

    @field_validator(
        "building_features", "meters", "blackout_ready", "appliances", "comfort", "accessibility"
    )
    @classmethod
    def _unique(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value is not None else None

    @field_validator("area_living", "area_kitchen", "ceiling_height")
    @classmethod
    def _two_decimals(cls, value: Decimal | None) -> Decimal | None:
        return value.quantize(Decimal("0.01")) if value is not None else None


class CompletenessOut(BaseModel):
    """Рівень заповненості анкети (FR-PRP-8)."""

    percent: int
    level: CompletenessLevel
    publish_min: int  # мінімум для оголошення, % (LISTING_MIN_COMPLETENESS)
    publishable: bool
    answered: int
    total: int
    missing_important: list[str]  # ключі полів PropertyDetails / базових полів
    sections: dict[str, int]


class UnitOut(BaseModel):
    id: uuid.UUID
    kind: UnitKind
    name: str  # для entire — порожньо, назву показує інтерфейс
    area: Decimal | None
    is_active: bool


class PropertyOut(BaseModel):
    id: uuid.UUID
    owner_user_id: uuid.UUID
    kind: PropertyKind
    city_code: str
    city: str
    street: str
    house_number: str
    district: str | None
    apartment: str | None
    entrance: str | None
    floor: int | None
    rooms: int
    area: Decimal | None
    has_location: bool
    ownership_status: OwnershipStatus
    ownership_verified_at: datetime | None
    units: list[UnitOut]
    created_at: datetime
    details: PropertyDetails = Field(default_factory=PropertyDetails)
    completeness: CompletenessOut | None = None  # рахує сервіс


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    uploaded_at: datetime
    deleted: bool  # файл уже видалено зі сховища за строком зберігання


class VerificationOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    user_id: uuid.UUID
    status: VerificationStatus
    is_dev_bypass: bool
    reject_reason: str | None
    owner_name_in_doc: str | None
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    documents: list[DocumentOut]


class OwnershipOut(BaseModel):
    """PRP-OWN: стан підтвердження власності для власника."""

    property_id: uuid.UUID
    ownership_status: OwnershipStatus
    # Відкрита (draft/pending) або остання вирішена перевірка.
    verification: VerificationOut | None
    dev_bypass_available: bool
    max_documents: int
    max_document_mb: int


class OwnerSummary(BaseModel):
    """Що модератор бачить про власника (MOD-OWN). Без телефону й Telegram."""

    user_id: uuid.UUID
    display_name: str
    registered_at: datetime
    risk_signals: int
    properties_count: int


class QueueItem(BaseModel):
    verification_id: uuid.UUID
    property: PropertyOut
    owner: OwnerSummary
    submitted_at: datetime | None
    documents_count: int


class VerificationCase(BaseModel):
    verification: VerificationOut
    property: PropertyOut
    owner: OwnerSummary


class PhotoOut(BaseModel):
    """Фото об'єкта (FR-PRP-9). Сам файл — окремим запитом."""

    id: uuid.UUID
    position: int  # 0 — обкладинка
    width: int
    height: int
