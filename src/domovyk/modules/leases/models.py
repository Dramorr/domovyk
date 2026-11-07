"""ORM-моделі модуля leases. Лише для repository.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.leases import LeaseOrigin, LeaseStatus, MemberRole, PayMethod


def _pg_enum[E: (LeaseOrigin, LeaseStatus, MemberRole, PayMethod)](
    enum: type[E], name: str
) -> Enum:
    return Enum(
        enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
        validate_strings=True,
    )


class LeaseRow(Base):
    __tablename__ = "leases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id"))
    landlord_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    origin: Mapped[LeaseOrigin] = mapped_column(_pg_enum(LeaseOrigin, "lease_origin"))
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[LeaseStatus] = mapped_column(
        _pg_enum(LeaseStatus, "lease_status"), server_default=LeaseStatus.DRAFT.value
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    rent_amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(
        Enum("UAH", "USD", "EUR", name="currency_code", create_type=False), server_default="UAH"
    )
    rent_due_day: Mapped[int] = mapped_column(SmallInteger)
    deposit_amount: Mapped[int | None] = mapped_column(Integer)
    pay_method: Mapped[PayMethod] = mapped_column(_pg_enum(PayMethod, "pay_method"))
    pay_details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    pay_details_enc: Mapped[str | None] = mapped_column(Text)
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    landlord_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    proposal_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    proposal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeaseMemberRow(Base):
    __tablename__ = "lease_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    member_role: Mapped[MemberRole] = mapped_column(_pg_enum(MemberRole, "member_role"))
    utility_share: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LeaseInviteRow(Base):
    __tablename__ = "lease_invites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    member_role: Mapped[MemberRole] = mapped_column(_pg_enum(MemberRole, "member_role"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeaseHiddenRow(Base):
    """Учасник прибрав скасовану оренду зі свого списку (FR-LSE-9)."""

    __tablename__ = "lease_hidden"

    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
