"""Збережені пошуки й сповіщення new_match (M6, частина 3): FR-SRC-3. Лише вигадані дані."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict, InvalidInput, NotFound
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.interfaces.telegram.notifications import make_renderer
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from domovyk.modules.notifications.service import PendingNotification
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from domovyk.modules.search.schemas import SavedSearchIn, SearchIn
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.unit.test_property_profile import FULL

OWNER, TENANT, OTHER = 42, 43, 44
YES = PublishIn(owner_declaration=True)
DAY = datetime(2026, 11, 17, 10, tzinfo=UTC)  # 12:00 за Києвом


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(DAY)


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (OTHER, "Оля")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _publish(
    s: Services, db: FakeDb, clock: FixedClock, *, house: str, price: int, rooms: int = 2
) -> uuid.UUID:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(
                street="вул. Тестова", house_number=house, district="Галицький район"
            ),
            apartment="3",
            rooms=rooms,
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    await s.properties.update_profile(OWNER, prop.id, FULL)
    await s.listings.save(OWNER, prop.id, ListingIn(price_amount=price))
    clock.advance(timedelta(minutes=1))
    state = await s.listings.publish(OWNER, prop.id, YES)
    assert state.listing is not None
    return prop.id


def _matches(db: FakeDb, user_id: uuid.UUID) -> list[dict[str, object]]:
    return [
        n.payload for n in db.prop.notifications if n.type == "new_match" and n.user_id == user_id
    ]


def _saved(name: str, **criteria: object) -> SavedSearchIn:
    return SavedSearchIn(name=name, criteria=SearchIn(**criteria))  # type: ignore[arg-type]


async def test_saved_search_crud(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    saved = await s.search.save_search(
        TENANT, _saved("  2к  до 20 000 ", rooms=[2], price_max=20000)
    )
    assert saved.name == "2к до 20 000" and saved.notify and saved.criteria.city_code == "lviv"
    assert [x.id for x in await s.search.saved(TENANT)] == [saved.id]
    assert await s.search.saved(OTHER) == []
    with pytest.raises(NotFound):
        await s.search.set_notify(OTHER, saved.id, False)
    off = await s.search.set_notify(TENANT, saved.id, False)
    assert not off.notify
    with pytest.raises(InvalidInput):  # фільтр перевіряємо так само, як пошук
        await s.search.save_search(TENANT, _saved("x", price_min=30000, price_max=10000))
    await s.search.delete_search(TENANT, saved.id)
    assert await s.search.saved(TENANT) == []


async def test_saved_search_limit(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    for i in range(10):
        await s.search.save_search(TENANT, _saved(f"пошук {i}"))
    with pytest.raises(Conflict):
        await s.search.save_search(TENANT, _saved("одинадцятий"))


async def test_new_listing_notifies_matching_searches_once(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    tenant = await s.users.find_by_telegram(TENANT)
    other = await s.users.find_by_telegram(OTHER)
    owner = await s.users.find_by_telegram(OWNER)
    assert tenant and other and owner
    await s.search.save_search(TENANT, _saved("2к до 20 000", rooms=[2], price_max=20000))
    await s.search.save_search(TENANT, _saved("будь-що"))  # теж підходить — але одне сповіщення
    await s.search.save_search(OTHER, _saved("лише 3к", rooms=[3]))
    await s.search.save_search(OWNER, _saved("моє"))  # своє оголошення собі не шлемо

    pid = await _publish(s, db, clock, house="1", price=15000)
    (payload,) = _matches(db, tenant.id)
    assert payload["search_name"] == "2к до 20 000" and payload["price"] == 15000
    assert "house_number" not in payload  # адреса лише вулицею (FR-LST-4)
    assert _matches(db, other.id) == [] and _matches(db, owner.id) == []
    assert "match_notified" in [e[1] for e in db.events]

    # Пауза й повернення — те саме оголошення, повторно не шлемо.
    await s.listings.pause(OWNER, pid)
    await s.listings.publish(OWNER, pid, PublishIn())
    assert len(_matches(db, tenant.id)) == 1


async def test_quiet_hours_and_daily_digest(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    tenant = await s.users.find_by_telegram(TENANT)
    assert tenant
    await s.search.save_search(TENANT, _saved("усе"))

    clock.advance(timedelta(hours=11))  # 23:00 за Києвом — тихі години
    await _publish(s, db, clock, house="1", price=15000)
    (night,) = [n for n in db.prop.notifications if n.type == "new_match"]
    assert night.send_after == datetime(2026, 11, 18, 6, tzinfo=UTC)  # 08:00 за Києвом

    # Денний ліміт вичерпано — замість окремих сповіщень один вечірній дайджест.
    clock.advance(timedelta(hours=10))  # 18.11, 11:00 за Києвом
    for i in range(12):
        await _publish(s, db, clock, house=str(10 + i), price=15000)
    mine = [n for n in db.prop.notifications if n.type == "new_match" and n.user_id == tenant.id]
    single = [n for n in mine if not n.payload.get("digest")]
    digests = [n for n in mine if n.payload.get("digest")]
    assert len(single) == 1 + 10 and len(digests) == 1
    assert digests[0].send_after == datetime(2026, 11, 18, 17, tzinfo=UTC)  # 19:00 за Києвом


def test_new_match_text() -> None:
    render = make_renderer("https://example.test/app/")
    lid = uuid.uuid4()
    msg = render(
        PendingNotification(
            1,
            uuid.uuid4(),
            "new_match",
            {
                "listing_id": str(lid),
                "search_name": "2к <до> 20 000",
                "price": 15000,
                "rooms": 2,
                "district": "Галицький район",
                "street": "вул. Тестова",
            },
        )
    )
    assert msg is not None
    assert "15 000 ₴/міс" in msg.text and "2-кімн." in msg.text and "&lt;до&gt;" in msg.text
    assert msg.buttons[0].web_app_url is not None and f"lst_{lid}" in msg.buttons[0].web_app_url
    digest = render(PendingNotification(2, uuid.uuid4(), "new_match", {"digest": True}))
    assert digest is not None and "searches" in (digest.buttons[0].web_app_url or "")
