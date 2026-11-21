"""Етап квартири й картка «Зараз» (ADR 0023)."""

from __future__ import annotations

from datetime import date

import pytest

from domovyk.domain.leases import LeaseStatus as L
from domovyk.domain.listings import ListingStatus as S
from domovyk.domain.overview import (
    LeaseFacts,
    NowCode,
    PropertyFacts,
    Stage,
    Target,
    Tone,
    lease_now,
    property_now,
    property_stage,
)
from domovyk.domain.properties import OwnershipStatus as O


@pytest.mark.parametrize(
    ("lease", "listing", "stage"),
    [
        (None, None, Stage.PREPARING),
        (None, S.DRAFT, Stage.PREPARING),
        (None, S.ARCHIVED, Stage.PREPARING),
        (None, S.ACTIVE, Stage.RENTING),
        (None, S.PAUSED, Stage.RENTING),
        (L.AWAITING_TENANT, S.RENTED, Stage.SIGNING),
        (L.AWAITING_SIGNATURES, None, Stage.SIGNING),
        (L.ACTIVE, S.RENTED, Stage.RENTED),
        (L.ENDING, S.RENTED, Stage.ENDING),
    ],
)
def test_property_stage(lease: L | None, listing: S | None, stage: Stage) -> None:
    assert property_stage(lease, listing) is stage


def _p(
    ownership: O = O.VERIFIED, publishable: bool = True, listing: S | None = None, apps: int = 0
) -> PropertyFacts:
    return PropertyFacts(ownership, publishable, listing, apps)


@pytest.mark.parametrize(
    ("facts", "code", "target"),
    [
        (_p(O.UNVERIFIED, publishable=False), NowCode.VERIFY_OWNERSHIP, Target.OWNERSHIP),
        (_p(O.REJECTED), NowCode.OWNERSHIP_REJECTED, Target.OWNERSHIP),
        # Поки модератор перевіряє — готуємо анкету; готова — просто чекаємо.
        (_p(O.PENDING, publishable=False), NowCode.FILL_PROFILE, Target.PROFILE),
        (_p(O.PENDING), NowCode.OWNERSHIP_PENDING, Target.NONE),
        (_p(publishable=False), NowCode.FILL_PROFILE, Target.PROFILE),
        (_p(), NowCode.PUBLISH_LISTING, Target.LISTING),
        (_p(listing=S.DRAFT), NowCode.PUBLISH_LISTING, Target.LISTING),
        (_p(listing=S.ACTIVE), NowCode.WAIT_APPLICATIONS, Target.LISTING),
        (_p(listing=S.PAUSED), NowCode.LISTING_PAUSED, Target.LISTING),
        (_p(listing=S.ACTIVE, apps=2), NowCode.NEW_APPLICATIONS, Target.APPLICATIONS),
    ],
)
def test_property_now(facts: PropertyFacts, code: NowCode, target: Target) -> None:
    now = property_now(facts)
    assert (now.code, now.target) == (code, target)


def test_new_applications_carry_count() -> None:
    assert property_now(_p(listing=S.ACTIVE, apps=3)).count == 3


def _l(status: L = L.AWAITING_TENANT, role: str = "landlord", **kw: object) -> LeaseFacts:
    base: dict[str, object] = {
        "has_responsible": True,
        "landlord_agreed": False,
        "tenant_agreed": False,
        "has_proposal": False,
    }
    base.update(kw)
    return LeaseFacts(status=status, role=role, **base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("facts", "code", "tone"),
    [
        (_l(has_responsible=False), NowCode.INVITE_TENANT, Tone.ACTION),
        (_l(has_responsible=False, role="responsible"), NowCode.WAIT_TENANT_TERMS, Tone.WAIT),
        (_l(has_proposal=True), NowCode.REVIEW_PROPOSAL, Tone.ACTION),
        (_l(has_proposal=True, role="responsible"), NowCode.WAIT_LANDLORD_TERMS, Tone.WAIT),
        (_l(), NowCode.AGREE_TERMS, Tone.ACTION),
        (_l(landlord_agreed=True), NowCode.WAIT_TENANT_TERMS, Tone.WAIT),
        (_l(role="responsible"), NowCode.TENANT_AGREE, Tone.ACTION),
        (_l(role="responsible", tenant_agreed=True), NowCode.WAIT_LANDLORD_TERMS, Tone.WAIT),
        (_l(role="co_tenant"), NowCode.WAIT_LANDLORD_TERMS, Tone.WAIT),
        (_l(L.AWAITING_SIGNATURES, contract_needs_names=True), NowCode.CONTRACT_NAMES, Tone.ACTION),
        (
            _l(L.AWAITING_SIGNATURES, contract_can_generate=True),
            NowCode.CONTRACT_GENERATE,
            Tone.ACTION,
        ),
        (_l(L.AWAITING_SIGNATURES, contract_can_sign=True), NowCode.SIGN_CONTRACT, Tone.ACTION),
        (
            _l(L.AWAITING_SIGNATURES, contract_awaiting_my_confirmation=True),
            NowCode.CONFIRM_EXTERNAL,
            Tone.ACTION,
        ),
        (_l(L.AWAITING_SIGNATURES), NowCode.WAIT_SIGNATURES, Tone.WAIT),
        (_l(L.AWAITING_SIGNATURES, role="co_tenant"), NowCode.WAIT_CONTRACT, Tone.WAIT),
        (_l(L.ACTIVE, payments_to_decide=1), NowCode.CONFIRM_PAYMENT, Tone.ACTION),
        (_l(L.ACTIVE, next_charge_overdue=True), NowCode.RENT_OVERDUE, Tone.WAIT),
        (_l(L.ACTIVE), NowCode.ALL_GOOD, Tone.OK),
        (_l(L.ACTIVE, role="responsible", next_charge_payable=True), NowCode.PAY_RENT, Tone.ACTION),
        (_l(L.ACTIVE, role="responsible"), NowCode.ALL_GOOD, Tone.OK),
        (_l(L.ACTIVE, role="co_tenant", next_charge_payable=True), NowCode.ALL_GOOD, Tone.OK),
        (_l(L.ENDING, payments_to_decide=2), NowCode.CONFIRM_PAYMENT, Tone.ACTION),
        (_l(L.ENDING, ends_on=date(2026, 12, 31)), NowCode.MOVE_OUT, Tone.WAIT),
    ],
)
def test_lease_now(facts: LeaseFacts, code: NowCode, tone: Tone) -> None:
    now = lease_now(facts)
    assert (now.code, now.tone) == (code, tone)


def test_move_out_carries_date() -> None:
    assert lease_now(_l(L.ENDING, ends_on=date(2026, 12, 31))).on == date(2026, 12, 31)
