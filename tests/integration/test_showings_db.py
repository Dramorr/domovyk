"""Модуль showings на справжньому Postgres: обмеження, місткість при одночасному записі."""

from __future__ import annotations

import asyncio
from datetime import date, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict
from domovyk.domain.applications import ApplicationStatus
from domovyk.domain.showings import ShowingAnswer
from domovyk.modules.applications.schemas import ApplicationIn, ShowingAnswerIn, ShowingIn
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from domovyk.modules.showings.schemas import SlotIn
from tests.integration.test_leases_db import OWNER, ROOMMATE, TENANT, _setup
from tests.unit.test_property_profile import FULL

pytestmark = pytest.mark.integration

DAY = date(2026, 11, 6)  # годинник тестів — 5.11.2026 12:00 UTC


async def test_showings_db(services: Services, engine: AsyncEngine, it_clock: FixedClock) -> None:
    s = services
    data = await _setup(s, engine)
    pid = data.property_id
    await s.properties.update_profile(OWNER, pid, FULL)
    await s.listings.save(OWNER, pid, ListingIn(price_amount=15000))
    state = await s.listings.publish(OWNER, pid, PublishIn(owner_declaration=True))
    assert state.listing is not None

    apps = []
    for tg in (TENANT, ROOMMATE):
        app = await s.applications.apply(tg, state.listing.id, ApplicationIn())
        await s.applications.approve(OWNER, app.id)
        apps.append(app.id)

    board = await s.applications.board(OWNER, pid)
    assert board.can_add and board.slots == []
    await s.showings.create(OWNER, pid, SlotIn(day=DAY, start=time(11, 0), minutes=30))
    slot = (await s.applications.board(OWNER, pid)).slots[0].id

    # Одне місце, двоє записуються одночасно: FOR UPDATE пропускає лише одного.
    results = await asyncio.gather(
        s.applications.book(TENANT, apps[0], ShowingIn(slot_id=slot)),
        s.applications.book(ROOMMATE, apps[1], ShowingIn(slot_id=slot)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, Conflict) for r in results) == 1
    booked = (await s.applications.board(OWNER, pid)).slots[0].booked
    assert len(booked) == 1
    winner = booked[0].application_id
    tenant_tg = TENANT if winner == apps[0] else ROOMMATE

    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):  # місткість 1..20
            await conn.execute(
                text("UPDATE showing_slots SET capacity = 0 WHERE id = :i"), {"i": slot}
            )
        await conn.rollback()
        with pytest.raises(IntegrityError):  # погоджена заявка без запису не тримає слот
            await conn.execute(
                text("UPDATE applications SET status = 'approved' WHERE id = :i"), {"i": winner}
            )
        await conn.rollback()

    it_clock.advance(timedelta(hours=20))  # 6.11 08:00 UTC — показ о 09:00 UTC
    stats = await s.applications.showings_tick()
    assert stats["reminders"] == 2
    it_clock.advance(timedelta(hours=2))
    for tg in (tenant_tg, OWNER):
        await s.applications.answer_showing(tg, winner, ShowingAnswerIn(answer=ShowingAnswer.DONE))
    card = await s.applications.card(OWNER, winner)
    assert card.status is ApplicationStatus.SHOWING_DONE and card.can_create_lease

    async with engine.connect() as conn:
        history = (
            (
                await conn.execute(
                    text(
                        "SELECT to_status FROM application_status_history "
                        "WHERE application_id = :i ORDER BY id"
                    ),
                    {"i": winner},
                )
            )
            .scalars()
            .all()
        )
        with pytest.raises(IntegrityError):  # після показу заявка ще відкрита — дубль не можна
            await conn.execute(
                text(
                    "INSERT INTO applications (listing_id, tenant_user_id, expires_at) "
                    "SELECT listing_id, tenant_user_id, now() FROM applications WHERE id = :i"
                ),
                {"i": winner},
            )
        await conn.rollback()
    assert history == ["pending", "approved", "showing_scheduled", "showing_done"]
