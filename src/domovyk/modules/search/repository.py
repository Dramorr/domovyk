"""Доступ до favorites. Лише всередині модуля search."""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.modules.search.models import FavoriteRow


class SearchRepository(Protocol):
    async def add_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None: ...
    async def remove_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None: ...
    async def favorite_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Новіші додані спершу."""
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
