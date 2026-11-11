"""Фейковий репозиторій модуля payments для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from domovyk.domain.payments import ChargeStatus, PaymentStatus
from domovyk.modules.payments.repository import (
    ChargeRecord,
    NewCharge,
    NewPayment,
    PaymentRecord,
)


@dataclass
class PaymentDb:
    charges: dict[uuid.UUID, ChargeRecord] = field(default_factory=dict)
    payments: dict[uuid.UUID, PaymentRecord] = field(default_factory=dict)
    seq: int = 0


class FakePaymentsRepository:
    def __init__(self, db: PaymentDb) -> None:
        self.db = db

    async def charges(self, lease_id: uuid.UUID) -> list[ChargeRecord]:
        items = [c for c in self.db.charges.values() if c.lease_id == lease_id]
        return sorted(items, key=lambda c: (c.due_date, c.kind), reverse=True)

    async def charge(self, charge_id: uuid.UUID) -> ChargeRecord | None:
        return self.db.charges.get(charge_id)

    async def add_charge(self, data: NewCharge) -> bool:
        if any(
            c.lease_id == data.lease_id
            and c.period_month == data.period_month
            and c.kind == data.kind
            for c in self.db.charges.values()
        ):
            return False
        cid = uuid.uuid4()
        self.db.charges[cid] = ChargeRecord(id=cid, **dataclasses.asdict(data))
        return True

    async def delete_charge(self, charge_id: uuid.UUID) -> None:
        self.db.charges.pop(charge_id, None)
        for pid in [p.id for p in self.db.payments.values() if p.charge_id == charge_id]:
            del self.db.payments[pid]

    async def set_charge_status(self, charge_id: uuid.UUID, status: ChargeStatus) -> None:
        self.db.charges[charge_id] = dataclasses.replace(self.db.charges[charge_id], status=status)

    async def payments(self, charge_ids: list[uuid.UUID]) -> list[PaymentRecord]:
        return [p for p in self.db.payments.values() if p.charge_id in charge_ids]

    async def payment(self, payment_id: uuid.UUID) -> PaymentRecord | None:
        return self.db.payments.get(payment_id)

    async def add_payment(self, data: NewPayment) -> uuid.UUID:
        pid = uuid.uuid4()
        self.db.seq += 1
        values = dataclasses.asdict(data)
        self.db.payments[pid] = PaymentRecord(
            id=pid,
            dispute_reason=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self.db.seq),
            **values,
        )
        return pid

    async def update_payment(self, payment_id: uuid.UUID, **values: Any) -> None:
        known = {k: v for k, v in values.items() if k in PaymentRecord.__dataclass_fields__}
        self.db.payments[payment_id] = dataclasses.replace(self.db.payments[payment_id], **known)

    async def receipt_exists(self, sha256: str) -> bool:
        return any(p.receipt_sha256 == sha256 for p in self.db.payments.values())

    async def reference_exists(self, reference: str) -> bool:
        return any(p.bank_reference == reference for p in self.db.payments.values())

    async def auto_accepted_before(self, before: datetime) -> list[PaymentRecord]:
        return [
            p
            for p in self.db.payments.values()
            if p.status is PaymentStatus.AUTO_ACCEPTED
            and p.dispute_deadline is not None
            and p.dispute_deadline <= before
        ]

    async def pending_before(self, before: datetime) -> list[PaymentRecord]:
        return [
            p
            for p in self.db.payments.values()
            if p.status is PaymentStatus.PENDING_LANDLORD and p.created_at < before
        ]
