"""Фейковий репозиторій модуля applications для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domovyk.domain.applications import OPEN, ApplicationStatus
from domovyk.modules.applications.repository import ApplicationRecord


@dataclass
class ApplicationDb:
    apps: dict[uuid.UUID, ApplicationRecord] = field(default_factory=dict)
    history: list[tuple[uuid.UUID, str | None, str, uuid.UUID | None]] = field(default_factory=list)


class FakeApplicationsRepository:
    def __init__(self, db: ApplicationDb) -> None:
        self.db = db

    async def get(self, application_id: uuid.UUID) -> ApplicationRecord | None:
        return self.db.apps.get(application_id)

    async def open_for(
        self, listing_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ApplicationRecord | None:
        return next(
            (
                a
                for a in self.db.apps.values()
                if a.listing_id == listing_id and a.tenant_user_id == tenant_id and a.status in OPEN
            ),
            None,
        )

    async def create(
        self,
        listing_id: uuid.UUID,
        tenant_id: uuid.UUID,
        message: str | None,
        expires_at: datetime,
        created_at: datetime,
    ) -> uuid.UUID:
        if await self.open_for(listing_id, tenant_id) is not None:
            raise AssertionError("uq_one_open_application")
        rec = ApplicationRecord(
            id=uuid.uuid4(),
            listing_id=listing_id,
            tenant_user_id=tenant_id,
            status=ApplicationStatus.PENDING,
            message=message,
            decline_reason=None,
            lease_id=None,
            contacts_revealed_at=None,
            decided_at=None,
            expires_at=expires_at,
            created_at=created_at,
        )
        self.db.apps[rec.id] = rec
        return rec.id

    async def update(self, application_id: uuid.UUID, **values: Any) -> None:
        self.db.apps[application_id] = dataclasses.replace(self.db.apps[application_id], **values)

    async def add_history(
        self,
        application_id: uuid.UUID,
        from_status: ApplicationStatus | None,
        to_status: ApplicationStatus,
        changed_by: uuid.UUID | None,
        at: datetime,
    ) -> None:
        self.db.history.append(
            (
                application_id,
                from_status.value if from_status else None,
                to_status.value,
                changed_by,
            )
        )

    async def by_tenant(self, tenant_id: uuid.UUID) -> list[ApplicationRecord]:
        out = [a for a in self.db.apps.values() if a.tenant_user_id == tenant_id]
        return sorted(out, key=lambda a: a.created_at, reverse=True)

    async def by_listings(self, listing_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        out = [a for a in self.db.apps.values() if a.listing_id in listing_ids]
        return sorted(out, key=lambda a: a.created_at, reverse=True)

    async def open_on_listings(self, listing_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        return [
            a for a in self.db.apps.values() if a.listing_id in listing_ids and a.status in OPEN
        ]

    async def expired_pending(self, now: datetime) -> list[ApplicationRecord]:
        return [
            a
            for a in self.db.apps.values()
            if a.status is ApplicationStatus.PENDING and a.expires_at <= now
        ]
