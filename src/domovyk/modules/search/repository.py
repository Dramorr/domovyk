"""Доступ до favorites. Лише всередині модуля search."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.modules.search.models import FavoriteRow, SearchFilterRow


@dataclass(frozen=True)
class FilterRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    city_code: str
    criteria: dict[str, Any]
    notify: bool
    created_at: datetime


def _filter(r: SearchFilterRow) -> FilterRecord:
    return FilterRecord(
        id=r.id,
        user_id=r.user_id,
        name=r.name,
        city_code=r.city_code,
        criteria=dict(r.criteria or {}),
        notify=r.notify,
        created_at=r.created_at,
    )


class SearchRepository(Protocol):
    async def add_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None: ...
    async def remove_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None: ...
    async def favorite_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Новіші додані спершу."""
        ...

    async def filters(self, user_id: uuid.UUID) -> list[FilterRecord]: ...
    async def filter(self, filter_id: uuid.UUID) -> FilterRecord | None: ...
    async def count_filters(self, user_id: uuid.UUID) -> int: ...
    async def add_filter(
        self, user_id: uuid.UUID, name: str, city_code: str, criteria: dict[str, Any], notify: bool
    ) -> uuid.UUID: ...
    async def update_filter(self, filter_id: uuid.UUID, **values: Any) -> None: ...
    async def delete_filter(self, filter_id: uuid.UUID) -> None: ...
    async def notify_filters(self, city_code: str) -> list[FilterRecord]:
        """Фільтри міста з увімкненими сповіщеннями (FR-SRC-3)."""
        ...


class SqlSearchRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def add_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None:
        await self._s.execute(
            insert(FavoriteRow)
            .values(user_id=user_id, listing_id=listing_id)
            .on_conflict_do_nothing()
        )

    async def remove_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None:
        await self._s.execute(
            delete(FavoriteRow).where(
                FavoriteRow.user_id == user_id, FavoriteRow.listing_id == listing_id
            )
        )

    async def favorite_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        rows = await self._s.scalars(
            select(FavoriteRow.listing_id)
            .where(FavoriteRow.user_id == user_id)
            .order_by(FavoriteRow.created_at.desc())
        )
        return list(rows)

    async def filters(self, user_id: uuid.UUID) -> list[FilterRecord]:
        rows = await self._s.scalars(
            select(SearchFilterRow)
            .where(SearchFilterRow.user_id == user_id)
            .order_by(SearchFilterRow.created_at, SearchFilterRow.id)
            .execution_options(populate_existing=True)
        )
        return [_filter(r) for r in rows]

    async def filter(self, filter_id: uuid.UUID) -> FilterRecord | None:
        row = await self._s.get(SearchFilterRow, filter_id, populate_existing=True)
        return _filter(row) if row else None

    async def count_filters(self, user_id: uuid.UUID) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(SearchFilterRow)
            .where(SearchFilterRow.user_id == user_id)
        )
        return int(count or 0)

    async def add_filter(
        self, user_id: uuid.UUID, name: str, city_code: str, criteria: dict[str, Any], notify: bool
    ) -> uuid.UUID:
        row = SearchFilterRow(
            user_id=user_id, name=name, city_code=city_code, criteria=criteria, notify=notify
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def update_filter(self, filter_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(
            update(SearchFilterRow).where(SearchFilterRow.id == filter_id).values(**values)
        )

    async def delete_filter(self, filter_id: uuid.UUID) -> None:
        await self._s.execute(delete(SearchFilterRow).where(SearchFilterRow.id == filter_id))

    async def notify_filters(self, city_code: str) -> list[FilterRecord]:
        rows = await self._s.scalars(
            select(SearchFilterRow).where(
                SearchFilterRow.city_code == city_code, SearchFilterRow.notify.is_(True)
            )
        )
        return [_filter(r) for r in rows]
