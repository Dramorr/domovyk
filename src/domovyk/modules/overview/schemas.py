"""DTO модуля overview: етап і картка «Зараз» (ADR 0023)."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel

from domovyk.domain.leases import LeaseStatus
from domovyk.domain.listings import ListingStatus
from domovyk.domain.overview import NowCode, Stage, Target, Tone


class NowOut(BaseModel):
    """Одна наступна дія: код (текст — у локалізації), тон і куди веде кнопка."""

    code: NowCode
    tone: Tone
    target: Target
    count: int | None = None
    on: date | None = None


class PropertyOverview(BaseModel):
    """Сторінка квартири власника: етап, «Зараз» і що потрібно для вкладок."""

    property_id: uuid.UUID
    stage: Stage
    now: NowOut
    lease_id: uuid.UUID | None  # відкрита оренда (етапи «Оформлюю», «Здано», «Завершення»)
    lease_status: LeaseStatus | None
    listing_status: ListingStatus | None
    new_applications: int
    chat_unread: int
    has_history: bool  # є завершені чи скасовані оренди


class LeaseOverview(BaseModel):
    """Сторінка оренди (орендар, співмешканець): етап і «Зараз»."""

    lease_id: uuid.UUID
    stage: Stage
    now: NowOut
