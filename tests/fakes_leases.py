"""Фейки модулів leases і chat для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from domovyk.domain.leases import OPEN_STATUSES, LeaseOrigin, MemberRole
from domovyk.modules.chat.repository import encode_system_params
from domovyk.modules.chat.schemas import MessageOut, SystemCode
from domovyk.modules.leases.repository import (
    InviteRecord,
    LeaseRecord,
    MemberRecord,
    NewLease,
)

_T0 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


@dataclass
class _Invite:
    rec: InviteRecord
    token_hash: str
    created_by: uuid.UUID
    used_by: uuid.UUID | None = None


@dataclass
class LeaseDb:
    leases: dict[uuid.UUID, LeaseRecord] = field(default_factory=dict)
    members: dict[uuid.UUID, list[MemberRecord]] = field(default_factory=dict)
    invites: list[_Invite] = field(default_factory=list)
    conversations: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)  # lease → conv
    messages: dict[uuid.UUID, list[tuple[MessageOut, uuid.UUID | None]]] = field(
        default_factory=dict
    )
    reads: dict[tuple[uuid.UUID, uuid.UUID], int] = field(default_factory=dict)
    seq: int = 0


class FakeLeasesRepository:
    def __init__(self, db: LeaseDb) -> None:
        self.db = db

    async def create(self, data: NewLease) -> uuid.UUID:
        rec = LeaseRecord(
            id=uuid.uuid4(),
            unit_id=data.unit_id,
            landlord_user_id=data.landlord_user_id,
            origin=data.origin or LeaseOrigin.INVITE,
            status=data.status,
            start_date=data.start_date,
            end_date=data.end_date,
            rent_amount=data.rent_amount,
            currency="UAH",
            rent_due_day=data.rent_due_day,
            deposit_amount=data.deposit_amount,
            pay_method=data.pay_method,
            pay_details=dict(data.pay_details),
            pay_details_enc=data.pay_details_enc,
            terms=dict(data.terms),
            terms_accepted_at=None,
            created_at=_T0,
        )
        self.db.leases[rec.id] = rec
        self.db.members[rec.id] = []
        return rec.id

    async def get(self, lease_id: uuid.UUID) -> LeaseRecord | None:
        return self.db.leases.get(lease_id)

    async def update(self, lease_id: uuid.UUID, **values: Any) -> None:
        known = {k: v for k, v in values.items() if k in LeaseRecord.__dataclass_fields__}
        self.db.leases[lease_id] = replace(self.db.leases[lease_id], **known)

    async def for_user(self, user_id: uuid.UUID) -> list[LeaseRecord]:
        return [
            lease
            for lease in self.db.leases.values()
            if lease.landlord_user_id == user_id
            or any(m.user_id == user_id and m.left_at is None for m in self.db.members[lease.id])
        ]

    async def open_unit_ids(self, unit_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return {
            lease.unit_id
            for lease in self.db.leases.values()
            if lease.unit_id in unit_ids and lease.status in OPEN_STATUSES
        }

    async def count_open_for_user(self, user_id: uuid.UUID) -> int:
        return sum(1 for lease in await self.for_user(user_id) if lease.status in OPEN_STATUSES)

    async def members(self, lease_id: uuid.UUID) -> list[MemberRecord]:
        return list(self.db.members[lease_id])

    async def add_member(
        self, lease_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole, at: datetime
    ) -> None:
        rows = [m for m in self.db.members[lease_id] if m.user_id != user_id]
        if role is MemberRole.RESPONSIBLE and any(
            m.role is MemberRole.RESPONSIBLE and m.left_at is None for m in rows
        ):
            raise AssertionError("uq_one_responsible порушено")
        rows.append(MemberRecord(user_id, role, at, None))
        self.db.members[lease_id] = rows

    async def mark_left(self, lease_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None:
        self.db.members[lease_id] = [
            replace(m, left_at=at) if m.user_id == user_id else m for m in self.db.members[lease_id]
        ]

    async def add_invite(
        self,
        lease_id: uuid.UUID,
        token_hash: str,
        role: MemberRole,
        created_by: uuid.UUID,
        expires_at: datetime,
    ) -> None:
        rec = InviteRecord(uuid.uuid4(), lease_id, role, expires_at, None, None)
        self.db.invites.append(_Invite(rec, token_hash, created_by))

    async def invite_by_hash(self, token_hash: str) -> InviteRecord | None:
        return next((i.rec for i in self.db.invites if i.token_hash == token_hash), None)

    async def use_invite(self, invite_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None:
        for i in self.db.invites:
            if i.rec.id == invite_id:
                i.rec = replace(i.rec, used_at=at)
                i.used_by = user_id

    async def revoke_invites(
        self, lease_id: uuid.UUID, role: MemberRole | None, at: datetime
    ) -> None:
        for i in self.db.invites:
            r = i.rec
            if (
                r.lease_id == lease_id
                and r.used_at is None
                and r.revoked_at is None
                and (role is None or r.role is role)
            ):
                i.rec = replace(r, revoked_at=at)

    async def pending_invites(self, lease_id: uuid.UUID, now: datetime) -> list[InviteRecord]:
        return [
            i.rec
            for i in self.db.invites
            if i.rec.lease_id == lease_id
            and i.rec.used_at is None
            and i.rec.revoked_at is None
            and i.rec.expires_at > now
        ]


class FakeChatRepository:
    def __init__(self, db: LeaseDb) -> None:
        self.db = db

    async def conversation_for_lease(self, lease_id: uuid.UUID) -> uuid.UUID | None:
        return self.db.conversations.get(lease_id)

    async def create_for_lease(self, lease_id: uuid.UUID) -> uuid.UUID:
        cid = uuid.uuid4()
        self.db.conversations[lease_id] = cid
        self.db.messages[cid] = []
        return cid

    async def add(
        self,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID | None,
        body: str | None,
        system_code: str | None,
    ) -> MessageOut:
        self.db.seq += 1
        params: dict[str, object] = {}
        text = body
        if system_code and body:
            import json

            params = json.loads(body.removeprefix("sys:"))
            text = None
        msg = MessageOut(
            id=self.db.seq,
            sender_id=sender_id,
            body=text,
            system_code=SystemCode(system_code) if system_code else None,
            system_params=params,
            created_at=_T0,
        )
        self.db.messages[conversation_id].append((msg, sender_id))
        return msg

    async def list(
        self, conversation_id: uuid.UUID, *, after_id: int, before_id: int | None, limit: int
    ) -> list[MessageOut]:
        msgs = [m for m, _ in self.db.messages[conversation_id] if m.id > after_id]
        if before_id is not None:
            msgs = [m for m in msgs if m.id < before_id][-limit:]
        elif after_id == 0:
            msgs = msgs[-limit:]
        else:
            msgs = msgs[:limit]
        return msgs

    async def last_read(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int:
        return self.db.reads.get((conversation_id, user_id), 0)

    async def set_read(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, message_id: int
    ) -> None:
        key = (conversation_id, user_id)
        self.db.reads[key] = max(self.db.reads.get(key, 0), message_id)

    async def unread_count(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> int:
        last = await self.last_read(conversation_id, user_id)
        return sum(
            1
            for m, sender in self.db.messages[conversation_id]
            if m.id > last and sender != user_id
        )


__all__ = ["FakeChatRepository", "FakeLeasesRepository", "LeaseDb", "encode_system_params"]
