"""ORM-моделі модуля contracts. Лише для repository.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, SmallInteger, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from domovyk.core.db import Base
from domovyk.domain.contracts import ContractSource, ContractStatus, PartyRole, SignatureCheck


def _pg_enum[E: (ContractSource, ContractStatus, PartyRole, SignatureCheck)](
    enum: type[E], name: str
) -> Enum:
    return Enum(
        enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
        validate_strings=True,
    )


class ContractPartyRow(Base):
    __tablename__ = "contract_parties"

    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leases.id"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    party_role: Mapped[PartyRole] = mapped_column(_pg_enum(PartyRole, "party_role"))
    full_name: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContractRow(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leases.id"))
    kind: Mapped[str] = mapped_column(Text, server_default="lease")
    source: Mapped[ContractSource] = mapped_column(_pg_enum(ContractSource, "contract_source"))
    template_code: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    pdf_storage_key: Mapped[str] = mapped_column(Text)
    pdf_sha256: Mapped[str] = mapped_column(Text)
    status: Mapped[ContractStatus] = mapped_column(_pg_enum(ContractStatus, "contract_status"))
    data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    conditions_hash: Mapped[str] = mapped_column(Text)
    void_reason: Mapped[str | None] = mapped_column(Text)
    external_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignatureRow(Base):
    __tablename__ = "contract_signatures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    party_role: Mapped[PartyRole] = mapped_column(_pg_enum(PartyRole, "party_role"))
    signature_storage_key: Mapped[str] = mapped_column(Text)
    signature_filename: Mapped[str] = mapped_column(Text)
    signer_name: Mapped[str | None] = mapped_column(Text)
    signer_tax_id_hash: Mapped[str | None] = mapped_column(Text)
    signed_at_claimed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_status: Mapped[SignatureCheck] = mapped_column(
        _pg_enum(SignatureCheck, "signature_check")
    )
    check_details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text)
