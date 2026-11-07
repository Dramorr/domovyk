"""DTO модуля leases — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from domovyk.domain.leases import LeaseOrigin, LeaseStatus, MemberRole, PayMethod
from domovyk.domain.properties import UnitKind
from domovyk.modules.properties.service import PropertyOut

PartyRole = Literal["landlord", "responsible", "co_tenant"]


class LeaseTerms(BaseModel):
    """Пункти договору (FR-LSE-2) з типовими значеннями (BOT_FLOWS LSE-NEW, крок 9)."""

    visits_notice_hours: int = Field(default=24, ge=0, le=168)
    minor_repairs: Literal["tenant", "landlord"] = "tenant"
    major_repairs: Literal["landlord", "tenant"] = "landlord"
    pets_allowed: bool = False
    utilities_payer: Literal["tenant_direct", "landlord", "included"] = "tenant_direct"
    residence_registration: bool = False
    early_termination_notice_days: int = Field(default=30, ge=0, le=90)
    dispute_window_days: Literal[1, 3, 5, 7] = 3
    # Піднайм (суборенда) — лише з письмової згоди власника (ст. 823 ЦКУ); типово заборонено.
    sublet_allowed: bool = False


AgreementState = Literal["none", "agreed", "proposed", "declined"]


class Agreement(BaseModel):
    """Хто погодив поточні умови (FR-LSE-10) — для смужки прогресу в картці оренди."""

    tenant: AgreementState
    landlord: AgreementState


class ProposalIn(BaseModel):
    """Орендар пропонує зміни умов. Реквізити оплати задає лише власник."""

    start_date: date
    months: int = Field(ge=1, le=35)
    rent_amount: int
    rent_due_day: int
    deposit_amount: int | None = None
    terms: LeaseTerms = Field(default_factory=LeaseTerms)


class ProposalOut(BaseModel):
    by: PartyRole
    start_date: date
    end_date: date
    months: int
    rent_amount: int
    rent_due_day: int
    deposit_amount: int | None
    terms: LeaseTerms
    proposed_at: datetime


class RespondIn(BaseModel):
    accept: bool


class PayDetailsIn(BaseModel):
    """Реквізити — шифруються (FR-PAY-8). Для готівки не потрібні."""

    card: str | None = Field(default=None, max_length=30)
    iban: str | None = Field(default=None, max_length=40)
    jar_url: str | None = Field(default=None, max_length=200)
    recipient_name: str | None = Field(default=None, max_length=120)


class LeaseConditions(BaseModel):
    """Умови, які власник задає й може змінювати до активації."""

    start_date: date
    months: int = Field(ge=1, le=35)
    rent_amount: int
    rent_due_day: int
    deposit_amount: int | None = None
    pay_method: PayMethod
    pay_details: PayDetailsIn | None = None
    terms: LeaseTerms = Field(default_factory=LeaseTerms)


class LeaseCreate(LeaseConditions):
    property_id: uuid.UUID
    unit_id: uuid.UUID | None = None  # None — «весь об'єкт»


class PayDisplay(BaseModel):
    """Як показуємо реквізити до активації: замасковано (FR-PAY-8)."""

    method: PayMethod
    masked: str | None
    recipient: str | None


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    role: PartyRole
    joined_at: datetime | None


class InviteOut(BaseModel):
    """Токен показуємо лише раз — у базі лише його хеш (FR-LSE-1)."""

    token: str
    role: MemberRole
    expires_at: datetime


class PendingInvite(BaseModel):
    role: MemberRole
    expires_at: datetime


class LeaseSummary(BaseModel):
    id: uuid.UUID
    status: LeaseStatus
    my_role: PartyRole
    street: str
    house_number: str
    apartment: str | None
    unit_kind: UnitKind
    unit_name: str
    start_date: date
    end_date: date
    rent_amount: int
    unread: int


class LeaseOut(LeaseSummary):
    property_id: uuid.UUID
    months_total: int
    months_left: int
    currency: str
    rent_due_day: int
    deposit_amount: int | None
    pay: PayDisplay
    terms: LeaseTerms
    terms_accepted_at: datetime | None
    agreement: Agreement
    proposal: ProposalOut | None
    members: list[MemberOut]
    pending_invites: list[PendingInvite]
    can_edit: bool
    can_invite_co_tenant: bool
    created_at: datetime


class LeaseForContract(BaseModel):
    """Що потрібно модулю contracts: оренда, сторони, об'єкт і відбиток погоджених умов."""

    lease: LeaseOut
    property: PropertyOut
    origin: LeaseOrigin
    landlord_user_id: uuid.UUID
    responsible_user_id: uuid.UUID | None
    viewer_user_id: uuid.UUID | None
    conditions_hash: str


class InvitePreview(BaseModel):
    """Що бачить людина за посиланням, до того як приєднатися (INV-ACCEPT)."""

    valid: bool
    reason: Literal[
        "ok",
        "expired",
        "used",
        "revoked",
        "not_found",
        "own_lease",
        "already_member",
        "lease_limit",
    ]
    role: MemberRole | None = None
    landlord_name: str | None = None
    street: str | None = None
    house_number: str | None = None
    apartment: str | None = None
    rent_amount: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    lease_id: uuid.UUID | None = None
