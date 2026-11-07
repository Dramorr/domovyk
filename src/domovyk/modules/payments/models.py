"""ORM-моделі модуля payments. Лише для repository.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ARRAY, Date, DateTime, Enum, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.leases import PayMethod
from domovyk.domain.payments import ChargeStatus, PaymentStatus


def _pg_enum[E: (ChargeStatus, PaymentStatus, PayMethod)](enum: type[E], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
        validate_strings=True,
    )


class ChargeRow(Base):
    __tablename__ = "rent_charges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leases.id"))
    period_month: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(Text, server_default="rent")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(
        Enum("UAH", "USD", "EUR", name="currency_code", create_type=False), server_default="UAH"
    )
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[ChargeStatus] = mapped_column(_pg_enum(ChargeStatus, "charge_status"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentRow(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rent_charges.id"))
    payer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    method: Mapped[PayMethod] = mapped_column(_pg_enum(PayMethod, "pay_method"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_storage_key: Mapped[str | None] = mapped_column(Text)
    receipt_filename: Mapped[str | None] = mapped_column(Text)
    receipt_mime: Mapped[str | None] = mapped_column(Text)
    receipt_sha256: Mapped[str | None] = mapped_column(Text)
    bank_reference: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    mismatches: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PaymentStatus] = mapped_column(_pg_enum(PaymentStatus, "payment_status"))
    dispute_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispute_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
