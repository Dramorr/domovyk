"""Модуль properties на справжньому Postgres + PostGIS: запити й обмеження схеми."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import DuplicateAddress
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.interfaces.telegram.notifications import make_renderer
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate, RoomIn
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V
from tests.fakes_properties import PDF, FakeNotifier

pytestmark = pytest.mark.integration

OWNER, OTHER = 42, 43


async def _user(s: Services, tg: int) -> None:
    await s.users.register(tg, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
    await s.users.add_phone(tg, f"+38067{tg:07d}")


def _flat(house: str = "12", apt: str | None = "5", lat: float | None = 49.84) -> PropertyCreate:
    return PropertyCreate(
        kind=PropertyKind.APARTMENT,
        address=AddressIn(
            street="вулиця Тараса Шевченка",
            house_number=house,
            district="Шевченківський район",
            lat=lat,
            lon=24.03 if lat else None,
        ),
        apartment=apt,
        rooms=2,
        area=Decimal("54.5"),
    )


async def test_full_ownership_cycle(
    services: Services, engine: AsyncEngine, it_clock: FixedClock
) -> None:
    s = services
    for tg in (ADMIN_TG, OWNER, OTHER):
        await _user(s, tg)

    prop = await s.properties.create_property(OWNER, _flat())
    assert prop.has_location and prop.district == "Шевченківський район"
    assert prop.area == Decimal("54.5")
    # Друга квартира в тому ж будинку — той самий будинок і район.
    second = await s.properties.create_property(OWNER, _flat(apt="6", lat=None))
    assert second.has_location
    prop = await s.properties.add_room(OWNER, prop.id, RoomIn(name="Кімната з балконом"))
    assert len(prop.units) == 2

    with pytest.raises(DuplicateAddress):
        await s.properties.create_property(OTHER, _flat(house="12"))

    await s.properties.upload_document(OWNER, prop.id, "довідка.pdf", PDF)
    await s.properties.upload_document(OWNER, prop.id, "довіреність.pdf", PDF)
    submitted = await s.properties.submit_ownership(OWNER, prop.id)
    assert submitted.verification is not None
    vid = submitted.verification.id

    queue = await s.properties.verification_queue(ADMIN_TG)
    assert [(q.verification_id, q.documents_count) for q in queue] == [(vid, 2)]
    assert queue[0].owner.properties_count == 2

    case = await s.properties.approve(ADMIN_TG, vid, "Шевченко Тарас Григорович")
    assert case.property.ownership_status is OwnershipStatus.VERIFIED

    notifier = FakeNotifier()
    sent = await s.notifications.send_pending(make_renderer("https://x.test/app/"), notifier)
    assert sent == 2  # модератору — черга, власнику — результат
    assert {m.chat_id for m in notifier.messages} == {ADMIN_TG, OWNER}

    it_clock.advance(timedelta(days=31))
    assert await s.properties.purge_documents() == 2

    async with engine.connect() as conn:
        signals = (await conn.execute(text("SELECT signal_type FROM risk_signals"))).scalars()
        assert list(signals) == ["duplicate_address"]
        actions = list(
            (await conn.execute(text("SELECT action FROM audit_log ORDER BY id"))).scalars()
        )
        assert actions[-1] == "ownership.approve"
        deleted = await conn.scalar(
            text("SELECT count(*) FROM verification_documents WHERE deleted_at IS NOT NULL")
        )
        assert deleted == 2
        districts = await conn.scalar(text("SELECT count(*) FROM districts"))
        assert districts == 1


async def test_db_constraints(services: Services, engine: AsyncEngine) -> None:
    await _user(services, OWNER)
    prop = await services.properties.create_property(OWNER, _flat())
    async with engine.connect() as conn:
        # Квартира без номера — CHECK.
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO properties (owner_user_id, kind, building_id, rooms) "
                    "SELECT owner_user_id, 'apartment', building_id, 1 FROM properties LIMIT 1"
                )
            )
        await conn.rollback()
        # Другий «весь об'єкт» — унікальний індекс.
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("INSERT INTO units (property_id, kind, name) VALUES (:p, 'entire', '')"),
                {"p": prop.id},
            )
        await conn.rollback()
        # Дві відкриті перевірки одного об'єкта — унікальний індекс.
        insert = text(
            "INSERT INTO user_verifications (user_id, kind, status, property_id) "
            "VALUES (:u, 'ownership', 'draft', :p)"
        )
        await conn.execute(insert, {"u": prop.owner_user_id, "p": prop.id})
        with pytest.raises(IntegrityError):
            await conn.execute(insert, {"u": prop.owner_user_id, "p": prop.id})
        await conn.rollback()


async def test_profile_jsonb_roundtrip(services: Services) -> None:
    from domovyk.modules.properties.schemas import PropertyDetails

    await _user(services, OWNER)
    prop = await services.properties.create_property(OWNER, _flat())
    details = PropertyDetails(
        heating="individual_gas",
        ceiling_height=Decimal("2.75"),
        appliances=["fridge", "dishwasher"],
        has_elevator=False,
    )
    saved = await services.properties.update_profile(OWNER, prop.id, details)
    again = await services.properties.get_mine(OWNER, prop.id)
    assert again.details == saved.details
    assert again.details.ceiling_height == Decimal("2.75")
    assert again.details.has_elevator is False
    assert again.completeness is not None and again.completeness.percent > 0
