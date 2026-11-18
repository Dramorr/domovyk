"""ORM-моделі модуля showings. Лише для repository.py."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, SmallInteger, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.showings import SlotStatus

_STATUS = Enum(
    SlotStatus,
    name="slot_status",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
    validate_strings=True,
)


class SlotRow(Base):
    __tablename__ = "showing_slots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("listings.id"))
    host_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    status: Mapped[SlotStatus] = mapped_column(_STATUS, server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
