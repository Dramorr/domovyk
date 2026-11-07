"""Модуль payments на справжньому Postgres: нарахування без дублів, оплати, обмеження."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict
from domovyk.domain.payments import ChargeStatus
from domovyk.modules.payments.schemas import CashIn, DecisionIn
from tests.integration.test_leases_db import OWNER, TENANT, _setup

pytestmark = pytest.mark.integration


async def test_payments_cycle_db(
    services: Services, engine: AsyncEngine, it_clock: FixedClock
) -> None:
    s = services
    data = await _setup(s, engine)  # старт 1.12.2026, день оплати 1, без депозиту
    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE leases SET status='active' WHERE id=:i"), {"i": lease.id})

    it_clock.advance(timedelta(days=21))  # 26.11 — за 5 днів до 1.12
    await s.payments.run_daily()
    await s.payments.run_daily()  # без дублів (UNIQUE lease, period, kind)
    view = await s.payments.overview(TENANT, lease.id)
    assert len(view.charges) == 1
    charge = view.charges[0]
    assert charge.amount == Decimal("15000.00")

    await s.payments.report_receipt(TENANT, charge.id, Decimal("15000"), "kv.pdf", b"%PDF-1.4 x")
    with pytest.raises(Conflict):
        await s.payments.report_receipt(TENANT, charge.id, Decimal("1"), "kv.pdf", b"%PDF-1.4 x")
    view = await s.payments.overview(OWNER, lease.id)
    payment = view.charges[0].payments[0]
    view = await s.payments.decide(OWNER, payment.id, DecisionIn(accept=True))
    assert view.charges[0].status is ChargeStatus.PAID
    with pytest.raises(Conflict):
        await s.payments.report_cash(TENANT, charge.id, CashIn(amount=Decimal("1")))

    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):  # готівка — так, переказ без квитанції — ні
            await conn.execute(
                text(
                    "INSERT INTO payments (charge_id, payer_user_id, method, amount, status) "
                    "SELECT charge_id, payer_user_id, 'card', 1, 'pending_landlord' "
                    "FROM payments LIMIT 1"
                )
            )
        await conn.rollback()
        with pytest.raises(IntegrityError):  # період — лише перше число місяця
            await conn.execute(
                text("UPDATE rent_charges SET period_month = '2026-12-05' WHERE id=:i"),
                {"i": charge.id},
            )
