"""ORM-моделі модуля users. Лише для repository.py цього модуля."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.users import ConsentDocument, IdentityProvider, UserRole, UserStatus


def _pg_enum[E: (UserRole, UserStatus, IdentityProvider, ConsentDocument)](
    enum: type[E], name: str
) -> Enum:
    return Enum(
        enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
        validate_strings=True,
    )


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    display_name: Mapped[str] = mapped_column(Text)
    birth_date: Mapped[date | None] = mapped_column(Date)
    role: Mapped[UserRole] = mapped_column(
        _pg_enum(UserRole, "user_role"), server_default=UserRole.USER.value
    )
    status: Mapped[UserStatus] = mapped_column(
        _pg_enum(UserStatus, "user_status"), server_default=UserStatus.ACTIVE.value
    )
    locale: Mapped[str] = mapped_column(Text, server_default="uk")
    app_mode: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthIdentityRow(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_uid"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    provider: Mapped[IdentityProvider] = mapped_column(
        _pg_enum(IdentityProvider, "identity_provider")
    )
    provider_uid: Mapped[str] = mapped_column(Text)
    is_contact: Mapped[bool] = mapped_column(Boolean, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantProfileRow(Base):
    __tablename__ = "tenant_profiles"
    __table_args__ = (CheckConstraint("household_size BETWEEN 1 AND 20", name="household_size"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    about: Mapped[str | None] = mapped_column(Text)
    occupation: Mapped[str | None] = mapped_column(Text)
    household_size: Mapped[int | None] = mapped_column(SmallInteger)
    has_children: Mapped[bool | None] = mapped_column(Boolean)
    has_pets: Mapped[bool | None] = mapped_column(Boolean)
    pets_details: Mapped[str | None] = mapped_column(Text)
    smoker: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserConsentRow(Base):
    __tablename__ = "user_consents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    document: Mapped[ConsentDocument] = mapped_column(_pg_enum(ConsentDocument, "consent_document"))
    version: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
