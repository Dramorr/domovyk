"""Доступ до applications і application_status_history. Лише всередині модуля applications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update

from domovyk.core.tx import Tx, session_of
from domovyk.domain.applications import OPEN, ApplicationStatus
from domovyk.modules.applications.models import ApplicationHistoryRow, ApplicationRow


@dataclass(frozen=True)
class ApplicationRecord:
    id: uuid.UUID
    listing_id: uuid.UUID
    tenant_user_id: uuid.UUID
    status: ApplicationStatus
    message: str | None
    decline_reason: str | None
    lease_id: uuid.UUID | None
    contacts_revealed_at: datetime | None
    decided_at: datetime | None
    expires_at: datetime
    created_at: datetime
    slot_id: uuid.UUID | None = None
    showing_tenant_answer: str | None = None
    showing_landlord_answer: str | None = None


def _record(r: ApplicationRow) -> ApplicationRecord:
    return ApplicationRecord(
        id=r.id,
        listing_id=r.listing_id,
        tenant_user_id=r.tenant_user_id,
        status=r.status,
        message=r.message,
        decline_reason=r.decline_reason,
        lease_id=r.lease_id,
        contacts_revealed_at=r.contacts_revealed_at,
        decided_at=r.decided_at,
        expires_at=r.expires_at,
        created_at=r.created_at,
        slot_id=r.slot_id,
        showing_tenant_answer=r.showing_tenant_answer,
        showing_landlord_answer=r.showing_landlord_answer,
    )


class ApplicationsRepository(Protocol):
    async def get(self, application_id: uuid.UUID) -> ApplicationRecord | None: ...
    async def open_for(
        self, listing_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ApplicationRecord | None: ...
    async def create(
        self,
        listing_id: uuid.UUID,
        tenant_id: uuid.UUID,
        message: str | None,
        expires_at: datetime,
        created_at: datetime,
    ) -> uuid.UUID: ...
    async def update(self, application_id: uuid.UUID, **values: Any) -> None: ...
    async def add_history(
        self,
        application_id: uuid.UUID,
        from_status: ApplicationStatus | None,
        to_status: ApplicationStatus,
        changed_by: uuid.UUID | None,
        at: datetime,
    ) -> None: ...
    async def by_tenant(self, tenant_id: uuid.UUID) -> list[ApplicationRecord]:
        """Новіші спершу."""
        ...

    async def by_listings(self, listing_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        """Новіші спершу."""
        ...

    async def open_on_listings(self, listing_ids: list[uuid.UUID]) -> list[ApplicationRecord]: ...
    async def expired_pending(self, now: datetime) -> list[ApplicationRecord]: ...
    async def booked_on(self, slot_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        """Записані на слоти (showing_scheduled) — для місткості й списку власника."""
        ...

    async def scheduled(self) -> list[ApplicationRecord]:
        """Усі записи на покази — для нагадувань і опитування після показу."""
        ...


class SqlApplicationsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def get(self, application_id: uuid.UUID) -> ApplicationRecord | None:
        row = await self._s.get(ApplicationRow, application_id, populate_existing=True)
        return _record(row) if row else None

    async def open_for(
        self, listing_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ApplicationRecord | None:
        row = await self._s.scalar(
            select(ApplicationRow).where(
                ApplicationRow.listing_id == listing_id,
                ApplicationRow.tenant_user_id == tenant_id,
                ApplicationRow.status.in_(list(OPEN)),
            )
        )
        return _record(row) if row else None

    async def create(
        self,
        listing_id: uuid.UUID,
        tenant_id: uuid.UUID,
        message: str | None,
        expires_at: datetime,
        created_at: datetime,
    ) -> uuid.UUID:
        row = ApplicationRow(
            listing_id=listing_id,
            tenant_user_id=tenant_id,
            status=ApplicationStatus.PENDING,
            message=message,
            expires_at=expires_at,
            created_at=created_at,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def update(self, application_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(
            update(ApplicationRow).where(ApplicationRow.id == application_id).values(**values)
        )

    async def add_history(
        self,
        application_id: uuid.UUID,
        from_status: ApplicationStatus | None,
        to_status: ApplicationStatus,
        changed_by: uuid.UUID | None,
        at: datetime,
    ) -> None:
        self._s.add(
            ApplicationHistoryRow(
                application_id=application_id,
                from_status=from_status,
                to_status=to_status,
                changed_by=changed_by,
                changed_at=at,
            )
        )
        await self._s.flush()

    async def by_tenant(self, tenant_id: uuid.UUID) -> list[ApplicationRecord]:
        rows = await self._s.scalars(
            select(ApplicationRow)
            .where(ApplicationRow.tenant_user_id == tenant_id)
            .order_by(ApplicationRow.created_at.desc())
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]

    async def by_listings(self, listing_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        if not listing_ids:
            return []
        rows = await self._s.scalars(
            select(ApplicationRow)
            .where(ApplicationRow.listing_id.in_(listing_ids))
            .order_by(ApplicationRow.created_at.desc())
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]

    async def open_on_listings(self, listing_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        if not listing_ids:
            return []
        rows = await self._s.scalars(
            select(ApplicationRow).where(
                ApplicationRow.listing_id.in_(listing_ids),
                ApplicationRow.status.in_(list(OPEN)),
            )
        )
        return [_record(r) for r in rows]

    async def expired_pending(self, now: datetime) -> list[ApplicationRecord]:
        rows = await self._s.scalars(
            select(ApplicationRow).where(
                ApplicationRow.status == ApplicationStatus.PENDING,
                ApplicationRow.expires_at <= now,
            )
        )
        return [_record(r) for r in rows]

    async def booked_on(self, slot_ids: list[uuid.UUID]) -> list[ApplicationRecord]:
        if not slot_ids:
            return []
        rows = await self._s.scalars(
            select(ApplicationRow)
            .where(
                ApplicationRow.slot_id.in_(slot_ids),
                ApplicationRow.status == ApplicationStatus.SHOWING_SCHEDULED,
            )
            .order_by(ApplicationRow.updated_at)
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]

    async def scheduled(self) -> list[ApplicationRecord]:
        rows = await self._s.scalars(
            select(ApplicationRow)
            .where(ApplicationRow.status == ApplicationStatus.SHOWING_SCHEDULED)
            .execution_options(populate_existing=True)
        )
        return [_record(r) for r in rows]
