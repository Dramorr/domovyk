"""Модуль listings на справжньому Postgres: запити, обмеження, pHash-двійники (FR-LST-1..5)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from domovyk.core.errors import HasLiveListing, UnitBusy
from domovyk.domain.listings import ListingStatus
from domovyk.domain.properties import PropertyKind
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from domovyk.modules.search.schemas import SavedSearchIn, SearchIn
from tests.integration.test_leases_db import OWNER, TENANT, _setup
from tests.unit.test_property_profile import FULL

pytestmark = pytest.mark.integration

TERMS = ListingIn(price_amount=15000, deposit_amount=15000, min_term_months=11)
YES = PublishIn(owner_declaration=True)


async def test_listing_lifecycle_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    pid = data.property_id
    await s.properties.update_profile(OWNER, pid, FULL)

    draft = await s.listings.save(OWNER, pid, TERMS)
    assert draft.listing is not None and draft.listing.status is ListingStatus.DRAFT
    state = await s.listings.publish(OWNER, pid, YES)
    assert state.listing is not None and state.listing.status is ListingStatus.ACTIVE
    with pytest.raises(HasLiveListing):
        await s.properties.delete_property(OWNER, pid)
    (mine,) = await s.listings.mine(OWNER)
    assert mine.id == state.listing.id

    # Друге живе оголошення на той самий юніт база не пустить.
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO listings (unit_id, created_by, price_amount) "
                    "SELECT unit_id, created_by, 9000 FROM listings WHERE id = :i"
                ),
                {"i": state.listing.id},
            )
        await conn.rollback()
        with pytest.raises(IntegrityError):  # опубліковане — лише з галочкою власника
            await conn.execute(
                text("UPDATE listings SET owner_declaration_at = NULL WHERE id = :i"),
                {"i": state.listing.id},
            )
        await conn.rollback()

    # Оренда на об'єкті: оголошення «здано», нове не опублікувати.
    lease, _ = await s.leases.create_lease(OWNER, data)
    rented = await s.listings.state(OWNER, pid)
    assert rented.listing is not None and rented.listing.status is ListingStatus.RENTED
    fresh = await s.listings.save(OWNER, pid, TERMS)
    assert fresh.listing is not None and fresh.listing.id != state.listing.id
    with pytest.raises(UnitBusy):
        await s.listings.publish(OWNER, pid, YES)
    assert lease.id


async def test_photo_twins_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    other = await s.properties.create_property(
        TENANT,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Інша", house_number="5"),
            apartment="1",
            rooms=1,
        ),
    )
    async with engine.begin() as conn:
        for prop_id, phash in ((data.property_id, 0x0F0F), (other.id, 0x0F0C), (other.id, -1)):
            await conn.execute(
                text(
                    "INSERT INTO property_photos (property_id, storage_key, width, height, phash) "
                    "VALUES (:p, :k, 640, 480, :h)"
                ),
                {"p": prop_id, "k": f"photos/{uuid.uuid4()}.jpg", "h": phash},
            )
    # 0x0F0F і 0x0F0C різняться на 2 біти; -1 (усі одиниці) — далеко.
    assert await s.properties.photo_twins(data.property_id) == [other.id]
    assert await s.properties.photo_twins(other.id) == [data.property_id]


async def test_search_and_favorites_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    pid = data.property_id
    await s.properties.update_profile(OWNER, pid, FULL)
    await s.listings.save(OWNER, pid, TERMS)
    state = await s.listings.publish(OWNER, pid, YES)
    assert state.listing is not None
    listing_id = state.listing.id

    options = await s.search.options(TENANT, "lviv")
    names = {d.name for d in options.districts}
    assert {"Личаківський район", "Сихівський район"} <= names  # з міграції 0018

    page = await s.search.search(TENANT, SearchIn(rooms=[2], price_max=20000))
    assert [i.id for i in page.items] == [listing_id]
    assert (await s.search.search(TENANT, SearchIn(price_max=10000))).total == 0

    await s.search.add_favorite(TENANT, listing_id)
    await s.search.add_favorite(TENANT, listing_id)  # ON CONFLICT DO NOTHING
    (fav,) = await s.search.favorites(TENANT)
    assert fav.id == listing_id and fav.actual
    await s.listings.close(OWNER, pid)
    (fav,) = await s.search.favorites(TENANT)
    assert not fav.actual
    await s.search.remove_favorite(TENANT, listing_id)
    assert await s.search.favorites(TENANT) == []


async def test_saved_search_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    pid = data.property_id
    saved = await s.search.save_search(
        TENANT, SavedSearchIn(name="2к до 20 000", criteria=SearchIn(rooms=[2], price_max=20000))
    )
    assert saved.criteria.rooms == [2] and saved.criteria.city_code == "lviv"
    await s.properties.update_profile(OWNER, pid, FULL)
    await s.listings.save(OWNER, pid, TERMS)
    await s.listings.publish(OWNER, pid, YES)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT payload->>'search_name', dedupe_key FROM notifications "
                    "WHERE type = 'new_match'"
                )
            )
        ).all()
    assert len(rows) == 1 and rows[0][0] == "2к до 20 000"
    assert (await s.search.set_notify(TENANT, saved.id, False)).notify is False
    await s.search.delete_search(TENANT, saved.id)
    assert await s.search.saved(TENANT) == []
