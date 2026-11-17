"""Фейковий репозиторій модуля listings для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from domovyk.domain.listings import LIVE, ListingStatus
from domovyk.modules.listings.repository import ListingRecord, ListingTerms
from domovyk.modules.search.repository import FilterRecord


@dataclass
class ListingDb:
    listings: dict[uuid.UUID, ListingRecord] = field(default_factory=dict)


class FakeListingsRepository:
    def __init__(self, db: ListingDb) -> None:
        self.db = db

    async def get(self, listing_id: uuid.UUID) -> ListingRecord | None:
        return self.db.listings.get(listing_id)

    async def latest_on_units(self, unit_ids: list[uuid.UUID]) -> ListingRecord | None:
        mine = [r for r in self.db.listings.values() if r.unit_id in unit_ids]
        mine.sort(key=lambda r: (r.status in LIVE, r.created_at), reverse=True)
        return mine[0] if mine else None

    async def live_on_units(self, unit_ids: list[uuid.UUID]) -> list[ListingRecord]:
        return [r for r in self.db.listings.values() if r.unit_id in unit_ids and r.status in LIVE]

    async def with_status(self, statuses: list[ListingStatus]) -> list[ListingRecord]:
        out = [r for r in self.db.listings.values() if r.status in statuses]
        return sorted(out, key=lambda r: r.published_at or r.created_at, reverse=True)

    async def many(self, listing_ids: list[uuid.UUID]) -> list[ListingRecord]:
        return [self.db.listings[i] for i in listing_ids if i in self.db.listings]

    async def by_author(self, user_id: uuid.UUID) -> list[ListingRecord]:
        best: dict[uuid.UUID, ListingRecord] = {}
        for r in self.db.listings.values():
            if r.created_by != user_id:
                continue
            cur = best.get(r.unit_id)
            if cur is None or (r.status in LIVE, r.created_at) > (
                cur.status in LIVE,
                cur.created_at,
            ):
                best[r.unit_id] = r
        return list(best.values())

    async def create(self, unit_id: uuid.UUID, author: uuid.UUID, terms: ListingTerms) -> uuid.UUID:
        if await self.live_on_units([unit_id]):
            raise AssertionError("uq_one_live_listing_per_unit")
        now = datetime.now(UTC)
        record = ListingRecord(
            id=uuid.uuid4(),
            unit_id=unit_id,
            created_by=author,
            status=ListingStatus.DRAFT,
            owner_declaration_at=None,
            published_at=None,
            closed_at=None,
            created_at=now,
            updated_at=now,
            **dataclasses.asdict(terms),
        )
        self.db.listings[record.id] = record
        return record.id

    async def set_terms(self, listing_id: uuid.UUID, terms: ListingTerms) -> None:
        await self.update(listing_id, **dataclasses.asdict(terms))

    async def update(self, listing_id: uuid.UUID, **values: Any) -> None:
        current = self.db.listings[listing_id]
        self.db.listings[listing_id] = dataclasses.replace(
            current, updated_at=datetime.now(UTC), **values
        )

    async def delete(self, listing_id: uuid.UUID) -> None:
        self.db.listings.pop(listing_id, None)

    async def published_units_since(self, user_id: uuid.UUID, since: datetime) -> int:
        return len(
            {
                r.unit_id
                for r in self.db.listings.values()
                if r.created_by == user_id
                and r.published_at is not None
                and r.published_at >= since
            }
        )


@dataclass
class SearchDb:
    favorites: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)
    filters: dict[uuid.UUID, FilterRecord] = field(default_factory=dict)


class FakeSearchRepository:
    def __init__(self, db: SearchDb) -> None:
        self.db = db

    async def add_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None:
        if (user_id, listing_id) not in self.db.favorites:
            self.db.favorites.append((user_id, listing_id))

    async def remove_favorite(self, user_id: uuid.UUID, listing_id: uuid.UUID) -> None:
        if (user_id, listing_id) in self.db.favorites:
            self.db.favorites.remove((user_id, listing_id))

    async def favorite_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return [lid for uid, lid in reversed(self.db.favorites) if uid == user_id]

    async def filters(self, user_id: uuid.UUID) -> list[FilterRecord]:
        return sorted(
            (f for f in self.db.filters.values() if f.user_id == user_id),
            key=lambda f: f.created_at,
        )

    async def filter(self, filter_id: uuid.UUID) -> FilterRecord | None:
        return self.db.filters.get(filter_id)

    async def count_filters(self, user_id: uuid.UUID) -> int:
        return len(await self.filters(user_id))

    async def add_filter(
        self, user_id: uuid.UUID, name: str, city_code: str, criteria: dict[str, Any], notify: bool
    ) -> uuid.UUID:
        fid = uuid.uuid4()
        created = datetime.now(UTC) + timedelta(microseconds=len(self.db.filters))
        self.db.filters[fid] = FilterRecord(
            fid, user_id, name, city_code, criteria, notify, created
        )
        return fid

    async def update_filter(self, filter_id: uuid.UUID, **values: Any) -> None:
        self.db.filters[filter_id] = dataclasses.replace(self.db.filters[filter_id], **values)

    async def delete_filter(self, filter_id: uuid.UUID) -> None:
        self.db.filters.pop(filter_id, None)

    async def notify_filters(self, city_code: str) -> list[FilterRecord]:
        return [f for f in self.db.filters.values() if f.city_code == city_code and f.notify]
