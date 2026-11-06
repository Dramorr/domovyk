"""Публічний інтерфейс модуля chat: розмова оренди (FR-CHT-1, FR-CHT-2).

Хто має право читати й писати, вирішує модуль leases (учасники оренди), тому
методи тут приймають уже перевірені `conversation_id` і `tx` бізнес-операції.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from domovyk.core.tx import Tx
from domovyk.domain.leases import clean_message
from domovyk.modules.chat.repository import ChatRepository, encode_system_params
from domovyk.modules.chat.schemas import MessageOut, MessagesPage, SystemCode

__all__ = ["ChatService", "MessageOut", "MessagesPage", "SystemCode"]

PAGE_LIMIT = 50


class ChatService:
    def __init__(self, repo: Callable[[Tx], ChatRepository]) -> None:
        self._repo = repo

    async def ensure_for_lease(self, tx: Tx, lease_id: uuid.UUID) -> uuid.UUID:
        repo = self._repo(tx)
        existing = await repo.conversation_for_lease(lease_id)
        return existing or await repo.create_for_lease(lease_id)

    async def conversation_for_lease(self, tx: Tx, lease_id: uuid.UUID) -> uuid.UUID | None:
        return await self._repo(tx).conversation_for_lease(lease_id)

    async def post(
        self, tx: Tx, conversation_id: uuid.UUID, sender_id: uuid.UUID, body: str
    ) -> MessageOut:
        repo = self._repo(tx)
        message = await repo.add(conversation_id, sender_id, clean_message(body), None)
        await repo.set_read(conversation_id, sender_id, message.id)  # своє — прочитане
        return message

    async def system(
        self,
        tx: Tx,
        conversation_id: uuid.UUID,
        code: SystemCode,
        params: dict[str, object] | None = None,
    ) -> MessageOut:
        body = encode_system_params(params) if params else None
        return await self._repo(tx).add(conversation_id, None, body, code.value)

    async def page(
        self,
        tx: Tx,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        after_id: int = 0,
        before_id: int | None = None,
        limit: int = PAGE_LIMIT,
    ) -> MessagesPage:
        repo = self._repo(tx)
        limit = max(1, min(limit, PAGE_LIMIT))
        messages = await repo.list(
            conversation_id, after_id=after_id, before_id=before_id, limit=limit + 1
        )
        has_more = len(messages) > limit
        if has_more:
            # Для «новіших за after_id» зайве — останнє; для історії — найстаріше.
            messages = messages[:limit] if after_id and before_id is None else messages[1:]
        return MessagesPage(
            messages=messages,
            last_read_message_id=await repo.last_read(conversation_id, user_id),
            has_more=has_more,
        )

    async def mark_read(
        self, tx: Tx, conversation_id: uuid.UUID, user_id: uuid.UUID, message_id: int
    ) -> None:
        await self._repo(tx).set_read(conversation_id, user_id, message_id)

    async def unread(self, tx: Tx, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int:
        return await self._repo(tx).unread_count(conversation_id, user_id)
