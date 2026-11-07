"""Модуль leases на справжньому Postgres: запити, обмеження схеми, чат."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict, HasOpenLease
from domovyk.domain.leases import LeaseStatus, MemberRole, PayMethod
from domovyk.domain.properties import PropertyKind
from domovyk.modules.leases.schemas import LeaseCreate, PayDetailsIn
from domovyk.modules.notifications.schemas import LeaseNotificationPrefs
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from tests.fakes import PRIVACY_V, TERMS_V

pytestmark = pytest.mark.integration

OWNER, TENANT, ROOMMATE = 42, 43, 44


async def _setup(s: Services, engine: AsyncEngine) -> LeaseCreate:
    for tg in (OWNER, TENANT, ROOMMATE):
        await s.users.register(tg, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Зелена", house_number="40"),
            apartment="7",
            rooms=2,
            area=Decimal("50"),
        ),
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE properties SET ownership_status = 'verified' WHERE id = :id"),
            {"id": prop.id},
        )
    return LeaseCreate(
        property_id=prop.id,
        start_date=date(2026, 12, 1),
        months=35,
        rent_amount=15000,
        rent_due_day=1,
        pay_method=PayMethod.CARD,
        pay_details=PayDetailsIn(card="4111111111111111", recipient_name="Тарас Шевченко"),
    )


async def test_lease_cycle(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    lease, invite = await s.leases.create_lease(OWNER, data)
    assert lease.end_date == date(2029, 10, 31)  # 35 місяців — межа без нотаріуса
    with pytest.raises(Conflict):
        await s.leases.create_lease(OWNER, data)  # юніт уже зайнятий

    await s.leases.accept_invite(TENANT, invite.token)
    co = await s.leases.new_invite(TENANT, lease.id, MemberRole.CO_TENANT)
    await s.leases.accept_invite(ROOMMATE, co.token)
    await s.leases.accept_terms(TENANT, lease.id)

    await s.leases.post_message(TENANT, lease.id, "Привіт!")
    await s.leases.post_message(ROOMMATE, lease.id, "І від мене привіт")
    page = await s.leases.messages(OWNER, lease.id)
    assert [m.body for m in page.messages if m.body] == ["Привіт!", "І від мене привіт"]
    assert page.messages[0].system_code == "lease_created"
    owner_view = await s.leases.get(OWNER, lease.id)
    assert owner_view.status is LeaseStatus.AWAITING_SIGNATURES
    assert [m.role for m in owner_view.members] == ["landlord", "responsible", "co_tenant"]
    assert owner_view.unread >= 2
    await s.leases.mark_read(OWNER, lease.id, page.messages[-1].id)
    assert (await s.leases.get(OWNER, lease.id)).unread == 0

    await s.leases.set_notification_prefs(
        TENANT, lease.id, LeaseNotificationPrefs(chat_message=False, quiet_from=23, quiet_to=7)
    )
    prefs = await s.leases.notification_prefs(TENANT, lease.id)
    assert (prefs.chat_message, prefs.quiet_from) == (False, 23)

    with pytest.raises(HasOpenLease):
        await s.users.delete_account(ROOMMATE)
    assert len(await s.leases.list_mine(ROOMMATE)) == 1

    async with engine.connect() as conn:
        stored = await conn.scalar(text("SELECT pay_details::text FROM leases"))
        assert "4111" not in stored
        # Двох відповідальних бути не може (частковий унікальний індекс).
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "UPDATE lease_members SET member_role = 'responsible' "
                    "WHERE member_role = 'co_tenant'"
                )
            )
        await conn.rollback()
        # Строк понад 35 місяців — CHECK у БД (правило 6).
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("UPDATE leases SET end_date = start_date + interval '36 months'")
            )
        await conn.rollback()


async def test_tenant_lease_limit_db(services: Services, engine: AsyncEngine) -> None:
    """FR-LSE-8: запит лічильника на справжній БД — третя оренда відповідальним блокується."""
    from domovyk.core.errors import LeaseLimitReached

    s = services
    base = await _setup(s, engine)
    invites = []
    for i, house in enumerate(("41", "42"), start=1):
        prop = await s.properties.create_property(
            OWNER,
            PropertyCreate(
                kind=PropertyKind.APARTMENT,
                address=AddressIn(street="вул. Зелена", house_number=house),
                apartment=str(i),
                rooms=1,
            ),
        )
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE properties SET ownership_status = 'verified' WHERE id = :id"),
                {"id": prop.id},
            )
        invites.append(
            (await s.leases.create_lease(OWNER, base.model_copy(update={"property_id": prop.id})))[
                1
            ]
        )
    _, first = await s.leases.create_lease(OWNER, base)
    await s.leases.accept_invite(TENANT, first.token)
    await s.leases.accept_invite(TENANT, invites[0].token)
    with pytest.raises(LeaseLimitReached):
        await s.leases.accept_invite(TENANT, invites[1].token)
    await s.leases.accept_invite(ROOMMATE, invites[1].token)


async def test_hide_and_purge_cancelled_db(services: Services, engine: AsyncEngine) -> None:
    """FR-LSE-9 на справжній БД: обидві сторони прибрали → оренда стерта каскадом."""
    s = services
    data = await _setup(s, engine)
    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.post_message(TENANT, lease.id, "Привіт")
    await s.leases.cancel(OWNER, lease.id)
    await s.leases.hide(OWNER, lease.id)
    assert await s.leases.list_mine(OWNER) == []
    assert len(await s.leases.list_mine(TENANT)) == 1
    await s.leases.hide(TENANT, lease.id)
    async with engine.connect() as conn:
        for table in ("leases", "lease_members", "conversations", "messages", "lease_hidden"):
            assert await conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table  # noqa: S608


async def test_termination_db(
    services: Services, engine: AsyncEngine, it_clock: FixedClock
) -> None:
    """FR-LSE-7 на справжній БД: розірвання, CHECK дати, автозавершення."""
    from datetime import timedelta

    from domovyk.modules.leases.schemas import TerminationIn

    s = services
    data = await _setup(s, engine)
    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE leases SET status='active' WHERE id=:i"), {"i": lease.id})
    view = await s.leases.request_termination(
        TENANT, lease.id, TerminationIn(date=date(2027, 3, 31), reason="Переїжджаю")
    )
    assert view.status is LeaseStatus.ENDING
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):  # дата розірвання після кінця договору
            await conn.execute(
                text("UPDATE leases SET termination_date = end_date + 1 WHERE id=:i"),
                {"i": lease.id},
            )
        await conn.rollback()
    it_clock.advance(timedelta(days=150))  # 2027-04-04
    assert await s.leases.finish_due() == 1
    assert (await s.leases.get(OWNER, lease.id)).status is LeaseStatus.ENDED
