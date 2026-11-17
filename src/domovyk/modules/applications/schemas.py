"""DTO модуля applications — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from domovyk.domain.applications import ApplicationStatus, DeclineReason
from domovyk.domain.listings import ListingStatus
from domovyk.modules.users.schemas import PartyProfile


class ApplicationIn(BaseModel):
    """APP-NEW: пара слів власнику, необов'язково. Контакти — лише після схвалення."""

    message: str | None = Field(default=None, max_length=600)


class DeclineIn(BaseModel):
    reason: DeclineReason | None = None


class ListingBrief(BaseModel):
    """Оголошення в заявці — без точної адреси, доки заявку не схвалено (FR-LST-4)."""

    listing_id: uuid.UUID
    property_id: uuid.UUID
    status: ListingStatus
    price_amount: int
    utilities_included: bool
    rooms: int
    area: Decimal | None
    district: str | None
    street: str
    cover_photo_id: uuid.UUID | None


class ContactsOut(BaseModel):
    """Після схвалення (FR-APP-2): телефон іншої сторони й точна адреса."""

    phone: str | None
    house_number: str
    apartment: str | None
    entrance: str | None
    floor: int | None


class ApplicationOut(BaseModel):
    id: uuid.UUID
    status: ApplicationStatus
    my_role: Literal["tenant", "landlord"]
    message: str | None
    decline_reason: DeclineReason | None
    created_at: datetime
    decided_at: datetime | None
    expires_at: datetime
    listing: ListingBrief
    other: PartyProfile  # для орендаря — власник, для власника — орендар
    unread: int
    lease_id: uuid.UUID | None


class ApplicationCard(ApplicationOut):
    contacts: ContactsOut | None
    can_approve: bool
    can_decline: bool
    can_withdraw: bool
    can_create_lease: bool
    can_chat: bool
