"""Етап квартири й картка «Зараз» (ADR 0023). Чиста логіка без I/O.

Сторінка квартири власника й сторінка оренди орендаря показують угорі одну
наступну дію: що чекає саме від цієї людини. Факти збирає `modules/overview`,
а тут — лише рішення, тож його легко перевірити тестами.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from domovyk.domain.leases import LeaseStatus
from domovyk.domain.listings import PUBLISHED, ListingStatus
from domovyk.domain.properties import OwnershipStatus


class Stage(StrEnum):
    """Етап життя квартири: від нього залежать вкладки сторінки."""

    PREPARING = "preparing"  # анкета, фото, власність — ще не здається
    RENTING = "renting"  # оголошення опубліковане (чи на паузі), шукаємо орендаря
    SIGNING = "signing"  # є оренда до активації: умови, договір, підписи
    RENTED = "rented"  # оренда активна
    ENDING = "ending"  # оренда завершується (розірвання або кінець строку)


SIGNING_STATUSES = frozenset(
    {LeaseStatus.DRAFT, LeaseStatus.AWAITING_TENANT, LeaseStatus.AWAITING_SIGNATURES}
)


def property_stage(lease: LeaseStatus | None, listing: ListingStatus | None) -> Stage:
    """Етап за відкритою орендою (якщо є) і станом оголошення."""
    if lease is LeaseStatus.ACTIVE:
        return Stage.RENTED
    if lease is LeaseStatus.ENDING:
        return Stage.ENDING
    if lease in SIGNING_STATUSES:
        return Stage.SIGNING
    if listing in PUBLISHED:
        return Stage.RENTING
    return Stage.PREPARING


class NowCode(StrEnum):
    """Що показати в картці «Зараз». Текст — у локалізації інтерфейсу."""

    # Підготовка
    VERIFY_OWNERSHIP = "verify_ownership"
    OWNERSHIP_PENDING = "ownership_pending"
    OWNERSHIP_REJECTED = "ownership_rejected"
    FILL_PROFILE = "fill_profile"
    PUBLISH_LISTING = "publish_listing"
    # Здаю
    NEW_APPLICATIONS = "new_applications"
    LISTING_PAUSED = "listing_paused"
    WAIT_APPLICATIONS = "wait_applications"
    # Оформлюю
    INVITE_TENANT = "invite_tenant"
    REVIEW_PROPOSAL = "review_proposal"
    AGREE_TERMS = "agree_terms"
    WAIT_TENANT_TERMS = "wait_tenant_terms"
    TENANT_AGREE = "tenant_agree"
    WAIT_LANDLORD_TERMS = "wait_landlord_terms"
    CONTRACT_NAMES = "contract_names"
    CONTRACT_GENERATE = "contract_generate"
    SIGN_CONTRACT = "sign_contract"
    CONFIRM_EXTERNAL = "confirm_external"
    WAIT_SIGNATURES = "wait_signatures"
    WAIT_CONTRACT = "wait_contract"  # співмешканець: договір підписують інші
    # Здано
    CONFIRM_PAYMENT = "confirm_payment"
    RENT_OVERDUE = "rent_overdue"
    PAY_RENT = "pay_rent"
    ALL_GOOD = "all_good"
    # Завершення
    MOVE_OUT = "move_out"


class Tone(StrEnum):
    ACTION = "action"  # чекає саме від тебе — з кнопкою
    WAIT = "wait"  # чекаємо іншу сторону чи модератора
    OK = "ok"  # усе гаразд, тихо


class Target(StrEnum):
    """Куди веде кнопка картки «Зараз» (вкладка чи екран сторінки)."""

    NONE = "none"
    OWNERSHIP = "ownership"
    PROFILE = "profile"
    LISTING = "listing"
    APPLICATIONS = "applications"
    OVERVIEW = "overview"  # дія вже на «Огляді» нижче (шлях до заселення, оплати)
    CONTRACT = "contract"
    INVITE = "invite"


@dataclass(frozen=True)
class Now:
    code: NowCode
    tone: Tone
    target: Target = Target.NONE
    count: int | None = None
    on: date | None = None


@dataclass(frozen=True)
class PropertyFacts:
    """Факти про квартиру без відкритої оренди (етапи «Підготовка» і «Здаю»)."""

    ownership: OwnershipStatus
    publishable: bool  # анкета заповнена достатньо для оголошення
    listing: ListingStatus | None
    new_applications: int


def property_now(f: PropertyFacts) -> Now:
    """«Зараз» для квартири без відкритої оренди."""
    if f.ownership is OwnershipStatus.UNVERIFIED:
        return Now(NowCode.VERIFY_OWNERSHIP, Tone.ACTION, Target.OWNERSHIP)
    if f.ownership is OwnershipStatus.REJECTED:
        return Now(NowCode.OWNERSHIP_REJECTED, Tone.ACTION, Target.OWNERSHIP)
    if f.new_applications > 0:
        return Now(NowCode.NEW_APPLICATIONS, Tone.ACTION, Target.APPLICATIONS, f.new_applications)
    if f.listing is ListingStatus.ACTIVE:
        return Now(NowCode.WAIT_APPLICATIONS, Tone.OK, Target.LISTING)
    if f.listing is ListingStatus.PAUSED:
        return Now(NowCode.LISTING_PAUSED, Tone.ACTION, Target.LISTING)
    if f.ownership is OwnershipStatus.PENDING:
        # Поки модератор перевіряє, можна готувати анкету.
        if not f.publishable:
            return Now(NowCode.FILL_PROFILE, Tone.ACTION, Target.PROFILE)
        return Now(NowCode.OWNERSHIP_PENDING, Tone.WAIT)
    if not f.publishable:
        return Now(NowCode.FILL_PROFILE, Tone.ACTION, Target.PROFILE)
    return Now(NowCode.PUBLISH_LISTING, Tone.ACTION, Target.LISTING)


@dataclass(frozen=True)
class LeaseFacts:
    """Факти про відкриту оренду з погляду однієї людини."""

    status: LeaseStatus
    role: str  # landlord / responsible / co_tenant
    has_responsible: bool
    landlord_agreed: bool
    tenant_agreed: bool
    has_proposal: bool
    # Договір (лише для awaiting_signatures)
    contract_needs_names: bool = False
    contract_can_generate: bool = False
    contract_can_sign: bool = False
    contract_awaiting_my_confirmation: bool = False
    # Оплати (лише для активної оренди)
    payments_to_decide: int = 0
    next_charge_due: date | None = None
    next_charge_overdue: bool = False
    next_charge_payable: bool = False  # due / overdue — пора платити
    ends_on: date | None = None


def lease_now(f: LeaseFacts) -> Now:
    """«Зараз» для відкритої оренди: що чекає саме від цієї людини."""
    landlord = f.role == "landlord"
    tenant = f.role == "responsible"
    if f.status in SIGNING_STATUSES and f.status is not LeaseStatus.AWAITING_SIGNATURES:
        if not f.has_responsible:
            if landlord:
                return Now(NowCode.INVITE_TENANT, Tone.ACTION, Target.INVITE)
            return Now(NowCode.WAIT_TENANT_TERMS, Tone.WAIT)
        if f.has_proposal:
            if landlord:
                return Now(NowCode.REVIEW_PROPOSAL, Tone.ACTION, Target.OVERVIEW)
            return Now(NowCode.WAIT_LANDLORD_TERMS, Tone.WAIT)
        if landlord:
            if not f.landlord_agreed:
                return Now(NowCode.AGREE_TERMS, Tone.ACTION, Target.OVERVIEW)
            return Now(NowCode.WAIT_TENANT_TERMS, Tone.WAIT)
        if tenant and not f.tenant_agreed:
            return Now(NowCode.TENANT_AGREE, Tone.ACTION, Target.OVERVIEW)
        return Now(NowCode.WAIT_LANDLORD_TERMS, Tone.WAIT)
    if f.status is LeaseStatus.AWAITING_SIGNATURES:
        if not (landlord or tenant):
            return Now(NowCode.WAIT_CONTRACT, Tone.WAIT)
        if f.contract_awaiting_my_confirmation:
            return Now(NowCode.CONFIRM_EXTERNAL, Tone.ACTION, Target.OVERVIEW)
        if f.contract_needs_names:
            return Now(NowCode.CONTRACT_NAMES, Tone.ACTION, Target.OVERVIEW)
        if f.contract_can_generate:
            return Now(NowCode.CONTRACT_GENERATE, Tone.ACTION, Target.OVERVIEW)
        if f.contract_can_sign:
            return Now(NowCode.SIGN_CONTRACT, Tone.ACTION, Target.OVERVIEW)
        return Now(NowCode.WAIT_SIGNATURES, Tone.WAIT)
    if f.status is LeaseStatus.ENDING:
        if landlord and f.payments_to_decide:
            return Now(NowCode.CONFIRM_PAYMENT, Tone.ACTION, Target.OVERVIEW, f.payments_to_decide)
        return Now(NowCode.MOVE_OUT, Tone.WAIT, on=f.ends_on)
    # Активна оренда
    if landlord:
        if f.payments_to_decide:
            return Now(NowCode.CONFIRM_PAYMENT, Tone.ACTION, Target.OVERVIEW, f.payments_to_decide)
        if f.next_charge_overdue:
            return Now(NowCode.RENT_OVERDUE, Tone.WAIT, Target.OVERVIEW, on=f.next_charge_due)
        return Now(NowCode.ALL_GOOD, Tone.OK, on=f.next_charge_due)
    if tenant and f.next_charge_payable:
        return Now(NowCode.PAY_RENT, Tone.ACTION, Target.OVERVIEW, on=f.next_charge_due)
    return Now(NowCode.ALL_GOOD, Tone.OK, on=f.next_charge_due)
