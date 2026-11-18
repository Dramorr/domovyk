"""Доступ до showing_slots. Лише всередині модуля showings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select, update

from domovyk.core.tx import Tx, session_of
from domovyk.domain.showings import SlotStatus
from domovyk.modules.showings.models import SlotRow
from domovyk.modules.showings.schemas import SlotOut


def _out(r: SlotRow) -> SlotOut:
    return SlotOut(
        id=r.id,
        listing_id=r.listing_id,
        host_user_id=r.host_user_id,
        starts_at=r.starts_at,
        ends_at=r.ends_at,
        capacity=r.capacity,
        status=r.status,
    )


class ShowingsRepository(Protocol):
    async def create(
        self,
        listing_id: uuid.UUID,
        host_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        capacity: int,
    ) -> SlotOut: ...
    async def get(self, slot_id: uuid.UUID) -> SlotOut | None: ...
    async def lock(self, slot_id: uuid.UUID) -> SlotOut | None:
        """SELECT … FOR UPDATE: запис на слот не перевищить місткість при одночасних запитах."""
        ...

    async def by_ids(self, slot_ids: list[uuid.UUID]) -> list[SlotOut]: ...
    async def by_listing(self, listing_id: uuid.UUID, ends_after: datetime) -> list[SlotOut]:
        """Слоти, що ще не закінчились, за часом початку."""
        ...

    async def count_open(self, listing_id: uuid.UUID, ends_after: datetime) -> int: ...
    async def set_status(self, slot_id: uuid.UUID, status: SlotStatus) -> None: ...


class SqlShowingsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def create(
        self,
        listing_id: uuid.UUID,
        host_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        capacity: int,
    ) -> SlotOut:
        row = SlotRow(
            listing_id=listing_id,
            host_user_id=host_id,
            starts_at=starts_at,
            ends_at=ends_at,
            capacity=capacity,
            status=SlotStatus.OPEN,
        )
        self._s.add(row)
        await self._s.flush()
        return _out(row)

    async def get(self, slot_id: uuid.UUID) -> SlotOut | None:
        row = await self._s.get(SlotRow, slot_id, populate_existing=True)
        return _out(row) if row else None

    async def lock(self, slot_id: uuid.UUID) -> SlotOut | None:
        row = await self._s.scalar(
            select(SlotRow)
            .where(SlotRow.id == slot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _out(row) if row else None

    async def by_ids(self, slot_ids: list[uuid.UUID]) -> list[SlotOut]:
        if not slot_ids:
            return []
        rows = await self._s.scalars(select(SlotRow).where(SlotRow.id.in_(slot_ids)))
        return [_out(r) for r in rows]

    async def by_listing(self, listing_id: uuid.UUID, ends_after: datetime) -> list[SlotOut]:
        rows = await self._s.scalars(
            select(SlotRow)
            .where(SlotRow.listing_id == listing_id, SlotRow.ends_at > ends_after)
            .order_by(SlotRow.starts_at)
            .execution_options(populate_existing=True)
        )
        return [_out(r) for r in rows]

    async def count_open(self, listing_id: uuid.UUID, ends_after: datetime) -> int:
        n = await self._s.scalar(
            select(func.count())
            .select_from(SlotRow)
            .where(
                SlotRow.listing_id == listing_id,
                SlotRow.status == SlotStatus.OPEN,
                SlotRow.ends_at > ends_after,
            )
        )
        return int(n or 0)

    async def set_status(self, slot_id: uuid.UUID, status: SlotStatus) -> None:
        await self._s.execute(update(SlotRow).where(SlotRow.id == slot_id).values(status=status))
