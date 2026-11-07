"""Доступ до contracts, contract_signatures, contract_parties. Лише всередині модуля contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from domovyk.core.tx import Tx, session_of
from domovyk.domain.contracts import (
    LIVE_STATUSES,
    ContractSource,
    ContractStatus,
    PartyRole,
    SignatureCheck,
)
from domovyk.modules.contracts.models import ContractPartyRow, ContractRow, SignatureRow


@dataclass(frozen=True)
class ContractRecord:
    id: uuid.UUID
    lease_id: uuid.UUID
    source: ContractSource
    template_code: str | None
    version: int
    pdf_storage_key: str
    pdf_sha256: str
    status: ContractStatus
    data_snapshot: dict[str, Any]
    conditions_hash: str
    void_reason: str | None
    created_by: uuid.UUID
    created_at: datetime
    signed_at: datetime | None


@dataclass(frozen=True)
class NewContract:
    lease_id: uuid.UUID
    source: ContractSource
    template_code: str | None
    version: int
    pdf_storage_key: str
    pdf_sha256: str
    status: ContractStatus
    data_snapshot: dict[str, Any]
    conditions_hash: str
    created_by: uuid.UUID


@dataclass(frozen=True)
class SignatureRecord:
    id: uuid.UUID
    contract_id: uuid.UUID
    user_id: uuid.UUID
    party_role: PartyRole
    signature_storage_key: str
    signature_filename: str
    signer_name: str | None
    signer_tax_id_hash: str | None
    signed_at_claimed: datetime | None
    check_status: SignatureCheck
    check_details: dict[str, Any]
    uploaded_at: datetime
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_reason: str | None


@dataclass(frozen=True)
class NewSignature:
    contract_id: uuid.UUID
    user_id: uuid.UUID
    party_role: PartyRole
    signature_storage_key: str
    signature_filename: str
    signer_name: str | None
    signer_tax_id_hash: str | None
    signed_at_claimed: datetime | None
    check_status: SignatureCheck
    check_details: dict[str, Any]
    uploaded_at: datetime


class ContractsRepository(Protocol):
    async def party_names(self, lease_id: uuid.UUID) -> dict[uuid.UUID, str]: ...
    async def set_party_name(
        self, lease_id: uuid.UUID, user_id: uuid.UUID, role: PartyRole, name: str, at: datetime
    ) -> None: ...
    async def create(self, data: NewContract) -> uuid.UUID: ...
    async def get(self, contract_id: uuid.UUID) -> ContractRecord | None: ...
    async def current_for_lease(self, lease_id: uuid.UUID) -> ContractRecord | None: ...
    async def latest_version(self, lease_id: uuid.UUID) -> int: ...
    async def file_keys_for_lease(self, lease_id: uuid.UUID) -> list[str]: ...
    async def update(self, contract_id: uuid.UUID, **values: Any) -> None: ...
    async def live_older_than(self, before: datetime) -> list[ContractRecord]: ...
    async def signatures(self, contract_id: uuid.UUID) -> list[SignatureRecord]: ...
    async def signature(self, signature_id: uuid.UUID) -> SignatureRecord | None: ...
    async def upsert_signature(self, data: NewSignature) -> uuid.UUID: ...
    async def review_signature(
        self,
        signature_id: uuid.UUID,
        status: SignatureCheck,
        reviewer: uuid.UUID,
        at: datetime,
        reason: str | None,
    ) -> None: ...
    async def signatures_to_review(self, limit: int) -> list[SignatureRecord]: ...
    async def lease_ids_signed_by(
        self, tax_id_hash: str, role: PartyRole, exclude_lease: uuid.UUID
    ) -> list[uuid.UUID]: ...


def _contract(r: ContractRow) -> ContractRecord:
    return ContractRecord(
        id=r.id,
        lease_id=r.lease_id,
        source=r.source,
        template_code=r.template_code,
        version=r.version,
        pdf_storage_key=r.pdf_storage_key,
        pdf_sha256=r.pdf_sha256,
        status=r.status,
        data_snapshot=dict(r.data_snapshot or {}),
        conditions_hash=r.conditions_hash,
        void_reason=r.void_reason,
        created_by=r.created_by,
        created_at=r.created_at,
        signed_at=r.signed_at,
    )


def _signature(r: SignatureRow) -> SignatureRecord:
    return SignatureRecord(
        id=r.id,
        contract_id=r.contract_id,
        user_id=r.user_id,
        party_role=r.party_role,
        signature_storage_key=r.signature_storage_key,
        signature_filename=r.signature_filename,
        signer_name=r.signer_name,
        signer_tax_id_hash=r.signer_tax_id_hash,
        signed_at_claimed=r.signed_at_claimed,
        check_status=r.check_status,
        check_details=dict(r.check_details or {}),
        uploaded_at=r.uploaded_at,
        reviewed_by=r.reviewed_by,
        reviewed_at=r.reviewed_at,
        review_reason=r.review_reason,
    )


# Підписи, що чекають модератора (поки немає криптоперевірки — і auto_ok теж, ADR 0010).
REVIEW_STATUSES = (SignatureCheck.NOT_CHECKED, SignatureCheck.AUTO_OK, SignatureCheck.MISMATCH)


class SqlContractsRepository:
    def __init__(self, tx: Tx) -> None:
        self._s = session_of(tx)

    # -------------------------------------------------------------- сторони

    async def party_names(self, lease_id: uuid.UUID) -> dict[uuid.UUID, str]:
        rows = await self._s.scalars(
            select(ContractPartyRow).where(ContractPartyRow.lease_id == lease_id)
        )
        return {r.user_id: r.full_name for r in rows}

    async def set_party_name(
        self, lease_id: uuid.UUID, user_id: uuid.UUID, role: PartyRole, name: str, at: datetime
    ) -> None:
        stmt = insert(ContractPartyRow).values(
            lease_id=lease_id, user_id=user_id, party_role=role, full_name=name, updated_at=at
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[ContractPartyRow.lease_id, ContractPartyRow.user_id],
                set_={"full_name": name, "party_role": role, "updated_at": at},
            )
        )

    # ------------------------------------------------------------- договори

    async def create(self, data: NewContract) -> uuid.UUID:
        row = ContractRow(
            lease_id=data.lease_id,
            source=data.source,
            template_code=data.template_code,
            version=data.version,
            pdf_storage_key=data.pdf_storage_key,
            pdf_sha256=data.pdf_sha256,
            status=data.status,
            data_snapshot=data.data_snapshot,
            conditions_hash=data.conditions_hash,
            created_by=data.created_by,
        )
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def get(self, contract_id: uuid.UUID) -> ContractRecord | None:
        row = await self._s.get(ContractRow, contract_id)
        return _contract(row) if row else None

    async def current_for_lease(self, lease_id: uuid.UUID) -> ContractRecord | None:
        """Живий або підписаний договір оренди (не void)."""
        row = await self._s.scalar(
            select(ContractRow)
            .where(ContractRow.lease_id == lease_id, ContractRow.status != ContractStatus.VOID)
            .order_by(ContractRow.created_at.desc())
            .limit(1)
        )
        return _contract(row) if row else None

    async def latest_version(self, lease_id: uuid.UUID) -> int:
        value = await self._s.scalar(
            select(func.max(ContractRow.version)).where(ContractRow.lease_id == lease_id)
        )
        return int(value or 0)

    async def file_keys_for_lease(self, lease_id: uuid.UUID) -> list[str]:
        """Усі файли договорів оренди (PDF і підписи, разом зі старими версіями)."""
        pdfs = await self._s.scalars(
            select(ContractRow.pdf_storage_key).where(ContractRow.lease_id == lease_id)
        )
        sigs = await self._s.scalars(
            select(SignatureRow.signature_storage_key)
            .join(ContractRow, ContractRow.id == SignatureRow.contract_id)
            .where(ContractRow.lease_id == lease_id)
        )
        return [*pdfs, *sigs]

    async def update(self, contract_id: uuid.UUID, **values: Any) -> None:
        await self._s.execute(
            update(ContractRow).where(ContractRow.id == contract_id).values(**values)
        )

    async def live_older_than(self, before: datetime) -> list[ContractRecord]:
        rows = await self._s.scalars(
            select(ContractRow).where(
                ContractRow.status.in_(list(LIVE_STATUSES)), ContractRow.created_at < before
            )
        )
        return [_contract(r) for r in rows]

    # --------------------------------------------------------------- підписи

    async def signatures(self, contract_id: uuid.UUID) -> list[SignatureRecord]:
        rows = await self._s.scalars(
            select(SignatureRow).where(SignatureRow.contract_id == contract_id)
        )
        return [_signature(r) for r in rows]

    async def signature(self, signature_id: uuid.UUID) -> SignatureRecord | None:
        row = await self._s.get(SignatureRow, signature_id)
        return _signature(row) if row else None

    async def upsert_signature(self, data: NewSignature) -> uuid.UUID:
        values = {
            "contract_id": data.contract_id,
            "user_id": data.user_id,
            "party_role": data.party_role,
            "signature_storage_key": data.signature_storage_key,
            "signature_filename": data.signature_filename,
            "signer_name": data.signer_name,
            "signer_tax_id_hash": data.signer_tax_id_hash,
            "signed_at_claimed": data.signed_at_claimed,
            "check_status": data.check_status,
            "check_details": data.check_details,
            "uploaded_at": data.uploaded_at,
        }
        stmt = insert(SignatureRow).values(**values)
        reset = {k: v for k, v in values.items() if k not in ("contract_id", "user_id")}
        reset.update(reviewed_by=None, reviewed_at=None, review_reason=None)
        result = await self._s.scalar(
            stmt.on_conflict_do_update(
                index_elements=[SignatureRow.contract_id, SignatureRow.user_id], set_=reset
            ).returning(SignatureRow.id)
        )
        if result is None:
            raise RuntimeError("Підпис не збережено")
        return uuid.UUID(str(result))

    async def review_signature(
        self,
        signature_id: uuid.UUID,
        status: SignatureCheck,
        reviewer: uuid.UUID,
        at: datetime,
        reason: str | None,
    ) -> None:
        await self._s.execute(
            update(SignatureRow)
            .where(SignatureRow.id == signature_id)
            .values(check_status=status, reviewed_by=reviewer, reviewed_at=at, review_reason=reason)
        )

    async def signatures_to_review(self, limit: int) -> list[SignatureRecord]:
        rows = await self._s.scalars(
            select(SignatureRow)
            .join(ContractRow, ContractRow.id == SignatureRow.contract_id)
            .where(
                SignatureRow.check_status.in_(list(REVIEW_STATUSES)),
                ContractRow.status.in_(list(LIVE_STATUSES)),
            )
            .order_by(SignatureRow.uploaded_at)
            .limit(limit)
        )
        return [_signature(r) for r in rows]

    async def lease_ids_signed_by(
        self, tax_id_hash: str, role: PartyRole, exclude_lease: uuid.UUID
    ) -> list[uuid.UUID]:
        rows = await self._s.scalars(
            select(ContractRow.lease_id)
            .join(SignatureRow, SignatureRow.contract_id == ContractRow.id)
            .where(
                SignatureRow.signer_tax_id_hash == tax_id_hash,
                SignatureRow.party_role == role,
                SignatureRow.check_status != SignatureCheck.INVALID,
                ContractRow.status != ContractStatus.VOID,
                ContractRow.lease_id != exclude_lease,
            )
            .distinct()
        )
        return list(rows)
