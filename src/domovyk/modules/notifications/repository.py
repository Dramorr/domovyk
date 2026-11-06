"""Доступ до notifications (outbox). Лише всередині модуля."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.modules.notifications.models import NotificationPreferenceRow, NotificationRow
from domovyk.modules.notifications.schemas import PendingNotification

# Позначка «більше не пробувати»: більше за будь-який ліміт спроб (smallint).
GIVE_UP_ATTEMPTS = 32767


class NotificationsRepository(Protocol):
    async def add(
        self,
        user_id: uuid.UUID,
        type_: str,
        payload: dict[str, Any],
        dedupe_key: str,
        send_after: datetime,
    ) -> bool: ...

    async def claim_next(self, now: datetime, max_attempts: int) -> PendingNotification | None: ...
    async def mark_sent(self, notification_id: int, at: datetime) -> None: ...
    async def prefs(
        self, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> dict[str, tuple[bool, dict[str, Any]]]: ...
    async def set_pref(
        self,
        user_id: uuid.UUID,
        lease_id: uuid.UUID,
        type_: str,
        enabled: bool,
        params: dict[str, Any],
    ) -> None: ...
    async def mark_failed(
        self, notification_id: int, error: str, *, retry_at: datetime | None
    ) -> None:
        """retry_at=None — більше не пробувати."""
        ...


class SqlNotificationsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def add(
        self,
        user_id: uuid.UUID,
        type_: str,
        payload: dict[str, Any],
        dedupe_key: str,
        send_after: datetime,
    ) -> bool:
        stmt = (
            insert(NotificationRow)
            .values(
                user_id=user_id,
                type=type_,
                payload=payload,
                dedupe_key=dedupe_key,
                send_after=send_after,
            )
            .on_conflict_do_nothing(index_elements=[NotificationRow.dedupe_key])
            .returning(NotificationRow.id)
        )
        return (await self._s.scalar(stmt)) is not None

    async def claim_next(self, now: datetime, max_attempts: int) -> PendingNotification | None:
        row = await self._s.scalar(
            select(NotificationRow)
            .where(
                NotificationRow.sent_at.is_(None),
                NotificationRow.send_after <= now,
                NotificationRow.attempts < max_attempts,
            )
            .order_by(NotificationRow.send_after, NotificationRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        return PendingNotification(
            id=row.id, user_id=row.user_id, type=row.type, payload=dict(row.payload)
        )

    async def mark_sent(self, notification_id: int, at: datetime) -> None:
        await self._s.execute(
            update(NotificationRow)
            .where(NotificationRow.id == notification_id)
            .values(sent_at=at, attempts=NotificationRow.attempts + 1, last_error=None)
        )

    async def mark_failed(
        self, notification_id: int, error: str, *, retry_at: datetime | None
    ) -> None:
        values: dict[str, Any] = {"last_error": error[:500]}
        if retry_at is None:
            values["attempts"] = GIVE_UP_ATTEMPTS
        else:
            values["attempts"] = NotificationRow.attempts + 1
            values["send_after"] = retry_at
        await self._s.execute(
            update(NotificationRow).where(NotificationRow.id == notification_id).values(**values)
        )

    async def prefs(
        self, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> dict[str, tuple[bool, dict[str, Any]]]:
        rows = await self._s.scalars(
            select(NotificationPreferenceRow).where(
                NotificationPreferenceRow.user_id == user_id,
                NotificationPreferenceRow.lease_id == lease_id,
                NotificationPreferenceRow.channel == "telegram",
            )
        )
        return {r.type: (r.enabled, dict(r.params)) for r in rows}

    async def set_pref(
        self,
        user_id: uuid.UUID,
        lease_id: uuid.UUID,
        type_: str,
        enabled: bool,
        params: dict[str, Any],
    ) -> None:
        stmt = insert(NotificationPreferenceRow).values(
            user_id=user_id, lease_id=lease_id, type=type_, enabled=enabled, params=params
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    NotificationPreferenceRow.user_id,
                    NotificationPreferenceRow.lease_id,
                    NotificationPreferenceRow.type,
                    NotificationPreferenceRow.channel,
                ],
                set_={"enabled": enabled, "params": params},
            )
        )
