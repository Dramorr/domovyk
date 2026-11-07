"""DTO модуля contracts — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from domovyk.domain.contracts import ContractSource, ContractStatus, SignatureCheck

SignatureState = Literal["accepted", "review", "rejected"]


class LegalNameIn(BaseModel):
    full_name: str = Field(max_length=200)


class ReviewIn(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=500)


class ExternalDecisionIn(BaseModel):
    accept: bool


class SignatureOut(BaseModel):
    id: uuid.UUID
    state: SignatureState
    check_status: SignatureCheck
    signer_name: str | None
    uploaded_at: datetime
    review_reason: str | None


class PartyOut(BaseModel):
    role: Literal["landlord", "tenant"]
    user_id: uuid.UUID
    display_name: str
    legal_name: str | None
    is_me: bool
    signature: SignatureOut | None


class ContractOut(BaseModel):
    id: uuid.UUID
    source: ContractSource
    status: ContractStatus
    version: int
    template_code: str | None
    pdf_sha256: str
    filename: str
    created_at: datetime
    signed_at: datetime | None


class ContractState(BaseModel):
    """Блок «Договір» у картці оренди (CTR-CHOICE, CTR-SIGN, CTR-EXT)."""

    lease_id: uuid.UUID
    lease_status: str
    my_role: Literal["landlord", "responsible", "co_tenant"]
    contract: ContractOut | None
    parties: list[PartyOut]
    # Умови змінилися після формування — договір треба сформувати заново.
    stale: bool
    needs_names: bool
    can_generate: bool
    can_upload_external: bool
    can_sign: bool
    awaiting_my_confirmation: bool
    require_moderator: bool
    landlord_name_hint: str | None
    diia_sign_url: str


class SignatureCase(BaseModel):
    """Підпис на перевірці в модератора (ADR 0010)."""

    signature_id: uuid.UUID
    contract_id: uuid.UUID
    lease_id: uuid.UUID
    street: str
    house_number: str
    apartment: str | None
    party_role: Literal["landlord", "tenant"]
    display_name: str
    expected_name: str | None
    owner_name_in_doc: str | None
    signer_name: str | None
    check_status: SignatureCheck
    document_match: str
    digest_algorithm: str | None
    container: str | None
    issuer: str | None
    signed_at_claimed: datetime | None
    uploaded_at: datetime
    notes: list[str]
