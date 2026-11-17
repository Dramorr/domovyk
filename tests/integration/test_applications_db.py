"""Модуль applications на справжньому Postgres: обмеження, чат заявки, оренда із заявки."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.domain.applications import ApplicationStatus, DeclineReason
from domovyk.domain.leases import PayMethod
from domovyk.modules.applications.schemas import ApplicationIn
from domovyk.modules.leases.schemas import LeaseConditions, PayDetailsIn
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from tests.integration.test_leases_db import OWNER, ROOMMATE, TENANT, _setup
from tests.unit.test_property_profile import FULL

pytestmark = pytest.mark.integration


async def test_application_to_lease_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    pid = data.property_id
    await s.properties.update_profile(OWNER, pid, FULL)
    await s.listings.save(OWNER, pid, ListingIn(price_amount=15000))
    state = await s.listings.publish(OWNER, pid, PublishIn(owner_declaration=True))
    assert state.listing is not None
    listing_id = state.listing.id

    chosen = await s.applications.apply(TENANT, listing_id, ApplicationIn(message="Привіт!"))
    other = await s.applications.apply(ROOMMATE, listing_id, ApplicationIn())

    # Друга відкрита заявка тієї самої людини на те саме оголошення — база не пустить.
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO applications (listing_id, tenant_user_id, expires_at) "
                    "SELECT listing_id, tenant_user_id, now() FROM applications WHERE id = :i"
                ),
                {"i": chosen.id},
            )
        await conn.rollback()

    approved = await s.applications.approve(OWNER, chosen.id)
    assert approved.contacts is not None and approved.contacts.house_number == "40"
    await s.applications.post_message(TENANT, chosen.id, "Коли можна подивитись?")
    page = await s.applications.messages(OWNER, chosen.id)
    assert page.messages[-1].body == "Коли можна подивитись?"

    lease = await s.applications.create_lease(
        OWNER,
        chosen.id,
        LeaseConditions(
            start_date=date(2026, 12, 1),
            months=11,
            rent_amount=15000,
            rent_due_day=1,
            pay_method=PayMethod.CARD,
            pay_details=PayDetailsIn(card="4111111111111111", recipient_name="Тарас Шевченко"),
        ),
    )
    async with engine.connect() as conn:
        origin, app_ref = (
            await conn.execute(
                text("SELECT origin, application_id FROM leases WHERE id = :i"), {"i": lease.id}
            )
        ).one()
        history = (
            (
                await conn.execute(
                    text(
                        "SELECT to_status FROM application_status_history "
                        "WHERE application_id = :i ORDER BY id"
                    ),
                    {"i": chosen.id},
                )
            )
            .scalars()
            .all()
        )
    assert origin == "application" and app_ref == chosen.id
    assert history == ["pending", "approved", "lease_created"]
    card = await s.applications.card(OWNER, chosen.id)
    assert card.status is ApplicationStatus.LEASE_CREATED and card.lease_id == lease.id
    rest = await s.applications.card(ROOMMATE, other.id)
    assert rest.status is ApplicationStatus.DECLINED
    assert rest.decline_reason is DeclineReason.RENTED
    assert len(await s.applications.incoming(OWNER, pid)) == 2
