"""DTO модуля applications — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from domovyk.domain.applications import ApplicationStatus, DeclineReason
from domovyk.domain.listings import ListingStatus
from domovyk.domain.showings import ShowingAnswer
from domovyk.modules.users.schemas import PartyProfile


class ApplicationIn(BaseModel):
    """APP-NEW: пара слів власнику, необов'язково. Контакти — лише після схвалення."""

    message: str | None = Field(default=None, max_length=600)


class DeclineIn(BaseModel):
    reason: DeclineReason | None = None


class ShowingIn(BaseModel):
    """Записатись на слот показу (FR-APP-4)."""

    slot_id: uuid.UUID


class ShowingAnswerIn(BaseModel):
    """Як пройшов показ (FR-APP-5)."""

    answer: ShowingAnswer


class ShowingOut(BaseModel):
    """Запис на показ у заявці."""

    slot_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    group: bool  # груповий показ (місткість більше одного)
    my_answer: ShowingAnswer | None
    can_answer: bool  # показ почався, а я ще не відповів


class SlotChoice(BaseModel):
    """Вільний час для запису (орендарю)."""

    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    capacity: int
    free: int
    mine: bool


class BookingOut(BaseModel):
    application_id: uuid.UUID
    display_name: str


class BoardSlot(BaseModel):
    """Слот у списку власника: хто записався."""

    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    capacity: int
    booked: list[BookingOut]


class ShowingsBoard(BaseModel):
    """Покази оголошення (власнику): майбутні слоти й записані."""

    listing_id: uuid.UUID | None
    can_add: bool
    slots: list[BoardSlot]


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
    showing: ShowingOut | None = None


class ApplicationCard(ApplicationOut):
    contacts: ContactsOut | None
    can_approve: bool
    can_decline: bool
    can_withdraw: bool
    can_create_lease: bool
    can_chat: bool
    can_book: bool = False  # орендар обирає (або змінює) час показу
    can_cancel_showing: bool = False  # будь-яка сторона, до початку показу
