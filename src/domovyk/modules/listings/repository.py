"""Доступ до listings. Лише всередині модуля listings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update

from domovyk.core.tx import Tx, session_of
from domovyk.domain.listings import LIVE, ListingStatus
from domovyk.modules.listings.models import ListingRow


@dataclass(frozen=True)
class ListingRecord:
    id: uuid.UUID
    unit_id: uuid.UUID
    created_by: uuid.UUID
    price_amount: int
    utilities_included: bool
    deposit_amount: int | None
    min_term_months: int | None
    available_from: date | None
    status: ListingStatus
    owner_declaration_at: datetime | None
    published_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ListingTerms:
    price_amount: int
    utilities_included: bool
    deposit_amount: int | None
    min_term_months: int | None
    available_from: date | None


class ListingsRepository(Protocol):
    async def get(self, listing_id: uuid.UUID) -> ListingRecord | None: ...
    async def latest_on_units(self, unit_ids: list[uuid.UUID]) -> ListingRecord | None:
        """Живе оголошення на юнітах, а якщо його немає — останнє з історії."""
        ...

    async def live_on_units(self, unit_ids: list[uuid.UUID]) -> list[ListingRecord]: ...
    async def with_status(self, statuses: list[ListingStatus]) -> list[ListingRecord]: ...
    async def many(self, listing_ids: list[uuid.UUID]) -> list[ListingRecord]: ...
    async def ids_on_units(self, unit_ids: list[uuid.UUID]) -> list[uuid.UUID]: ...
    async def by_author(self, user_id: uuid.UUID) -> list[ListingRecord]:
        """Живі оголошення автора й останні з історії — по одному на юніт."""
        ...

    async def create(
        self, unit_id: uuid.UUID, author: uuid.UUID, terms: ListingTerms
    ) -> uuid.UUID: ...
    async def set_terms(self, listing_id: uuid.UUID, terms: ListingTerms) -> None: ...
    async def update(self, listing_id: uuid.UUID, **values: Any) -> None: ...
    async def delete(self, listing_id: uuid.UUID) -> None: ...
    async def published_units_since(self, user_id: uuid.UUID, since: datetime) -> int:
        """Скільки різних юнітів автор опублікував від `since` (FR-LST-5, «багато адрес»)."""
        ...


def _record(r: ListingRow) -> ListingRecord:
    return ListingRecord(
        id=r.id,
        unit_id=r.unit_id,
        created_by=r.created_by,
        price_amount=r.price_amount,
        utilities_included=r.utilities_included,
        deposit_amount=r.deposit_amount,
        min_term_months=r.min_term_months,
        available_from=r.available_from,
        status=r.status,
        owner_declaration_at=r.owner_declaration_at,
        published_at=r.published_at,
        closed_at=r.closed_at,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


class SqlListingsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def get(self, listing_id: uuid.UUID) -> ListingRecord | None:
        row = await self._s.get(ListingRow, listing_id, populate_existing=True)
        return _record(row) if row else None

    async def latest_on_units(self, unit_ids: list[uuid.UUID]) -> ListingRecord | None:
        if not unit_ids:
            return None
        live_first = ListingRow.status.in_(list(LIVE)).desc()
        row = await self._s.scalar(
            select(ListingRow)
            .where(ListingRow.unit_id.in_(unit_ids))
            .order_by(live_first, ListingRow.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return _record(row) if row else None

    async def live_on_units(self, unit_ids: list[uuid.UUID]) -> list[ListingRecord]:
        if not unit_ids:
            return []
        rows = await self._s.scalars(
            select(ListingRow)
            .where(ListingRow.unit_id.in_(unit_ids), ListingRow.status.in_(list(LIVE)))
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]

    async def with_status(self, statuses: list[ListingStatus]) -> list[ListingRecord]:
        rows = await self._s.scalars(
            select(ListingRow)
            .where(ListingRow.status.in_(statuses))
            .order_by(ListingRow.published_at.desc(), ListingRow.id)
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]

    async def ids_on_units(self, unit_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        if not unit_ids:
            return []
        rows = await self._s.scalars(select(ListingRow.id).where(ListingRow.unit_id.in_(unit_ids)))
        return list(rows)

    async def many(self, listing_ids: list[uuid.UUID]) -> list[ListingRecord]:
        if not listing_ids:
            return []
        rows = await self._s.scalars(
            select(ListingRow)
            .where(ListingRow.id.in_(listing_ids))
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]

    async def by_author(self, user_id: uuid.UUID) -> list[ListingRecord]:
        live_first = ListingRow.status.in_(list(LIVE)).desc()
        rows = await self._s.scalars(
            select(ListingRow)
            .where(ListingRow.created_by == user_id)
            .order_by(live_first, ListingRow.created_at.desc())
            .execution_options(populate_existing=True)
        )
        best: dict[uuid.UUID, ListingRecord] = {}
        for r in rows:
            best.setdefault(r.unit_id, _record(r))  # перший — живий або найновіший
        return list(best.values())

    async def create(self, unit_id: uuid.UUID, author: uuid.UUID, terms: ListingTerms) -> uuid.UUID:
        row = ListingRow(
            unit_id=unit_id,
            created_by=author,
            price_amount=terms.price_amount,
            utilities_included=terms.utilities_included,
            deposit_amount=terms.deposit_amount,
            min_term_months=terms.min_term_months,
            available_from=terms.available_from,
            status=ListingStatus.DRAFT,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def set_terms(self, listing_id: uuid.UUID, terms: ListingTerms) -> None:
        await self.update(
            listing_id,
            price_amount=terms.price_amount,
            utilities_included=terms.utilities_included,
            deposit_amount=terms.deposit_amount,
            min_term_months=terms.min_term_months,
            available_from=terms.available_from,
        )

    async def update(self, listing_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(
            update(ListingRow).where(ListingRow.id == listing_id).values(**values)
        )

    async def delete(self, listing_id: uuid.UUID) -> None:
        await self._s.execute(delete(ListingRow).where(ListingRow.id == listing_id))

    async def published_units_since(self, user_id: uuid.UUID, since: datetime) -> int:
        count = await self._s.scalar(
            select(func.count(func.distinct(ListingRow.unit_id))).where(
                ListingRow.created_by == user_id,
                ListingRow.published_at >= since,
            )
        )
        return int(count or 0)
