"""ORM-моделі модуля listings. Лише для repository.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, SmallInteger, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.listings import ListingStatus


class ListingRow(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    price_amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(
        Enum("UAH", "USD", "EUR", name="currency_code", create_type=False), server_default="UAH"
    )
    utilities_included: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    deposit_amount: Mapped[int | None] = mapped_column(Integer)
    min_term_months: Mapped[int | None] = mapped_column(SmallInteger)
    available_from: Mapped[date | None] = mapped_column(Date)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(
            ListingStatus,
            name="listing_status",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
            validate_strings=True,
        ),
        server_default="draft",
    )
    owner_declaration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
