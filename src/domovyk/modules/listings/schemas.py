"""DTO модуля listings — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from domovyk.domain.listings import ListingStatus


class ListingIn(BaseModel):
    """Умови угоди (FR-LST-1). Решту — опис, тварин, дітей, мешканців — бере з анкети."""

    price_amount: int
    utilities_included: bool = False
    deposit_amount: int | None = None
    min_term_months: int | None = None
    available_from: date | None = None


class PublishIn(BaseModel):
    """FR-LST-2: «Я власник або уповноважена особа і не беру комісії»."""

    owner_declaration: bool = False


class ListingOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    unit_id: uuid.UUID
    status: ListingStatus
    price_amount: int
    currency: str
    utilities_included: bool
    deposit_amount: int | None
    min_term_months: int | None
    available_from: date | None
    owner_declared: bool
    published_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ListingReadiness(BaseModel):
    """Що бракує для публікації. Фото не обов'язкові, але без них оголошення слабке."""

    ownership_verified: bool
    completeness_percent: int
    completeness_min: int
    profile_ok: bool
    photos: int
    unit_busy: bool  # на об'єкті жива оренда
    ready: bool


class ListingState(BaseModel):
    """Екран оголошення власника: живе оголошення (або останнє з історії) і готовність."""

    property_id: uuid.UUID
    listing: ListingOut | None
    readiness: ListingReadiness
