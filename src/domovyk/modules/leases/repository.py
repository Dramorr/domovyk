"""Доступ до leases, lease_members, lease_invites. Лише всередині модуля leases."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.domain.leases import OPEN_STATUSES, LeaseOrigin, LeaseStatus, MemberRole, PayMethod
from domovyk.modules.leases.models import (
    LeaseHiddenRow,
    LeaseInviteRow,
    LeaseMemberRow,
    LeaseRow,
)


@dataclass(frozen=True)
class LeaseRecord:
    id: uuid.UUID
    unit_id: uuid.UUID
    landlord_user_id: uuid.UUID
    origin: LeaseOrigin
    status: LeaseStatus
    start_date: date
    end_date: date
    rent_amount: int
    currency: str
    rent_due_day: int
    deposit_amount: int | None
    pay_method: PayMethod
    pay_details: dict[str, Any]
    pay_details_enc: str | None
    terms: dict[str, Any]
    terms_accepted_at: datetime | None
    created_at: datetime
    # FR-LSE-10: погодження обома сторонами й пропозиції змін.
    landlord_accepted_at: datetime | None = None
    proposal: dict[str, Any] | None = None
    proposal_by: uuid.UUID | None = None
    proposal_at: datetime | None = None
    declined_by: uuid.UUID | None = None
    # FR-LSE-7: розірвання.
    termination_date: date | None = None
    termination_reason: str | None = None
    termination_by: uuid.UUID | None = None
    termination_requested_at: datetime | None = None


@dataclass(frozen=True)
class MemberRecord:
    user_id: uuid.UUID
    role: MemberRole
    joined_at: datetime
    left_at: datetime | None


@dataclass(frozen=True)
class InviteRecord:
    id: uuid.UUID
    lease_id: uuid.UUID
    role: MemberRole
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class NewLease:
    unit_id: uuid.UUID
    landlord_user_id: uuid.UUID
    origin: LeaseOrigin
    status: LeaseStatus
    start_date: date
    end_date: date
    rent_amount: int
    rent_due_day: int
    deposit_amount: int | None
    pay_method: PayMethod
    pay_details: dict[str, Any]
    pay_details_enc: str | None
    terms: dict[str, Any]


class LeasesRepository(Protocol):
    async def create(self, data: NewLease) -> uuid.UUID: ...
    async def get(self, lease_id: uuid.UUID) -> LeaseRecord | None: ...
    async def update(self, lease_id: uuid.UUID, **values: Any) -> None: ...
    async def for_user(self, user_id: uuid.UUID) -> list[LeaseRecord]: ...
    async def hide(self, lease_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None: ...
    async def purgeable(self, cancelled_before: datetime) -> list[uuid.UUID]: ...
    async def delete(self, lease_id: uuid.UUID) -> None: ...
    async def due_to_finish(self, today: date) -> list[uuid.UUID]: ...
    async def ids_with_status(self, statuses: list[LeaseStatus]) -> list[uuid.UUID]: ...
    async def open_unit_ids(self, unit_ids: list[uuid.UUID]) -> set[uuid.UUID]: ...
    async def statuses_on_units(
        self, unit_ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, LeaseStatus]]: ...
    async def count_open_for_user(self, user_id: uuid.UUID) -> int: ...
    async def count_open_as_responsible(self, user_id: uuid.UUID) -> int: ...
    async def members(self, lease_id: uuid.UUID) -> list[MemberRecord]: ...
    async def add_member(
        self, lease_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole, at: datetime
    ) -> None: ...
    async def mark_left(self, lease_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None: ...
    async def add_invite(
        self,
        lease_id: uuid.UUID,
        token_hash: str,
        role: MemberRole,
        created_by: uuid.UUID,
        expires_at: datetime,
    ) -> None: ...
    async def invite_by_hash(self, token_hash: str) -> InviteRecord | None: ...
    async def use_invite(self, invite_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None: ...
    async def revoke_invites(
        self, lease_id: uuid.UUID, role: MemberRole | None, at: datetime
    ) -> None: ...
    async def pending_invites(self, lease_id: uuid.UUID, now: datetime) -> list[InviteRecord]: ...


def _lease(r: LeaseRow) -> LeaseRecord:
    return LeaseRecord(
        id=r.id,
        unit_id=r.unit_id,
        landlord_user_id=r.landlord_user_id,
        origin=r.origin,
        status=r.status,
        start_date=r.start_date,
        end_date=r.end_date,
        rent_amount=r.rent_amount,
        currency=r.currency,
        rent_due_day=r.rent_due_day,
        deposit_amount=r.deposit_amount,
        pay_method=r.pay_method,
        pay_details=dict(r.pay_details),
        pay_details_enc=r.pay_details_enc,
        terms=dict(r.terms),
        terms_accepted_at=r.terms_accepted_at,
        created_at=r.created_at,
        landlord_accepted_at=r.landlord_accepted_at,
        proposal=dict(r.proposal) if r.proposal is not None else None,
        proposal_by=r.proposal_by,
        proposal_at=r.proposal_at,
        declined_by=r.declined_by,
        termination_date=r.termination_date,
        termination_reason=r.termination_reason,
        termination_by=r.termination_by,
        termination_requested_at=r.termination_requested_at,
    )


def _invite(r: LeaseInviteRow) -> InviteRecord:
    return InviteRecord(r.id, r.lease_id, r.member_role, r.expires_at, r.used_at, r.revoked_at)


class SqlLeasesRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def create(self, data: NewLease) -> uuid.UUID:
        row = LeaseRow(
            unit_id=data.unit_id,
            landlord_user_id=data.landlord_user_id,
            origin=data.origin,
            status=data.status,
            start_date=data.start_date,
            end_date=data.end_date,
            rent_amount=data.rent_amount,
            rent_due_day=data.rent_due_day,
            deposit_amount=data.deposit_amount,
            pay_method=data.pay_method,
            pay_details=data.pay_details,
            pay_details_enc=data.pay_details_enc,
            terms=data.terms,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def get(self, lease_id: uuid.UUID) -> LeaseRecord | None:
        row = await self._s.get(LeaseRow, lease_id, populate_existing=True)
        return _lease(row) if row else None

    async def update(self, lease_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(update(LeaseRow).where(LeaseRow.id == lease_id).values(**values))

    async def for_user(self, user_id: uuid.UUID) -> list[LeaseRecord]:
        member = select(LeaseMemberRow.lease_id).where(
            LeaseMemberRow.user_id == user_id, LeaseMemberRow.left_at.is_(None)
        )
        hidden = select(LeaseHiddenRow.lease_id).where(LeaseHiddenRow.user_id == user_id)
        rows = await self._s.scalars(
            select(LeaseRow)
            .where(
                or_(LeaseRow.landlord_user_id == user_id, LeaseRow.id.in_(member)),
                LeaseRow.id.not_in(hidden),
            )
            .order_by(LeaseRow.created_at.desc())
            .execution_options(populate_existing=True)
        )
        return [_lease(r) for r in rows]

    async def hide(self, lease_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None:
        await self._s.execute(
            insert(LeaseHiddenRow)
            .values(lease_id=lease_id, user_id=user_id, hidden_at=at)
            .on_conflict_do_nothing()
        )

    async def purgeable(self, cancelled_before: datetime) -> list[uuid.UUID]:
        """Скасовані оренди, які пора стерти: минув строк або всі учасники їх прибрали."""
        members = (
            select(func.count())
            .select_from(LeaseMemberRow)
            .where(LeaseMemberRow.lease_id == LeaseRow.id)
            .scalar_subquery()
        )
        hidden = (
            select(func.count())
            .select_from(LeaseHiddenRow)
            .where(LeaseHiddenRow.lease_id == LeaseRow.id)
            .scalar_subquery()
        )
        rows = await self._s.scalars(
            select(LeaseRow.id).where(
                LeaseRow.status == LeaseStatus.CANCELLED,
                # +1 — власник (він не в lease_members).
                or_(LeaseRow.cancelled_at < cancelled_before, hidden >= members + 1),
            )
        )
        return list(rows)

    async def ids_with_status(self, statuses: list[LeaseStatus]) -> list[uuid.UUID]:
        rows = await self._s.scalars(select(LeaseRow.id).where(LeaseRow.status.in_(statuses)))
        return list(rows)

    async def due_to_finish(self, today: date) -> list[uuid.UUID]:
        """Оренди, що мають стати завершеними: настала дата розірвання або минув строк."""
        rows = await self._s.scalars(
            select(LeaseRow.id).where(
                or_(
                    (LeaseRow.status == LeaseStatus.ENDING) & (LeaseRow.termination_date <= today),
                    (LeaseRow.status.in_([LeaseStatus.ACTIVE, LeaseStatus.ENDING]))
                    & (LeaseRow.end_date < today),
                )
            )
        )
        return list(rows)

    async def delete(self, lease_id: uuid.UUID) -> None:
        await self._s.execute(delete(LeaseRow).where(LeaseRow.id == lease_id))

    async def open_unit_ids(self, unit_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not unit_ids:
            return set()
        rows = await self._s.scalars(
            select(LeaseRow.unit_id).where(
                LeaseRow.unit_id.in_(unit_ids), LeaseRow.status.in_(list(OPEN_STATUSES))
            )
        )
        return set(rows)

    async def statuses_on_units(
        self, unit_ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, LeaseStatus]]:
        if not unit_ids:
            return []
        rows = await self._s.execute(
            select(LeaseRow.id, LeaseRow.status).where(LeaseRow.unit_id.in_(unit_ids))
        )
        return [(r[0], r[1]) for r in rows]

    async def count_open_for_user(self, user_id: uuid.UUID) -> int:
        member = select(LeaseMemberRow.lease_id).where(
            LeaseMemberRow.user_id == user_id, LeaseMemberRow.left_at.is_(None)
        )
        count = await self._s.scalar(
            select(func.count())
            .select_from(LeaseRow)
            .where(
                LeaseRow.status.in_(list(OPEN_STATUSES)),
                or_(LeaseRow.landlord_user_id == user_id, LeaseRow.id.in_(member)),
            )
        )
        return int(count or 0)

    async def count_open_as_responsible(self, user_id: uuid.UUID) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(LeaseRow)
            .join(LeaseMemberRow, LeaseMemberRow.lease_id == LeaseRow.id)
            .where(
                LeaseMemberRow.user_id == user_id,
                LeaseMemberRow.member_role == MemberRole.RESPONSIBLE,
                LeaseMemberRow.left_at.is_(None),
                LeaseRow.status.in_(list(OPEN_STATUSES)),
            )
        )
        return int(count or 0)

    async def members(self, lease_id: uuid.UUID) -> list[MemberRecord]:
        rows = await self._s.scalars(
            select(LeaseMemberRow)
            .where(LeaseMemberRow.lease_id == lease_id)
            .order_by(LeaseMemberRow.joined_at)
            .execution_options(populate_existing=True)
        )
        return [MemberRecord(r.user_id, r.member_role, r.joined_at, r.left_at) for r in rows]

    async def add_member(
        self, lease_id: uuid.UUID, user_id: uuid.UUID, role: MemberRole, at: datetime
    ) -> None:
        existing = await self._s.scalar(
            select(LeaseMemberRow).where(
                LeaseMemberRow.lease_id == lease_id, LeaseMemberRow.user_id == user_id
            )
        )
        if existing is not None:  # повернувся після виходу — той самий рядок (UNIQUE)
            existing.member_role = role
            existing.left_at = None
            existing.joined_at = at
        else:
            self._s.add(
                LeaseMemberRow(lease_id=lease_id, user_id=user_id, member_role=role, joined_at=at)
            )
        await self._s.flush()

    async def mark_left(self, lease_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None:
        await self._s.execute(
            update(LeaseMemberRow)
            .where(LeaseMemberRow.lease_id == lease_id, LeaseMemberRow.user_id == user_id)
            .values(left_at=at)
        )

    async def add_invite(
        self,
        lease_id: uuid.UUID,
        token_hash: str,
        role: MemberRole,
        created_by: uuid.UUID,
        expires_at: datetime,
    ) -> None:
        self._s.add(
            LeaseInviteRow(
                lease_id=lease_id,
                token_hash=token_hash,
                member_role=role,
                created_by=created_by,
                expires_at=expires_at,
            )
        )
        await self._s.flush()

    async def invite_by_hash(self, token_hash: str) -> InviteRecord | None:
        row = await self._s.scalar(
            select(LeaseInviteRow)
            .where(LeaseInviteRow.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        return _invite(row) if row else None

    async def use_invite(self, invite_id: uuid.UUID, user_id: uuid.UUID, at: datetime) -> None:
        await self._s.execute(
            update(LeaseInviteRow)
            .where(LeaseInviteRow.id == invite_id)
            .values(used_by=user_id, used_at=at)
        )

    async def revoke_invites(
        self, lease_id: uuid.UUID, role: MemberRole | None, at: datetime
    ) -> None:
        stmt = update(LeaseInviteRow).where(
            LeaseInviteRow.lease_id == lease_id,
            LeaseInviteRow.used_at.is_(None),
            LeaseInviteRow.revoked_at.is_(None),
        )
        if role is not None:
            stmt = stmt.where(LeaseInviteRow.member_role == role)
        await self._s.execute(stmt.values(revoked_at=at))

    async def pending_invites(self, lease_id: uuid.UUID, now: datetime) -> list[InviteRecord]:
        rows = await self._s.scalars(
            select(LeaseInviteRow).where(
                LeaseInviteRow.lease_id == lease_id,
                LeaseInviteRow.used_at.is_(None),
                LeaseInviteRow.revoked_at.is_(None),
                LeaseInviteRow.expires_at > now,
            )
        )
        return [_invite(r) for r in rows]
