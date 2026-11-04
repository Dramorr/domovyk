"""Публічний інтерфейс модуля moderation: бани (FR-USR-2, FR-CMP-4) і журнал дій (FR-USR-4).

Методи приймають `tx`, щоб працювати в транзакції бізнес-операції іншого модуля.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from domovyk.core.clock import Clock
from domovyk.core.errors import InvalidInput
from domovyk.core.tx import Tx
from domovyk.modules.moderation.repository import ModerationRepository
from domovyk.modules.moderation.schemas import AuditAction, BanIdentifiers, BanOut

__all__ = ["AuditAction", "BanIdentifiers", "BanOut", "ModerationService"]


class ModerationService:
    def __init__(self, repo: Callable[[Tx], ModerationRepository], clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    async def find_active_ban(self, tx: Tx, ids: BanIdentifiers) -> BanOut | None:
        if ids.is_empty():
            return None
        return await self._repo(tx).find_active_ban(ids, self._clock.now())

    async def ban(
        self,
        tx: Tx,
        ids: BanIdentifiers,
        *,
        reason: str,
        user_id: uuid.UUID | None,
        actor_id: uuid.UUID | None,
    ) -> uuid.UUID:
        if ids.is_empty():
            raise InvalidInput("Бан потребує хоча б одного ідентифікатора")
        reason = reason.strip()
        if not reason:
            raise InvalidInput("Потрібна причина бану", field="reason")
        repo = self._repo(tx)
        ban_id = await repo.add_ban(ids, user_id=user_id, reason=reason, created_by=actor_id)
        await self.audit(
            tx,
            actor_id,
            AuditAction.BAN_CREATE,
            "ban",
            str(ban_id),
            {"user_id": str(user_id) if user_id else None, "reason": reason},
        )
        return ban_id

    async def lift(
        self, tx: Tx, ids: BanIdentifiers, *, actor_id: uuid.UUID | None, user_id: uuid.UUID | None
    ) -> int:
        lifted = await self._repo(tx).lift_bans(ids, self._clock.now())
        if lifted:
            await self.audit(
                tx,
                actor_id,
                AuditAction.BAN_LIFT,
                "user" if user_id else "ban",
                str(user_id) if user_id else (ids.telegram_uid or "-"),
                {"lifted": lifted},
            )
        return lifted

    async def audit(
        self,
        tx: Tx,
        actor_id: uuid.UUID | None,
        action: AuditAction,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Запис у audit_log. Без персональних даних у details (лише ID і причини)."""
        await self._repo(tx).add_audit(
            actor_id, action.value, entity_type, entity_id, details or {}
        )
