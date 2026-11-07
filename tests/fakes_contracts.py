"""Фейки модуля contracts і адаптера PDF для юніт-тестів. Лише вигадані дані."""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domovyk.adapters.pdf.base import PdfError
from domovyk.domain.contracts import (
    LIVE_STATUSES,
    ContractStatus,
    PartyRole,
    SignatureCheck,
)
from domovyk.modules.contracts.repository import (
    REVIEW_STATUSES,
    ContractRecord,
    NewContract,
    NewSignature,
    SignatureRecord,
)


@dataclass
class ContractDb:
    parties: dict[tuple[uuid.UUID, uuid.UUID], tuple[PartyRole, str]] = field(default_factory=dict)
    contracts: dict[uuid.UUID, ContractRecord] = field(default_factory=dict)
    signatures: dict[uuid.UUID, SignatureRecord] = field(default_factory=dict)
    clock_seq: int = 0


class FakePdfRenderer:
    """Замість WeasyPrint: детермінований «PDF» з HTML (для хешів і звірки підпису)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.last_html: str | None = None

    async def render(self, html: str) -> bytes:
        if self.fail:
            raise PdfError("немає Pango")
        self.last_html = html
        return b"%PDF-1.7\n" + hashlib.sha256(html.encode()).hexdigest().encode() + b"\n%%EOF"


class FakeContractsRepository:
    def __init__(self, db: ContractDb, now: Any) -> None:
        self.db = db
        self._now = now

    async def party_names(self, lease_id: uuid.UUID) -> dict[uuid.UUID, str]:
        return {u: name for (lid, u), (_, name) in self.db.parties.items() if lid == lease_id}

    async def set_party_name(
        self, lease_id: uuid.UUID, user_id: uuid.UUID, role: PartyRole, name: str, at: datetime
    ) -> None:
        self.db.parties[(lease_id, user_id)] = (role, name)

    async def create(self, data: NewContract) -> uuid.UUID:
        live = [
            c
            for c in self.db.contracts.values()
            if c.lease_id == data.lease_id and c.status is not ContractStatus.VOID
        ]
        if live:  # uq_contracts_live
            raise AssertionError("Другий живий договір на оренду")
        cid = uuid.uuid4()
        self.db.clock_seq += 1
        self.db.contracts[cid] = ContractRecord(
            id=cid,
            lease_id=data.lease_id,
            source=data.source,
            template_code=data.template_code,
            version=data.version,
            pdf_storage_key=data.pdf_storage_key,
            pdf_sha256=data.pdf_sha256,
            status=data.status,
            data_snapshot=data.data_snapshot,
            conditions_hash=data.conditions_hash,
            void_reason=None,
            created_by=data.created_by,
            created_at=self._now(),
            signed_at=None,
        )
        return cid

    async def get(self, contract_id: uuid.UUID) -> ContractRecord | None:
        return self.db.contracts.get(contract_id)

    async def current_for_lease(self, lease_id: uuid.UUID) -> ContractRecord | None:
        items = [
            c
            for c in self.db.contracts.values()
            if c.lease_id == lease_id and c.status is not ContractStatus.VOID
        ]
        return items[-1] if items else None

    async def latest_version(self, lease_id: uuid.UUID) -> int:
        return max(
            (c.version for c in self.db.contracts.values() if c.lease_id == lease_id), default=0
        )

    async def file_keys_for_lease(self, lease_id: uuid.UUID) -> list[str]:
        contracts = [c for c in self.db.contracts.values() if c.lease_id == lease_id]
        ids = {c.id for c in contracts}
        return [c.pdf_storage_key for c in contracts] + [
            s.signature_storage_key for s in self.db.signatures.values() if s.contract_id in ids
        ]

    async def update(self, contract_id: uuid.UUID, **values: Any) -> None:
        values.pop("external_decided_at", None)
        self.db.contracts[contract_id] = dataclasses.replace(
            self.db.contracts[contract_id], **values
        )

    async def live_older_than(self, before: datetime) -> list[ContractRecord]:
        return [
            c
            for c in self.db.contracts.values()
            if c.status in LIVE_STATUSES and c.created_at < before
        ]

    async def signatures(self, contract_id: uuid.UUID) -> list[SignatureRecord]:
        return [s for s in self.db.signatures.values() if s.contract_id == contract_id]

    async def signature(self, signature_id: uuid.UUID) -> SignatureRecord | None:
        return self.db.signatures.get(signature_id)

    async def upsert_signature(self, data: NewSignature) -> uuid.UUID:
        existing = next(
            (
                s
                for s in self.db.signatures.values()
                if s.contract_id == data.contract_id and s.user_id == data.user_id
            ),
            None,
        )
        sid = existing.id if existing else uuid.uuid4()
        self.db.signatures[sid] = SignatureRecord(
            id=sid,
            reviewed_by=None,
            reviewed_at=None,
            review_reason=None,
            **dataclasses.asdict(data),
        )
        return sid

    async def review_signature(
        self,
        signature_id: uuid.UUID,
        status: SignatureCheck,
        reviewer: uuid.UUID,
        at: datetime,
        reason: str | None,
    ) -> None:
        self.db.signatures[signature_id] = dataclasses.replace(
            self.db.signatures[signature_id],
            check_status=status,
            reviewed_by=reviewer,
            reviewed_at=at,
            review_reason=reason,
        )

    async def signatures_to_review(self, limit: int) -> list[SignatureRecord]:
        return [
            s
            for s in self.db.signatures.values()
            if s.check_status in REVIEW_STATUSES
            and self.db.contracts[s.contract_id].status in LIVE_STATUSES
        ][:limit]

    async def lease_ids_signed_by(
        self, tax_id_hash: str, role: PartyRole, exclude_lease: uuid.UUID
    ) -> list[uuid.UUID]:
        out: list[uuid.UUID] = []
        for s in self.db.signatures.values():
            c = self.db.contracts[s.contract_id]
            if (
                s.signer_tax_id_hash == tax_id_hash
                and s.party_role is role
                and s.check_status is not SignatureCheck.INVALID
                and c.status is not ContractStatus.VOID
                and c.lease_id != exclude_lease
                and c.lease_id not in out
            ):
                out.append(c.lease_id)
        return out
