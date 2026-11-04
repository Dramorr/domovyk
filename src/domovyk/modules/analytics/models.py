"""ORM-моделі модуля analytics. Лише для repository.py цього модуля."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(Text)
    props: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    source: Mapped[str] = mapped_column(Text, server_default="telegram_bot")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
