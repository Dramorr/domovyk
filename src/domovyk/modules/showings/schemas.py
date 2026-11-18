"""DTO модуля showings — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel

from domovyk.domain.showings import SlotStatus


class SlotIn(BaseModel):
    """Новий слот: день і час за Києвом, тривалість у хвилинах, місткість (FR-APP-4)."""

    day: date
    start: time
    minutes: int = 30
    capacity: int = 1


class SlotOut(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    host_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    capacity: int
    status: SlotStatus
