"""DTO модуля payments — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from domovyk.domain.leases import PayMethod
from domovyk.domain.payments import ChargeStatus, PaymentStatus


class PaymentOut(BaseModel):
    id: uuid.UUID
    method: PayMethod
    amount: Decimal
    status: PaymentStatus
    payer_name: str
    has_receipt: bool
    receipt_mime: str | None
    mismatches: list[str]
    note: str | None
    dispute_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    # Автозарахування за квитанцією (FR-PAY-3): до цього часу власник може оскаржити.
    dispute_deadline: datetime | None = None
    receipt_bank: str | None = None
    receipt_reference: str | None = None
    operated_at: str | None = None


class ChargeOut(BaseModel):
    id: uuid.UUID
    kind: Literal["rent", "deposit", "utilities", "other"]
    period_month: date
    due_date: date
    amount: Decimal
    paid: Decimal
    remaining: Decimal
    status: ChargeStatus
    payments: list[PaymentOut]


class PaymentsOverview(BaseModel):
    """Блок «Оплати» в картці оренди (PAY-LIST)."""

    lease_id: uuid.UUID
    my_role: Literal["landlord", "responsible", "co_tenant"]
    pay_method: PayMethod
    next_charge: ChargeOut | None
    charges: list[ChargeOut]
    awaiting_my_decision: int


class PayInfo(BaseModel):
    """PAY-REQ: що й куди платити. Повні реквізити — лише учасникам оренди (FR-PAY-8)."""

    charge_id: uuid.UUID
    kind: str
    period_month: date
    amount: Decimal
    method: PayMethod
    card: str | None
    iban: str | None
    jar_url: str | None
    recipient: str | None
    street: str
    house_number: str
    apartment: str | None


class CashIn(BaseModel):
    amount: Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=300)


class DecisionIn(BaseModel):
    accept: bool
    reason: str | None = Field(default=None, max_length=500)
