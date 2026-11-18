"""Пошук для орендаря (M6, частина 2): FR-SRC-1..6, FR-LST-4. Лише вигадані дані."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import InvalidInput, NotFound
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.domain.search import Amenity, Candidate, Criteria, clean_criteria, matches
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from domovyk.modules.properties.repository import StoredPhoto
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from domovyk.modules.search.schemas import SearchIn
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.unit.test_property_profile import FULL

OWNER, TENANT = 42, 43
YES = PublishIn(owner_declaration=True)


# ------------------------------------------------------------------ домен


def _x(**kw: object) -> Candidate:
    base: dict[str, object] = {
        "kind": PropertyKind.APARTMENT,
        "district": "Галицький район",
        "rooms": 2,
        "area": 54.0,
        "floor": 3,
        "price": 15000,
        "completeness": 80,
        "details": FULL.model_dump(mode="json"),
    }
    base.update(kw)
    return Candidate(**base)  # type: ignore[arg-type]


def test_matches_basic_filters() -> None:
    assert matches(Criteria(), _x())
    assert matches(Criteria(rooms=frozenset({2})), _x())
    assert matches(Criteria(rooms=frozenset({4})), _x(rooms=6))  # «4+»
    assert not matches(Criteria(rooms=frozenset({0, 1})), _x())
    assert not matches(Criteria(price_max=14000), _x())
    assert not matches(Criteria(area_min=60), _x())
    assert not matches(Criteria(area_min=10), _x(area=None))  # площа не вказана — не підходить
    assert not matches(Criteria(districts=frozenset({"Сихівський район"})), _x())
    assert not matches(Criteria(completeness_min=90), _x())


def test_matches_floors_rules_and_amenities() -> None:
    # FULL: 9 поверхів, коти дозволені, діти так, реєстрації ні, ліфт є, пральна машина є.
    assert not matches(Criteria(floor_not_first=True), _x(floor=1))
    assert not matches(Criteria(floor_not_last=True), _x(floor=9))
    assert matches(Criteria(floor_not_first=True, floor_not_last=True), _x(floor=5))
    assert matches(Criteria(pets=True, children=True), _x())
    assert not matches(Criteria(registration=True), _x())
    good = frozenset({Amenity.WASHING_MACHINE, Amenity.ELEVATOR, Amenity.SHELTER})
    assert matches(Criteria(amenities=good), _x())
    assert not matches(Criteria(amenities=frozenset({Amenity.DISHWASHER})), _x())
    # Не відповів в анкеті — для фільтра це «ні»: орендар просив певності.
    assert not matches(Criteria(pets=True), _x(details={}))


def test_clean_criteria() -> None:
    args = {
        "kinds": [],
        "districts": [],
        "rooms": [1],
        "price_min": None,
        "price_max": None,
        "area_min": None,
        "floor_not_first": False,
        "floor_not_last": False,
        "pets": False,
        "children": False,
        "registration": False,
        "amenities": [],
        "completeness_min": 0,
        "completeness_floor": 40,
    }
    assert clean_criteria(**args).completeness_min == 40  # type: ignore[arg-type]
    with pytest.raises(InvalidInput):
        clean_criteria(**{**args, "rooms": [9]})  # type: ignore[arg-type]
    with pytest.raises(InvalidInput):
        clean_criteria(**{**args, "price_min": 20000, "price_max": 10000})  # type: ignore[arg-type]


# --------------------------------------------------------------- оточення


CLOCK = FixedClock(datetime(2026, 11, 17, 10, tzinfo=UTC))


@pytest.fixture
async def env() -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=CLOCK)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _listed(
    s: Services,
    db: FakeDb,
    *,
    house: str,
    district: str,
    price: int,
    rooms: int = 2,
    publish: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Тестова", house_number=house, district=district),
            apartment="3",
            floor=4,
            rooms=rooms,
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    await s.properties.update_profile(OWNER, prop.id, FULL)
    state = await s.listings.save(OWNER, prop.id, ListingIn(price_amount=price))
    if publish:
        CLOCK.advance(timedelta(minutes=1))  # різний час публікації — для сортування
        state = await s.listings.publish(OWNER, prop.id, YES)
    assert state.listing is not None
    return prop.id, state.listing.id


# ----------------------------------------------------------------- сервіс


async def test_search_filters_and_hides_exact_address(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, cheap = await _listed(s, db, house="1", district="Галицький район", price=12000)
    _, dear = await _listed(s, db, house="2", district="Сихівський район", price=25000, rooms=3)
    await _listed(s, db, house="3", district="Галицький район", price=9000, publish=False)

    options = await s.search.options(TENANT, None)
    assert options.completeness_min == 40
    galician = next(d.id for d in options.districts if d.name == "Галицький район")

    everything = await s.search.search(TENANT, SearchIn())
    assert [i.id for i in everything.items] == [dear, cheap]  # новіші спершу, чернетки немає
    page = await s.search.search(TENANT, SearchIn(district_ids=[galician], price_max=20000))
    assert [i.id for i in page.items] == [cheap] and page.total == 1
    assert (await s.search.search(TENANT, SearchIn(rooms=[3]))).total == 1

    # FR-LST-4 (ADR 0021): вулиця й будинок — так; квартира, під'їзд і поверх — ні.
    card = await s.search.card(TENANT, cheap)
    dumped = card.model_dump()
    assert card.street == "вул. Тестова" and card.house_number == "1"
    assert card.district == "Галицький район"
    assert not {"apartment", "floor", "entrance"} & dumped.keys()
    assert page.items[0].house_number == "1"
    assert card.owner.display_name == "Ірина" and not card.is_mine
    # Власник себе в пошуку не бачить.
    assert (await s.search.search(OWNER, SearchIn())).total == 0


async def test_search_requires_known_city(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    with pytest.raises(InvalidInput):
        await s.search.search(TENANT, SearchIn(city_code="kyiv"))
    with pytest.raises(InvalidInput):
        await s.search.search(TENANT, SearchIn(district_ids=[999]))


async def test_hidden_listing_is_not_found(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid, listing = await _listed(s, db, house="1", district="Галицький район", price=12000)
    await s.listings.pause(OWNER, pid)
    with pytest.raises(NotFound):
        await s.search.card(TENANT, listing)
    with pytest.raises(NotFound):
        await s.search.add_favorite(TENANT, listing)
    assert (await s.search.card(OWNER, listing)).is_mine  # автор бачить своє


async def test_favorites_keep_closed_listing_marked(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid, listing = await _listed(s, db, house="1", district="Галицький район", price=12000)
    photo = uuid.uuid4()
    db.prop.photos[photo] = StoredPhoto(photo, pid, f"photos/{photo}.jpg", 0, 640, 480)
    await db.storage.put(f"photos/{photo}.jpg", b"jpeg")

    await s.search.add_favorite(TENANT, listing)
    await s.search.add_favorite(TENANT, listing)  # повторно — без дубля
    (fav,) = await s.search.favorites(TENANT)
    assert fav.is_favorite and fav.actual and fav.cover_photo_id == photo
    assert (await s.search.search(TENANT, SearchIn())).items[0].is_favorite

    await s.listings.close(OWNER, pid)
    (fav,) = await s.search.favorites(TENANT)
    assert not fav.actual  # «вже неактуальне»
    # Збережене й далі відкривається — з фото, щоб людина згадала, що це було.
    assert (await s.search.card(TENANT, listing)).status.value == "archived"
    assert await s.search.photo(TENANT, listing, photo) == b"jpeg"

    await s.search.remove_favorite(TENANT, listing)
    assert await s.search.favorites(TENANT) == []
    with pytest.raises(NotFound):
        await s.search.card(TENANT, listing)
