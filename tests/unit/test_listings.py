"""Оголошення власника (M6, частина 1): FR-LST-1..5, ADR 0018. Лише вигадані дані."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import (
    DeclarationRequired,
    HasLiveListing,
    InvalidInput,
    InvalidTransition,
    NotFound,
    OwnershipNotVerified,
    ProfileIncomplete,
    UnitBusy,
)
from domovyk.domain.leases import PayMethod
from domovyk.domain.listings import (
    LISTING,
    ListingStatus,
    clean_available_from,
    clean_min_term,
    clean_price,
    find_risk_words,
)
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.modules.leases.schemas import LeaseCreate, PayDetailsIn
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from domovyk.modules.properties.repository import StoredPhoto
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.unit.test_property_profile import FULL

OWNER, OTHER = 42, 43
CARD = "4111 1111 1111 1111"  # тестовий номер (Luhn), не справжня картка
TERMS = ListingIn(price_amount=14000, deposit_amount=14000, min_term_months=6)
YES = PublishIn(owner_declaration=True)


# ------------------------------------------------------------------ домен


def test_clean_terms() -> None:
    assert clean_price(14000) == 14000
    for bad in (0, 99, 1_000_001):
        with pytest.raises(InvalidInput):
            clean_price(bad)
    assert clean_min_term(None) is None
    with pytest.raises(InvalidInput):
        clean_min_term(36)  # довше — вже нотаріус (правило 6)
    today = date(2026, 11, 16)
    assert clean_available_from(date(2026, 11, 1), today) is None  # минула дата — «вже вільна»
    assert clean_available_from(date(2026, 12, 1), today) == date(2026, 12, 1)
    with pytest.raises(InvalidInput):
        clean_available_from(today + timedelta(days=400), today)


def test_risk_words() -> None:
    stems = ["комісі", "агент", "ріелтор"]
    text = "Без комісії! Агентство нерухомості, дзвоніть"
    assert find_risk_words(text, stems) == ["комісі", "агент"]
    # Основа має бути початком слова: «реагент» — не агент.
    assert find_risk_words("Пральний реагент і тиша", stems) == []
    assert find_risk_words("", stems) == []


def test_transitions() -> None:
    assert LISTING.can(ListingStatus.DRAFT, ListingStatus.ACTIVE)
    assert LISTING.can(ListingStatus.PAUSED, ListingStatus.ACTIVE)
    assert not LISTING.can(ListingStatus.DRAFT, ListingStatus.PAUSED)
    for final in (ListingStatus.RENTED, ListingStatus.ARCHIVED, ListingStatus.BLOCKED):
        assert not LISTING.can(final, ListingStatus.ACTIVE)


# --------------------------------------------------------------- оточення


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 16, 10, tzinfo=UTC))


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (OTHER, "Андрій"), (ADMIN_TG, "Модератор")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg % 10_000_000:07d}")
    return s, db


async def _flat(
    s: Services, db: FakeDb, *, owner: int = OWNER, house: str = "7", ready: bool = True
) -> uuid.UUID:
    prop = await s.properties.create_property(
        owner,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Тестова", house_number=house),
            apartment="3",
            rooms=2,
        ),
    )
    if ready:
        db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
        await s.properties.update_profile(owner, prop.id, FULL)
    return prop.id


def _photo(db: FakeDb, property_id: uuid.UUID, phash: int) -> None:
    pid = uuid.uuid4()
    db.prop.photos[pid] = StoredPhoto(pid, property_id, f"photos/{pid}.jpg", 0, 640, 480)
    db.prop.phashes[pid] = phash


def _alerts(db: FakeDb) -> list[str]:
    return [str(n.payload["reason"]) for n in db.prop.notifications if n.type == "moderation_alert"]


# ----------------------------------------------------------------- сервіс


async def test_save_creates_draft_and_updates_it(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db, ready=False)
    empty = await s.listings.state(OWNER, pid)
    assert empty.listing is None
    assert not empty.readiness.ready and not empty.readiness.ownership_verified
    assert empty.suggested_available_from == date(2026, 11, 16)  # вільна — з сьогодні (clock)

    first = await s.listings.save(OWNER, pid, TERMS)
    assert first.listing is not None and first.listing.status is ListingStatus.DRAFT
    again = await s.listings.save(
        OWNER, pid, TERMS.model_copy(update={"price_amount": 15000, "utilities_included": True})
    )
    assert again.listing is not None and again.listing.id == first.listing.id
    assert again.listing.price_amount == 15000 and again.listing.utilities_included
    assert len(db.listing.listings) == 1


async def test_publish_requires_declaration_ownership_and_profile(
    env: tuple[Services, FakeDb],
) -> None:
    s, db = env
    pid = await _flat(s, db, ready=False)
    await s.listings.save(OWNER, pid, TERMS)
    with pytest.raises(DeclarationRequired):
        await s.listings.publish(OWNER, pid, PublishIn())
    with pytest.raises(OwnershipNotVerified):
        await s.listings.publish(OWNER, pid, YES)
    db.prop.properties[pid].ownership = OwnershipStatus.VERIFIED
    with pytest.raises(ProfileIncomplete):
        await s.listings.publish(OWNER, pid, YES)
    await s.properties.update_profile(OWNER, pid, FULL)

    state = await s.listings.publish(OWNER, pid, YES)
    assert state.readiness.ready
    assert state.listing is not None and state.listing.status is ListingStatus.ACTIVE
    assert state.listing.owner_declared and state.listing.published_at is not None
    assert [e[1] for e in db.events].count("listing_published") == 1


async def test_pause_resume_close_and_relist(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db)
    await s.listings.save(OWNER, pid, TERMS)
    published = await s.listings.publish(OWNER, pid, YES)
    assert published.listing is not None
    paused = await s.listings.pause(OWNER, pid)
    assert paused.listing is not None and paused.listing.status is ListingStatus.PAUSED
    # Галочку вже поставлено — повернення з паузи її не вимагає; дата публікації та сама.
    resumed = await s.listings.publish(OWNER, pid, PublishIn())
    assert resumed.listing is not None and resumed.listing.status is ListingStatus.ACTIVE
    assert resumed.listing.published_at == published.listing.published_at
    assert [e[1] for e in db.events].count("listing_published") == 1

    closed = await s.listings.close(OWNER, pid)
    assert closed.listing is not None and closed.listing.status is ListingStatus.ARCHIVED
    with pytest.raises(NotFound):
        await s.listings.pause(OWNER, pid)
    # Нове оголошення — новий запис, зняте лишається історією.
    fresh = await s.listings.save(OWNER, pid, TERMS)
    assert fresh.listing is not None and fresh.listing.status is ListingStatus.DRAFT
    assert fresh.listing.id != closed.listing.id and not fresh.listing.owner_declared
    assert len(db.listing.listings) == 2


async def test_close_draft_deletes_it(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db)
    await s.listings.save(OWNER, pid, TERMS)
    state = await s.listings.close(OWNER, pid)
    assert state.listing is None and not db.listing.listings
    await s.listings.save(OWNER, pid, TERMS)
    with pytest.raises(InvalidTransition):
        await s.listings.pause(OWNER, pid)  # чернетку на паузу не ставлять


async def test_lease_marks_rented_and_blocks_publish(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db)
    await s.listings.save(OWNER, pid, TERMS)
    await s.listings.publish(OWNER, pid, YES)
    await s.leases.create_lease(
        OWNER,
        LeaseCreate(
            property_id=pid,
            start_date=date(2026, 12, 1),
            months=11,
            rent_amount=14000,
            rent_due_day=5,
            pay_method=PayMethod.CARD,
            pay_details=PayDetailsIn(card=CARD, recipient_name="Ірина Тестова"),
        ),
    )
    state = await s.listings.state(OWNER, pid)
    assert state.listing is not None and state.listing.status is ListingStatus.RENTED
    assert state.listing.closed_at is not None and state.readiness.unit_busy
    # Здана на 11 місяців з 1 грудня: вільна — з наступного дня після кінця оренди.
    lease = (await s.leases.list_mine(OWNER))[0]
    assert state.suggested_available_from == lease.end_date + timedelta(days=1)
    await s.listings.save(OWNER, pid, TERMS)
    with pytest.raises(UnitBusy):
        await s.listings.publish(OWNER, pid, YES)


async def test_property_delete_blocked_by_published_listing(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db)
    await s.listings.save(OWNER, pid, TERMS)
    await s.listings.publish(OWNER, pid, YES)
    with pytest.raises(HasLiveListing):
        await s.properties.delete_property(OWNER, pid)
    await s.listings.close(OWNER, pid)
    await s.properties.delete_property(OWNER, pid)


async def test_strangers_see_nothing(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db)
    await s.listings.save(OWNER, pid, TERMS)
    with pytest.raises(NotFound):
        await s.listings.state(OTHER, pid)
    with pytest.raises(NotFound):
        await s.listings.save(OTHER, pid, TERMS)
    assert await s.listings.mine(OTHER) == []
    (mine,) = await s.listings.mine(OWNER)
    assert mine.property_id == pid


# ------------------------------------------------- FR-LST-5: сигнали ризику


async def test_commission_words_alert_moderators(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _flat(s, db)
    text = (
        "Затишна квартира біля парку, тихі сусіди, ремонт свіжий. Комісія агенції 50%, без торгу."
    )
    await s.properties.update_profile(OWNER, pid, FULL.model_copy(update={"description": text}))
    await s.listings.save(OWNER, pid, TERMS)
    state = await s.listings.publish(OWNER, pid, YES)
    # Не блокує: оголошення живе, але модератор уже знає.
    assert state.listing is not None and state.listing.status is ListingStatus.ACTIVE
    ((_, signal, details),) = db.risk_signals
    assert signal == "commission_keywords" and details["words"] == ["комісі", "агенці"]
    assert _alerts(db) == ["commission_keywords"]


async def test_duplicate_photos_alert(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    mine = await _flat(s, db)
    theirs = await _flat(s, db, owner=OTHER, house="9")
    _photo(db, theirs, 0b1011_0000)
    _photo(db, mine, 0b1011_0011)  # відрізняється на 2 біти — те саме фото
    await s.listings.save(OWNER, mine, TERMS)
    await s.listings.publish(OWNER, mine, YES)
    assert [sig for _, sig, _ in db.risk_signals] == ["duplicate_photos"]
    assert db.risk_signals[0][2]["other_properties"] == [str(theirs)]


async def test_many_addresses_alert(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    for house in ("1", "2", "3"):
        pid = await _flat(s, db, house=house)
        await s.listings.save(OWNER, pid, TERMS)
        await s.listings.publish(OWNER, pid, YES)
    assert [sig for _, sig, _ in db.risk_signals] == ["many_addresses"]
    assert _alerts(db) == ["many_addresses"]
