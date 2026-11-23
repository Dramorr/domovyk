"""Публічний інтерфейс модуля overview: етап квартири й картка «Зараз» (ADR 0023).

Нічого не зберігає: збирає факти через сервіси інших модулів, а рішення
приймає чиста логіка `domain/overview.py`. Ті самі коди потім можуть
використати бот і сайт.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from domovyk.domain.applications import ApplicationStatus
from domovyk.domain.leases import LeaseStatus
from domovyk.domain.overview import (
    LeaseFacts,
    Now,
    PropertyFacts,
    Tone,
    lease_now,
    property_now,
    property_stage,
)
from domovyk.domain.payments import ChargeStatus
from domovyk.modules.applications.service import ApplicationsService
from domovyk.modules.contracts.service import ContractsService
from domovyk.modules.leases.service import LeaseOut, LeasesService
from domovyk.modules.listings.service import ListingsService
from domovyk.modules.overview.schemas import AttentionOut, LeaseOverview, NowOut, PropertyOverview
from domovyk.modules.payments.service import PaymentsService
from domovyk.modules.properties.service import PropertiesService

__all__ = ["AttentionOut", "LeaseOverview", "NowOut", "OverviewService", "PropertyOverview"]

_OPEN = frozenset(
    {
        LeaseStatus.DRAFT,
        LeaseStatus.AWAITING_TENANT,
        LeaseStatus.AWAITING_SIGNATURES,
        LeaseStatus.ACTIVE,
        LeaseStatus.ENDING,
    }
)
_PAYABLE = frozenset({ChargeStatus.DUE, ChargeStatus.OVERDUE})


class OverviewService:
    def __init__(
        self,
        properties: PropertiesService,
        listings: ListingsService,
        applications: ApplicationsService,
        leases: LeasesService,
        contracts: ContractsService,
        payments: PaymentsService,
    ) -> None:
        self._properties = properties
        self._listings = listings
        self._applications = applications
        self._leases = leases
        self._contracts = contracts
        self._payments = payments

    async def property(self, telegram_uid: int, property_id: uuid.UUID) -> PropertyOverview:
        """Сторінка квартири власника. Чужа квартира — NotFound від properties."""
        prop = await self._properties.get_mine(telegram_uid, property_id)
        mine = [
            x
            for x in await self._leases.list_mine(telegram_uid)
            if x.property_id == property_id and x.my_role == "landlord"
        ]
        current = next((x for x in mine if x.status in _OPEN), None)
        listing = (await self._listings.state(telegram_uid, property_id)).listing
        listing_status = listing.status if listing else None

        new_apps = 0
        if current is None and listing is not None:
            incoming = await self._applications.incoming(telegram_uid, property_id)
            new_apps = sum(1 for a in incoming if a.status is ApplicationStatus.PENDING)

        if current is not None:
            lease = await self._leases.get(telegram_uid, current.id)
            now = await self._lease_now(telegram_uid, lease)
        else:
            completeness = prop.completeness
            now = property_now(
                PropertyFacts(
                    ownership=prop.ownership_status,
                    publishable=bool(completeness and completeness.publishable),
                    listing=listing_status,
                    new_applications=new_apps,
                )
            )
        return PropertyOverview(
            property_id=property_id,
            stage=property_stage(current.status if current else None, listing_status),
            now=_out(now),
            lease_id=current.id if current else None,
            lease_status=current.status if current else None,
            listing_status=listing_status,
            new_applications=new_apps,
            chat_unread=current.unread if current else 0,
            has_history=any(x.status not in _OPEN for x in mine),
        )

    async def lease(self, telegram_uid: int, lease_id: uuid.UUID) -> LeaseOverview:
        """Сторінка оренди (для орендаря й співмешканця; власник бачить квартиру)."""
        lease = await self._leases.get(telegram_uid, lease_id)
        return LeaseOverview(
            lease_id=lease_id,
            stage=property_stage(lease.status, None),
            now=_out(await self._lease_now(telegram_uid, lease)),
        )

    async def attention(self, telegram_uid: int) -> AttentionOut:
        """Що чекає дії в кожному режимі (крапка біля перемикача, ADR 0023).

        Та сама логіка, що й картка «Зараз»: квартира чи оренда рахується,
        коли її «Зараз» має тон `action`. У типового власника 1–2 квартири,
        тож окремого запиту в БД під це не робимо.
        """
        landlord = 0
        for prop in await self._properties.list_mine(telegram_uid):
            ov = await self.property(telegram_uid, prop.id)
            landlord += ov.now.tone is Tone.ACTION
        tenant = 0
        for item in await self._leases.list_mine(telegram_uid):
            if item.my_role == "landlord" or item.status not in _OPEN:
                continue
            lease = await self._leases.get(telegram_uid, item.id)
            tenant += (await self._lease_now(telegram_uid, lease)).tone is Tone.ACTION
        return AttentionOut(landlord=landlord, tenant=tenant)

    async def _lease_now(self, telegram_uid: int, lease: LeaseOut) -> Now:
        facts = LeaseFacts(
            status=lease.status,
            role=lease.my_role,
            has_responsible=any(m.role == "responsible" for m in lease.members),
            landlord_agreed=lease.agreement.landlord == "agreed",
            tenant_agreed=lease.agreement.tenant == "agreed",
            has_proposal=lease.proposal is not None,
            ends_on=(lease.termination.date if lease.termination else lease.end_date),
        )
        if lease.status is LeaseStatus.AWAITING_SIGNATURES and lease.my_role != "co_tenant":
            c = await self._contracts.state(telegram_uid, lease.id)
            facts = replace(
                facts,
                contract_needs_names=c.needs_names,
                contract_can_generate=c.can_generate and (c.contract is None or c.stale),
                contract_can_sign=c.can_sign,
                contract_awaiting_my_confirmation=c.awaiting_my_confirmation,
            )
        if lease.status in (LeaseStatus.ACTIVE, LeaseStatus.ENDING):
            p = await self._payments.overview(telegram_uid, lease.id)
            nxt = p.next_charge
            facts = replace(
                facts,
                payments_to_decide=p.awaiting_my_decision,
                next_charge_due=nxt.due_date if nxt else None,
                next_charge_overdue=bool(nxt and nxt.status is ChargeStatus.OVERDUE),
                next_charge_payable=bool(nxt and nxt.status in _PAYABLE),
            )
        return lease_now(facts)


def _out(now: Now) -> NowOut:
    return NowOut(code=now.code, tone=now.tone, target=now.target, count=now.count, on=now.on)
