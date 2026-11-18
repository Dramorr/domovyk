"""Фейковий репозиторій модуля showings для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from domovyk.domain.showings import SlotStatus
from domovyk.modules.showings.schemas import SlotOut


@dataclass
class ShowingDb:
    slots: dict[uuid.UUID, SlotOut] = field(default_factory=dict)


class FakeShowingsRepository:
    def __init__(self, db: ShowingDb) -> None:
        self.db = db

    async def create(
        self,
        listing_id: uuid.UUID,
        host_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        capacity: int,
    ) -> SlotOut:
        slot = SlotOut(
            id=uuid.uuid4(),
            listing_id=listing_id,
            host_user_id=host_id,
            starts_at=starts_at,
            ends_at=ends_at,
            capacity=capacity,
            status=SlotStatus.OPEN,
        )
        self.db.slots[slot.id] = slot
        return slot

    async def get(self, slot_id: uuid.UUID) -> SlotOut | None:
        return self.db.slots.get(slot_id)

    async def lock(self, slot_id: uuid.UUID) -> SlotOut | None:
        return self.db.slots.get(slot_id)

    async def by_ids(self, slot_ids: list[uuid.UUID]) -> list[SlotOut]:
        return [s for i, s in self.db.slots.items() if i in slot_ids]

    async def by_listing(self, listing_id: uuid.UUID, ends_after: datetime) -> list[SlotOut]:
        out = [s for s in self.db.slots.values() if s.listing_id == listing_id]
        out = [s for s in out if s.ends_at > ends_after]
        return sorted(out, key=lambda s: s.starts_at)

    async def count_open(self, listing_id: uuid.UUID, ends_after: datetime) -> int:
        return sum(
            1 for s in await self.by_listing(listing_id, ends_after) if s.status is SlotStatus.OPEN
        )

    async def set_status(self, slot_id: uuid.UUID, status: SlotStatus) -> None:
        self.db.slots[slot_id] = self.db.slots[slot_id].model_copy(update={"status": status})
