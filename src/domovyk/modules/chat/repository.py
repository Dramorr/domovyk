"""Доступ до conversations, messages, conversation_reads. Лише всередині модуля chat."""

from __future__ import annotations

import json
import uuid
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.modules.chat.models import ConversationReadRow, ConversationRow, MessageRow
from domovyk.modules.chat.schemas import MessageOut, SystemCode

# Параметри системного повідомлення лежать у body як JSON (тексту там немає).
_SYS_PREFIX = "sys:"


class ChatRepository(Protocol):
    async def conversation_for_lease(self, lease_id: uuid.UUID) -> uuid.UUID | None: ...
    async def create_for_lease(self, lease_id: uuid.UUID) -> uuid.UUID: ...
    async def add(
        self,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID | None,
        body: str | None,
        system_code: str | None,
    ) -> MessageOut: ...
    async def list(
        self, conversation_id: uuid.UUID, *, after_id: int, before_id: int | None, limit: int
    ) -> list[MessageOut]: ...
    async def last_read(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int: ...
    async def set_read(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, message_id: int
    ) -> None: ...
    async def unread_count(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int: ...


def _out(row: MessageRow) -> MessageOut:
    params: dict[str, object] = {}
    body = row.body
    if row.system_code and body and body.startswith(_SYS_PREFIX):
        params = json.loads(body.removeprefix(_SYS_PREFIX))
        body = None
    return MessageOut(
        id=row.id,
        sender_id=row.sender_id,
        body=body,
        system_code=SystemCode(row.system_code) if row.system_code else None,
        system_params=params,
        created_at=row.created_at,
    )


def encode_system_params(params: dict[str, object]) -> str:
    return _SYS_PREFIX + json.dumps(params, ensure_ascii=False)


class SqlChatRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def conversation_for_lease(self, lease_id: uuid.UUID) -> uuid.UUID | None:
        return await self._s.scalar(
            select(ConversationRow.id).where(ConversationRow.lease_id == lease_id)
        )

    async def create_for_lease(self, lease_id: uuid.UUID) -> uuid.UUID:
        row = ConversationRow(lease_id=lease_id)
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def add(
        self,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID | None,
        body: str | None,
        system_code: str | None,
    ) -> MessageOut:
        row = MessageRow(
            conversation_id=conversation_id,
            sender_id=sender_id,
            body=body,
            system_code=system_code,
        )
        self._s.add(row)
        await self._s.flush()
        await self._s.refresh(row)
        return _out(row)

    async def list(
        self, conversation_id: uuid.UUID, *, after_id: int, before_id: int | None, limit: int
    ) -> list[MessageOut]:
        stmt = select(MessageRow).where(
            MessageRow.conversation_id == conversation_id,
            MessageRow.id > after_id,
            MessageRow.deleted_at.is_(None),
        )
        if before_id is not None:
            # Сторінка назад: останні `limit` перед before_id, потім у хронологічному порядку.
            stmt = stmt.where(MessageRow.id < before_id).order_by(MessageRow.id.desc()).limit(limit)
            rows = list(await self._s.scalars(stmt))
            rows.reverse()
        else:
            if after_id == 0:
                stmt = stmt.order_by(MessageRow.id.desc()).limit(limit)
                rows = list(await self._s.scalars(stmt))
                rows.reverse()
            else:
                rows = list(await self._s.scalars(stmt.order_by(MessageRow.id).limit(limit)))
        return [_out(r) for r in rows]

    async def last_read(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int:
        value = await self._s.scalar(
            select(ConversationReadRow.last_read_message_id).where(
                ConversationReadRow.conversation_id == conversation_id,
                ConversationReadRow.user_id == user_id,
            )
        )
        return int(value or 0)

    async def set_read(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, message_id: int
    ) -> None:
        stmt = insert(ConversationReadRow).values(
            conversation_id=conversation_id, user_id=user_id, last_read_message_id=message_id
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[ConversationReadRow.conversation_id, ConversationReadRow.user_id],
                set_={
                    "last_read_message_id": func.greatest(
                        ConversationReadRow.last_read_message_id, message_id
                    ),
                    "updated_at": func.now(),
                },
            )
        )

    async def unread_count(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int:
        last = await self.last_read(conversation_id, user_id)
        count = await self._s.scalar(
            select(func.count())
            .select_from(MessageRow)
            .where(
                MessageRow.conversation_id == conversation_id,
                MessageRow.id > last,
                MessageRow.deleted_at.is_(None),
                (MessageRow.sender_id.is_(None)) | (MessageRow.sender_id != user_id),
            )
        )
        return int(count or 0)
