"""Доступ до bans і audit_log. Лише всередині модуля moderation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import func, or_, select, update

from domovyk.core.tx import Tx, session_of
from domovyk.modules.moderation.models import AuditRow, BanRow, RiskSignalRow
from domovyk.modules.moderation.schemas import BanIdentifiers, BanOut


class ModerationRepository(Protocol):
    async def find_active_ban(self, ids: BanIdentifiers, now: datetime) -> BanOut | None: ...

    async def add_ban(
        self,
        ids: BanIdentifiers,
        *,
        user_id: uuid.UUID | None,
        reason: str,
        created_by: uuid.UUID | None,
    ) -> uuid.UUID: ...

    async def lift_bans(self, ids: BanIdentifiers, now: datetime) -> int: ...

    async def add_audit(
        self,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any],
    ) -> None: ...

    async def add_risk_signal(
        self, user_id: uuid.UUID, signal_type: str, weight: int, details: dict[str, Any]
    ) -> None: ...

    async def count_risk_signals(self, user_id: uuid.UUID) -> int: ...


def _matches(ids: BanIdentifiers) -> list[Any]:
    conditions = []
    if ids.telegram_uid:
        conditions.append(BanRow.telegram_uid == ids.telegram_uid)
    if ids.phone_hash:
        conditions.append(BanRow.phone_hash == ids.phone_hash)
    if ids.identity_hash:
        conditions.append(BanRow.identity_hash == ids.identity_hash)
    return conditions


class SqlModerationRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def find_active_ban(self, ids: BanIdentifiers, now: datetime) -> BanOut | None:
        conditions = _matches(ids)
        if not conditions:
            return None
        row = await self._s.scalar(
            select(BanRow)
            .where(
                or_(*conditions),
                BanRow.lifted_at.is_(None),
                or_(BanRow.expires_at.is_(None), BanRow.expires_at > now),
            )
            .order_by(BanRow.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return BanOut(id=row.id, user_id=row.user_id, reason=row.reason, created_at=row.created_at)

    async def add_ban(
        self,
        ids: BanIdentifiers,
        *,
        user_id: uuid.UUID | None,
        reason: str,
        created_by: uuid.UUID | None,
    ) -> uuid.UUID:
        row = BanRow(
            user_id=user_id,
            telegram_uid=ids.telegram_uid,
            phone_hash=ids.phone_hash,
            identity_hash=ids.identity_hash,
            reason=reason,
            created_by=created_by,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def lift_bans(self, ids: BanIdentifiers, now: datetime) -> int:
        conditions = _matches(ids)
        if not conditions:
            return 0
        result = await self._s.execute(
            update(BanRow).where(or_(*conditions), BanRow.lifted_at.is_(None)).values(lifted_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def add_audit(
        self,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any],
    ) -> None:
        self._s.add(
            AuditRow(
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
            )
        )
        await self._s.flush()

    async def add_risk_signal(
        self, user_id: uuid.UUID, signal_type: str, weight: int, details: dict[str, Any]
    ) -> None:
        self._s.add(
            RiskSignalRow(user_id=user_id, signal_type=signal_type, weight=weight, details=details)
        )
        await self._s.flush()

    async def count_risk_signals(self, user_id: uuid.UUID) -> int:
        count = await self._s.scalar(
            select(func.count()).select_from(RiskSignalRow).where(RiskSignalRow.user_id == user_id)
        )
        return int(count or 0)
