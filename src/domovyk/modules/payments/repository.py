"""Доступ до rent_charges і payments. Лише всередині модуля payments."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.domain.leases import PayMethod
from domovyk.domain.payments import ChargeStatus, PaymentStatus
from domovyk.modules.payments.models import ChargeRow, PaymentRow


@dataclass(frozen=True)
class ChargeRecord:
    id: uuid.UUID
    lease_id: uuid.UUID
    period_month: date
    kind: str
    amount: Decimal
    currency: str
    due_date: date
    status: ChargeStatus


@dataclass(frozen=True)
class NewCharge:
    lease_id: uuid.UUID
    period_month: date
    kind: str
    amount: Decimal
    currency: str
    due_date: date
    status: ChargeStatus


@dataclass(frozen=True)
class PaymentRecord:
    id: uuid.UUID
    charge_id: uuid.UUID
    payer_user_id: uuid.UUID
    method: PayMethod
    amount: Decimal
    paid_at: datetime | None
    receipt_storage_key: str | None
    receipt_filename: str | None
    receipt_mime: str | None
    receipt_sha256: str | None
    mismatches: list[str]
    note: str | None
    status: PaymentStatus
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    dispute_reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class NewPayment:
    charge_id: uuid.UUID
    payer_user_id: uuid.UUID
    method: PayMethod
    amount: Decimal
    paid_at: datetime | None
    receipt_storage_key: str | None
    receipt_filename: str | None
    receipt_mime: str | None
    receipt_sha256: str | None
    mismatches: list[str]
    note: str | None
    status: PaymentStatus
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None


class PaymentsRepository(Protocol):
    async def charges(self, lease_id: uuid.UUID) -> list[ChargeRecord]: ...
    async def charge(self, charge_id: uuid.UUID) -> ChargeRecord | None: ...
    async def add_charge(self, data: NewCharge) -> bool: ...
    async def set_charge_status(self, charge_id: uuid.UUID, status: ChargeStatus) -> None: ...
    async def payments(self, charge_ids: list[uuid.UUID]) -> list[PaymentRecord]: ...
    async def payment(self, payment_id: uuid.UUID) -> PaymentRecord | None: ...
    async def add_payment(self, data: NewPayment) -> uuid.UUID: ...
    async def update_payment(self, payment_id: uuid.UUID, **values: Any) -> None: ...
    async def receipt_exists(self, sha256: str) -> bool: ...
    async def pending_before(self, before: datetime) -> list[PaymentRecord]: ...


def _charge(r: ChargeRow) -> ChargeRecord:
    return ChargeRecord(
        id=r.id,
        lease_id=r.lease_id,
        period_month=r.period_month,
        kind=r.kind,
        amount=r.amount,
        currency=r.currency,
        due_date=r.due_date,
        status=r.status,
    )


def _payment(r: PaymentRow) -> PaymentRecord:
    return PaymentRecord(
        id=r.id,
        charge_id=r.charge_id,
        payer_user_id=r.payer_user_id,
        method=r.method,
        amount=r.amount,
        paid_at=r.paid_at,
        receipt_storage_key=r.receipt_storage_key,
        receipt_filename=r.receipt_filename,
        receipt_mime=r.receipt_mime,
        receipt_sha256=r.receipt_sha256,
        mismatches=list(r.mismatches or []),
        note=r.note,
        status=r.status,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        dispute_reason=r.dispute_reason,
        created_at=r.created_at,
    )


class SqlPaymentsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    async def charges(self, lease_id: uuid.UUID) -> list[ChargeRecord]:
        rows = await self._s.scalars(
            select(ChargeRow)
            .where(ChargeRow.lease_id == lease_id)
            .order_by(ChargeRow.due_date.desc(), ChargeRow.kind)
            .execution_options(populate_existing=True)
        )
        return [_charge(r) for r in rows]

    async def charge(self, charge_id: uuid.UUID) -> ChargeRecord | None:
        row = await self._s.get(ChargeRow, charge_id, populate_existing=True)
        return _charge(row) if row else None

    async def add_charge(self, data: NewCharge) -> bool:
        result = await self._s.execute(
            insert(ChargeRow)
            .values(
                lease_id=data.lease_id,
                period_month=data.period_month,
                kind=data.kind,
                amount=data.amount,
                currency=data.currency,
                due_date=data.due_date,
                status=data.status,
            )
            .on_conflict_do_nothing(
                index_elements=[ChargeRow.lease_id, ChargeRow.period_month, ChargeRow.kind]
            )
            .returning(ChargeRow.id)
        )
        return result.scalar() is not None

    async def set_charge_status(self, charge_id: uuid.UUID, status: ChargeStatus) -> None:
        await self._s.execute(
            update(ChargeRow).where(ChargeRow.id == charge_id).values(status=status)
        )

    async def payments(self, charge_ids: list[uuid.UUID]) -> list[PaymentRecord]:
        if not charge_ids:
            return []
        rows = await self._s.scalars(
            select(PaymentRow)
            .where(PaymentRow.charge_id.in_(charge_ids))
            .order_by(PaymentRow.created_at)
            .execution_options(populate_existing=True)
        )
        return [_payment(r) for r in rows]

    async def payment(self, payment_id: uuid.UUID) -> PaymentRecord | None:
        row = await self._s.get(PaymentRow, payment_id, populate_existing=True)
        return _payment(row) if row else None

    async def add_payment(self, data: NewPayment) -> uuid.UUID:
        row = PaymentRow(
            charge_id=data.charge_id,
            payer_user_id=data.payer_user_id,
            method=data.method,
            amount=data.amount,
            paid_at=data.paid_at,
            receipt_storage_key=data.receipt_storage_key,
            receipt_filename=data.receipt_filename,
            receipt_mime=data.receipt_mime,
            receipt_sha256=data.receipt_sha256,
            mismatches=data.mismatches,
            note=data.note,
            status=data.status,
            decided_by=data.decided_by,
            decided_at=data.decided_at,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def update_payment(self, payment_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(
            update(PaymentRow).where(PaymentRow.id == payment_id).values(**values)
        )

    async def receipt_exists(self, sha256: str) -> bool:
        found = await self._s.scalar(
            select(PaymentRow.id).where(PaymentRow.receipt_sha256 == sha256).limit(1)
        )
        return found is not None

    async def pending_before(self, before: datetime) -> list[PaymentRecord]:
        rows = await self._s.scalars(
            select(PaymentRow).where(
                PaymentRow.status == PaymentStatus.PENDING_LANDLORD,
                PaymentRow.created_at < before,
            )
        )
        return [_payment(r) for r in rows]
