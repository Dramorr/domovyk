"""Модуль utilities на справжньому Postgres: довідник постачальників, лічильники, обмеження."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict, InvalidInput
from domovyk.domain.utilities import MeterKind
from domovyk.modules.payments.schemas import UtilityChargeIn
from domovyk.modules.utilities.schemas import MeterIn
from tests.integration.test_leases_db import OWNER, TENANT, _setup

pytestmark = pytest.mark.integration


async def test_meters_and_readings_db(
    services: Services, engine: AsyncEngine, it_clock: FixedClock
) -> None:
    s = services
    data = await _setup(s, engine)
    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE leases SET status='active' WHERE id=:i"), {"i": lease.id})

    view = await s.utilities.for_property(OWNER, data.property_id)
    codes = {p.name for p in view.providers}
    assert len(view.providers) == 4 and any("Газмережі" in n for n in codes)
    gas = next(p for p in view.providers if MeterKind.GAS in p.meter_kinds)
    water = next(p for p in view.providers if MeterKind.WATER_COLD in p.meter_kinds)

    with pytest.raises(InvalidInput):  # газовий постачальник не приймає воду
        await s.utilities.add_meter(
            OWNER, data.property_id, MeterIn(kind=MeterKind.WATER_COLD, provider_id=gas.id)
        )
    view = await s.utilities.add_meter(
        OWNER,
        data.property_id,
        MeterIn(
            kind=MeterKind.WATER_COLD,
            provider_id=water.id,
            label="Ванна",
            tariff=Decimal("40.524"),
            initial_value=Decimal("10.5"),
        ),
    )
    meter = view.meters[0]
    assert meter.tariff == Decimal("40.5240") and meter.last is not None

    it_clock.advance(timedelta(days=30))
    result = await s.utilities.submit_reading(TENANT, meter.id, "15.5")
    assert result.reading.consumed == Decimal("5.000")
    assert result.reading.estimate == Decimal("202.62")
    with pytest.raises(InvalidInput):
        await s.utilities.submit_reading(TENANT, meter.id, "15.4")

    tenant = await s.utilities.for_lease(TENANT, lease.id)
    assert [r.value for r in tenant.meters[0].readings] == [Decimal("15.500"), Decimal("10.500")]

    # Видалити лічильник з показниками не можна — лише вимкнути; історія лишається.
    view = await s.utilities.remove_meter(OWNER, meter.id)
    assert not view.meters[0].is_active and len(view.meters[0].readings) == 2

    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("UPDATE meter_readings SET value = -1 WHERE meter_id = :m"), {"m": meter.id}
            )
        await conn.rollback()
        with pytest.raises(DBAPIError):  # невідомий тип лічильника
            await conn.execute(
                text("UPDATE meters SET kind = 'steam' WHERE id = :m"), {"m": meter.id}
            )


async def test_utility_charge_db(
    services: Services, engine: AsyncEngine, it_clock: FixedClock
) -> None:
    s = services
    data = await _setup(s, engine)
    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE leases SET status='active' WHERE id=:i"), {"i": lease.id})

    today = it_clock.now().date()
    body = UtilityChargeIn(
        period_month=date(2026, 11, 1),
        amount=Decimal("980.50"),
        due_date=today + timedelta(days=5),
        note="світло й вода",
    )
    view = await s.payments.add_utility_charge(OWNER, lease.id, body)
    charge = next(c for c in view.charges if c.kind == "utilities")
    assert charge.note == "світло й вода" and charge.can_cancel
    with pytest.raises(Conflict):
        await s.payments.add_utility_charge(OWNER, lease.id, body)
    view = await s.payments.cancel_charge(OWNER, charge.id)
    assert all(c.kind != "utilities" for c in view.charges)
