"""ORM-моделі модуля properties: адреси, об'єкти, юніти, перевірки власності.

Лише для repository.py цього модуля. Колонку buildings.location (PostGIS) ORM
не мапить — її пише й читає repository.py через SQL-функції PostGIS.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from domovyk.domain.properties import OwnershipStatus, PropertyKind, UnitKind, VerificationStatus
from domovyk.domain.users import IdentityProvider


def _pg_enum[E: (PropertyKind, UnitKind, OwnershipStatus, VerificationStatus, IdentityProvider)](
    enum: type[E], name: str
) -> Enum:
    return Enum(
        enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
        validate_strings=True,
    )


class CityRow(Base):
    """Довідник міст (ADR 0008). status: active — працюємо, soon — скоро."""

    __tablename__ = "cities"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    region: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="soon")
    timezone: Mapped[str] = mapped_column(Text, server_default="Europe/Kyiv")
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DistrictRow(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city_code: Mapped[str] = mapped_column(Text, ForeignKey("cities.code"))
    name: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("districts.id"))


class BuildingRow(Base):
    __tablename__ = "buildings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    city_code: Mapped[str] = mapped_column(Text, ForeignKey("cities.code"))
    street: Mapped[str] = mapped_column(Text)
    house_number: Mapped[str] = mapped_column(Text)
    normalized_key: Mapped[str] = mapped_column(Text, unique=True)
    district_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("districts.id"))
    geocoder_place_id: Mapped[str | None] = mapped_column(Text)
    floors_total: Mapped[int | None] = mapped_column(SmallInteger)
    has_backup_power: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PropertyRow(Base):
    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kind: Mapped[PropertyKind] = mapped_column(
        _pg_enum(PropertyKind, "property_kind"), server_default=PropertyKind.APARTMENT.value
    )
    building_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("buildings.id"))
    apartment_number: Mapped[str | None] = mapped_column(Text)
    apartment_number_norm: Mapped[str | None] = mapped_column(Text)
    entrance: Mapped[str | None] = mapped_column(Text)
    floor: Mapped[int | None] = mapped_column(SmallInteger)
    rooms: Mapped[int] = mapped_column(SmallInteger)
    area_total: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    ownership_status: Mapped[OwnershipStatus] = mapped_column(
        _pg_enum(OwnershipStatus, "ownership_status"),
        server_default=OwnershipStatus.UNVERIFIED.value,
    )
    ownership_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    # Анкета об'єкта (FR-PRP-8): PropertyDetails + "v" (версія анкети).
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UnitRow(Base):
    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    kind: Mapped[UnitKind] = mapped_column(
        _pg_enum(UnitKind, "unit_kind"), server_default=UnitKind.ENTIRE.value
    )
    name: Mapped[str] = mapped_column(Text)
    area: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerificationRow(Base):
    """user_verifications. Модуль properties працює лише з рядками kind='ownership'."""

    __tablename__ = "user_verifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(
        Enum(
            "phone",
            "identity",
            "ownership",
            name="verification_kind",
            create_type=False,
        )
    )
    status: Mapped[VerificationStatus] = mapped_column(
        _pg_enum(VerificationStatus, "verification_status"),
        server_default=VerificationStatus.PENDING.value,
    )
    provider: Mapped[IdentityProvider | None] = mapped_column(
        _pg_enum(IdentityProvider, "identity_provider")
    )
    identity_hash: Mapped[str | None] = mapped_column(Text)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    owner_name_in_doc: Mapped[str | None] = mapped_column(Text)
    is_dev_bypass: Mapped[bool] = mapped_column(Boolean, server_default="false")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reject_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationDocumentRow(Base):
    __tablename__ = "verification_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_verifications.id", ondelete="CASCADE")
    )
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PropertyPhotoRow(Base):
    __tablename__ = "property_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    position: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    phash: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
