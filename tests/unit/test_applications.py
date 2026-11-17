"""Заявки орендарів (M6, частина 4): FR-APP-1..3, 6, 7, правило 9. Лише вигадані дані."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import (
    Conflict,
    ContactsInText,
    InvalidTransition,
    NotFound,
    PermissionDenied,
)
from domovyk.domain.applications import ApplicationStatus, DeclineReason, clean_application_message
from domovyk.domain.leases import PayMethod
from domovyk.domain.listings import ListingStatus
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.interfaces.telegram.notifications import make_renderer
from domovyk.modules.applications.schemas import ApplicationIn, DeclineIn
from domovyk.modules.leases.schemas import LeaseConditions, PayDetailsIn
from domovyk.modules.listings.schemas import ListingIn, PublishIn
from domovyk.modules.notifications.service import PendingNotification
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.unit.test_property_profile import FULL

OWNER, TENANT, OTHER, STRANGER = 42, 43, 44, 45
CARD = "4111 1111 1111 1111"  # тестовий номер (Luhn), не справжня картка
HELLO = ApplicationIn(message="Шукаю з дружиною на рік, без тварин, заїхати з 1 грудня.")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 18, 10, tzinfo=UTC))


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (OTHER, "Оля"), (STRANGER, "Сусід")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _listing(s: Services, db: FakeDb) -> tuple[uuid.UUID, uuid.UUID]:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Тестова", house_number="7"),
            apartment="12",
            entrance="2",
            floor=4,
            rooms=2,
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    await s.properties.update_profile(OWNER, prop.id, FULL)
    await s.listings.save(OWNER, prop.id, ListingIn(price_amount=15000))
    state = await s.listings.publish(OWNER, prop.id, PublishIn(owner_declaration=True))
    assert state.listing is not None
    return prop.id, state.listing.id


def _events(db: FakeDb, user_id: uuid.UUID) -> list[str]:
    return [
        str(n.payload["event"])
        for n in db.prop.notifications
        if n.type == "application" and n.user_id == user_id
    ]


async def _uid(s: Services, tg: int) -> uuid.UUID:
    user = await s.users.find_by_telegram(tg)
    assert user is not None
    return user.id


# ------------------------------------------------------------------ домен


def test_clean_message() -> None:
    assert clean_application_message("  ") is None
    assert clean_application_message(" Привіт ") == "Привіт"
    with pytest.raises(ContactsInText):  # контакти — лише після схвалення (правило 9)
        clean_application_message("Пишіть у телеграм @andrii_test або 067 123 45 67")


# ----------------------------------------------------------------- сервіс


async def test_apply_hides_contacts_until_approval(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, listing = await _listing(s, db)
    card = await s.applications.apply(TENANT, listing, HELLO)
    assert card.status is ApplicationStatus.PENDING and card.my_role == "tenant"
    assert card.contacts is None and not card.can_chat and card.can_withdraw
    assert card.other.display_name == "Ірина"
    assert _events(db, await _uid(s, OWNER)) == ["new"]
    assert "application_sent" in [e[1] for e in db.events]

    with pytest.raises(Conflict):  # одна відкрита заявка на оголошення
        await s.applications.apply(TENANT, listing, ApplicationIn())
    with pytest.raises(PermissionDenied):  # на своє не відгукуються
        await s.applications.apply(OWNER, listing, ApplicationIn())
    with pytest.raises(PermissionDenied):  # чат — після схвалення
        await s.applications.messages(TENANT, card.id)
    with pytest.raises(NotFound):  # чужа заявка виглядає як неіснуюча
        await s.applications.card(STRANGER, card.id)

    (incoming,) = await s.applications.incoming(OWNER)
    assert incoming.id == card.id and incoming.other.display_name == "Андрій"
    landlord = await s.applications.card(OWNER, card.id)
    assert landlord.can_approve and landlord.can_decline and not landlord.can_create_lease


async def test_approve_reveals_contacts_and_chat(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, listing = await _listing(s, db)
    app = await s.applications.apply(TENANT, listing, HELLO)
    approved = await s.applications.approve(OWNER, app.id)
    assert approved.status is ApplicationStatus.APPROVED and approved.contacts is not None
    assert approved.contacts.phone == f"+38067{TENANT:07d}"  # власник бачить орендаря
    tenant_view = await s.applications.card(TENANT, app.id)
    assert tenant_view.contacts is not None
    assert tenant_view.contacts.phone == f"+38067{OWNER:07d}"
    assert (tenant_view.contacts.house_number, tenant_view.contacts.apartment) == ("7", "12")
    assert tenant_view.contacts.floor == 4 and tenant_view.can_chat
    assert _events(db, await _uid(s, TENANT)) == ["approved"]

    await s.applications.post_message(TENANT, app.id, "Коли зручно подивитись?")
    page = await s.applications.messages(OWNER, app.id)
    assert [m.body for m in page.messages if m.body][-1] == "Коли зручно подивитись?"
    assert _events(db, await _uid(s, OWNER))[-1] == "message"
    with pytest.raises(InvalidTransition):
        await s.applications.approve(OWNER, app.id)


async def test_decline_and_withdraw(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, listing = await _listing(s, db)
    a = await s.applications.apply(TENANT, listing, HELLO)
    b = await s.applications.apply(OTHER, listing, ApplicationIn())
    with pytest.raises(PermissionDenied):
        await s.applications.decline(TENANT, a.id, DeclineIn())  # рішення — за власником
    declined = await s.applications.decline(OWNER, a.id, DeclineIn(reason=DeclineReason.TERMS))
    assert declined.status is ApplicationStatus.DECLINED
    assert declined.decline_reason is DeclineReason.TERMS
    assert _events(db, await _uid(s, TENANT)) == ["declined"]
    # Після відмови можна відгукнутись знову — відкритої заявки вже немає.
    again = await s.applications.apply(TENANT, listing, ApplicationIn())
    assert again.id != a.id

    withdrawn = await s.applications.withdraw(OTHER, b.id)
    assert withdrawn.status is ApplicationStatus.WITHDRAWN
    assert _events(db, await _uid(s, OWNER)).count("withdrawn") == 1


async def test_expire_after_72_hours(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    _, listing = await _listing(s, db)
    app = await s.applications.apply(TENANT, listing, HELLO)
    clock.advance(timedelta(hours=71))
    assert await s.applications.expire_due() == 0
    clock.advance(timedelta(hours=2))
    assert await s.applications.expire_due() == 1
    assert (await s.applications.card(TENANT, app.id)).status is ApplicationStatus.EXPIRED
    assert _events(db, await _uid(s, TENANT)) == ["expired"]


async def test_lease_from_application(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid, listing = await _listing(s, db)
    chosen = await s.applications.apply(TENANT, listing, HELLO)
    other = await s.applications.apply(OTHER, listing, ApplicationIn())
    conditions = LeaseConditions(
        start_date=date(2026, 12, 1),
        months=11,
        rent_amount=15000,
        rent_due_day=1,
        pay_method=PayMethod.CARD,
        pay_details=PayDetailsIn(card=CARD, recipient_name="Ірина Тестова"),
    )
    with pytest.raises(InvalidTransition):  # спершу схвалити
        await s.applications.create_lease(OWNER, chosen.id, conditions)
    await s.applications.approve(OWNER, chosen.id)
    lease = await s.applications.create_lease(OWNER, chosen.id, conditions)

    assert lease.status.value == "awaiting_tenant"
    tenant_id = await _uid(s, TENANT)
    assert (tenant_id, "responsible") in [(m.user_id, m.role) for m in lease.members]
    card = await s.applications.card(OWNER, chosen.id)
    assert card.status is ApplicationStatus.LEASE_CREATED and card.lease_id == lease.id
    # Оголошення «здано», інші відкриті заявки закрито з причиною «вже здано».
    state = await s.listings.state(OWNER, pid)
    assert state.listing is not None and state.listing.status is ListingStatus.RENTED
    rest = await s.applications.card(OTHER, other.id)
    assert rest.status is ApplicationStatus.DECLINED
    assert rest.decline_reason is DeclineReason.RENTED
    # Орендар бачить оренду у своєму списку й отримав сповіщення погодити умови.
    assert any(x.id == lease.id for x in await s.leases.list_mine(TENANT))


def test_application_texts() -> None:
    render = make_renderer("https://example.test/app/")
    aid = uuid.uuid4()
    msg = render(
        PendingNotification(
            1,
            uuid.uuid4(),
            "application",
            {
                "application_id": str(aid),
                "event": "declined",
                "reason": "rented",
                "street": "вул. Тестова",
                "rooms": 2,
                "price": 15000,
            },
        )
    )
    assert msg is not None and "вже здали" in msg.text and "2-кімн." in msg.text
    assert f"app_{aid}" in (msg.buttons[0].web_app_url or "")
