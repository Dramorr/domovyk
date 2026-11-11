"""ORM-моделі модуля utilities. Лише для repository.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.contracts import PartyRole
from domovyk.domain.utilities import MeterKind


def _pg_enum[E: (MeterKind, PartyRole)](enum: type[E], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
        validate_strings=True,
    )


class ProviderRow(Base):
    __tablename__ = "utility_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    meter_kinds: Mapped[list[MeterKind]] = mapped_column(ARRAY(_pg_enum(MeterKind, "meter_kind")))
    window_rule: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    cabinet_url: Mapped[str | None] = mapped_column(Text)
    payment_url: Mapped[str | None] = mapped_column(Text)
    city_code: Mapped[str] = mapped_column(Text)


class MeterRow(Base):
    __tablename__ = "meters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id"))
    kind: Mapped[MeterKind] = mapped_column(_pg_enum(MeterKind, "meter_kind"))
    provider_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("utility_providers.id"))
    account_number: Mapped[str | None] = mapped_column(Text)
    serial_number: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    submits: Mapped[PartyRole] = mapped_column(_pg_enum(PartyRole, "party_role"))
    pays: Mapped[PartyRole] = mapped_column(_pg_enum(PartyRole, "party_role"))
    tariff: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReadingRow(Base):
    __tablename__ = "meter_readings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    meter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meters.id"))
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leases.id"))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    photo_key: Mapped[str | None] = mapped_column(Text)
    submitted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reading_date: Mapped[date] = mapped_column(Date)
    sent_to_provider_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
