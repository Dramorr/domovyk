"""DTO модуля utilities — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from domovyk.domain.contracts import PartyRole
from domovyk.domain.utilities import MeterKind


class ProviderOut(BaseModel):
    id: int
    name: str
    meter_kinds: list[MeterKind]
    cabinet_url: str | None
    payment_url: str | None


class WindowOut(BaseModel):
    """Вікно подачі показників: поточне або найближче (обидві дати включно)."""

    start: date
    end: date
    open: bool


class ReadingOut(BaseModel):
    id: uuid.UUID
    value: Decimal
    consumed: Decimal | None  # різниця з попереднім показником
    estimate: Decimal | None  # орієнтовна сума за тарифом власника (FR-UTL-4)
    reading_date: date
    submitted_by_name: str
    mine: bool
    has_photo: bool
    sent_to_provider_at: datetime | None
    can_delete: bool


class MeterOut(BaseModel):
    id: uuid.UUID
    kind: MeterKind
    label: str | None
    provider: ProviderOut | None
    account_number: str | None
    submits: PartyRole
    pays: PartyRole
    tariff: Decimal | None
    is_active: bool
    window: WindowOut | None
    due: bool  # вікно відкрите, а показника в ньому ще немає
    i_submit: bool  # нагадування приходять мені
    last: ReadingOut | None
    readings: list[ReadingOut]


class MetersOverview(BaseModel):
    """UTL-LIST: лічильники об'єкта очима власника або орендаря."""

    property_id: uuid.UUID
    lease_id: uuid.UUID | None
    my_role: PartyRole
    can_manage: bool
    can_submit: bool
    meters: list[MeterOut]
    providers: list[ProviderOut]  # для форми власника; орендарю — порожньо


class MeterIn(BaseModel):
    kind: MeterKind
    provider_id: int | None = None
    account_number: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=40)
    submits: PartyRole = PartyRole.TENANT
    pays: PartyRole = PartyRole.TENANT
    tariff: Decimal | None = Field(default=None, gt=0, le=100000)
    initial_value: Decimal | None = Field(default=None, ge=0)  # лише при створенні


class ReadingResult(BaseModel):
    """Що показати після подачі: скільки спожито й куди передати показник."""

    meter: MeterOut
    reading: ReadingOut
    cabinet_url: str | None


ReminderStage = Literal["open", "last"]
