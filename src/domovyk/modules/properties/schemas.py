"""DTO модуля properties — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from domovyk.domain.properties import (
    DEFAULT_CITY,
    OwnershipStatus,
    PropertyKind,
    UnitKind,
    VerificationStatus,
)


class AddressCandidate(BaseModel):
    """Будинок, знайдений геокодером (PRP-ADDR-CONFIRM)."""

    city: str
    street: str
    house_number: str
    district: str | None = None
    lat: float | None = None
    lon: float | None = None
    place_id: str | None = None


class AddressIn(BaseModel):
    """Адреса будинку: підтверджений кандидат геокодера або введена вручну."""

    city: str = Field(default=DEFAULT_CITY, max_length=60)
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
