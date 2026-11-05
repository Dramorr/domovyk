"""Юніт-тести modules/properties/service.py з фейками (FR-PRP-1..5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from domovyk.adapters.geocoder.base import GeocodedAddress, GeocoderError
from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import (
    Conflict,
    DuplicateAddress,
    InvalidInput,
    NotFound,
    NotStaff,
    OwnershipNotVerified,
    PermissionDenied,
    PhoneRequired,
    PropertyAlreadyYours,
    ServiceUnavailable,
    TooManyDocuments,
)
from domovyk.domain.properties import OwnershipStatus, PropertyKind, UnitKind, VerificationStatus
from domovyk.modules.properties.schemas import (
    AddressIn,
    PropertyCreate,
    PropertyOut,
    PropertyUpdate,
    RoomIn,
    VerificationOut,
)
from domovyk.modules.properties.service import PropertiesConfig
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.fakes_properties import PDF

OWNER = 42
OTHER = 43


def _clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 5, 12, 0, tzinfo=UTC))


async def _user(s: Services, tg: int) -> None:
    await s.users.register(tg, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
    await s.users.add_phone(tg, f"+38067{tg:07d}")


def _flat(
    street: str = "вул. Шевченка", house: str = "12", apt: str | None = "5"
) -> PropertyCreate:
    return PropertyCreate(
        kind=PropertyKind.APARTMENT,
        address=AddressIn(street=street, house_number=house, district="Шевченківський район"),
        apartment=apt,
        rooms=2,
        area=Decimal("54"),
    )


@pytest.fixture
def clock() -> FixedClock:
    return _clock()


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    services, db = make_fake_services(clock=clock)
    await _user(services, ADMIN_TG)  # адмін = модератор черги
    await _user(services, OWNER)
    await _user(services, OTHER)
    return services, db


# ------------------------------------------------------------------ створення


async def test_create_property_with_default_unit(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop = await s.properties.create_property(OWNER, _flat())
    assert prop.street == "вул. Шевченка"
    assert prop.apartment == "5"
    assert prop.ownership_status is OwnershipStatus.UNVERIFIED
    assert [u.kind for u in prop.units] == [UnitKind.ENTIRE]
    assert [name for _, name, _ in db.events][-1] == "property_created"
    assert [p.id for p in await s.properties.list_mine(OWNER)] == [prop.id]


async def test_create_requires_phone_and_apartment(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    await s.users.register(77, "Без Номера", terms_version=TERMS_V, privacy_version=PRIVACY_V)
    with pytest.raises(PhoneRequired):
        await s.properties.create_property(77, _flat())
    with pytest.raises(InvalidInput) as exc:
        await s.properties.create_property(OWNER, _flat(apt=None))
    assert exc.value.details["field"] == "apartment"


async def test_house_needs_no_apartment(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    data = _flat(apt="99").model_copy(update={"kind": PropertyKind.HOUSE})
    prop = await s.properties.create_property(OWNER, data)
    assert prop.apartment is None


async def test_only_lviv(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    data = _flat()
    data.address.city = "Київ"
    with pytest.raises(InvalidInput):
        await s.properties.create_property(OWNER, data)


async def test_duplicate_address_of_other_owner_gives_risk_signal(
    env: tuple[Services, FakeDb],
) -> None:
    """FR-PRP-2: «Шевченка, 12а, кв. 5» і «вул. Шевченка 12 А, кв. 5» — та сама квартира."""
    s, db = env
    first = await s.properties.create_property(OWNER, _flat(house="12а"))
    with pytest.raises(DuplicateAddress):
        await s.properties.create_property(OTHER, _flat(street="Шевченка", house="12 А"))
    other = await s.users.find_by_telegram(OTHER)
    assert other is not None
    assert db.risk_signals == [
        (other.id, "duplicate_address", {"existing_property_id": str(first.id)})
    ]
    assert await s.properties.list_mine(OTHER) == []
    # Інша квартира того ж будинку — можна.
    await s.properties.create_property(OTHER, _flat(house="12а", apt="6"))


async def test_same_owner_duplicate_is_friendly(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    await s.properties.create_property(OWNER, _flat())
    with pytest.raises(PropertyAlreadyYours):
        await s.properties.create_property(OWNER, _flat())
    assert db.risk_signals == []


async def test_foreign_property_looks_missing(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    prop = await s.properties.create_property(OWNER, _flat())
    with pytest.raises(NotFound):
        await s.properties.get_mine(OTHER, prop.id)
    with pytest.raises(NotFound):
        await s.properties.upload_document(OTHER, prop.id, "x.pdf", PDF)


async def test_update_details_and_rooms(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    prop = await s.properties.create_property(OWNER, _flat())
    prop = await s.properties.update_details(
        OWNER, prop.id, PropertyUpdate(entrance="2", floor=7, rooms=3, area=Decimal("60.5"))
    )
    assert (prop.entrance, prop.floor, prop.rooms, prop.area) == ("2", 7, 3, Decimal("60.5"))
    prop = await s.properties.add_room(OWNER, prop.id, RoomIn(name="Кімната з балконом"))
    room = next(u for u in prop.units if u.kind is UnitKind.ROOM)
    prop = await s.properties.remove_room(OWNER, prop.id, room.id)
    assert [u.kind for u in prop.units] == [UnitKind.ENTIRE]
    entire = prop.units[0]
    with pytest.raises(NotFound):  # «весь об'єкт» не видаляється
        await s.properties.remove_room(OWNER, prop.id, entire.id)


async def test_search_address(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    found = await s.properties.search_address(OWNER, "вул. Шевченка, 12а")
    assert [(a.street, a.house_number) for a in found] == [("вул. Шевченка", "12а")]
    with pytest.raises(InvalidInput):
        await s.properties.search_address(OWNER, "ab")


async def test_search_address_geocoder_down(clock: FixedClock) -> None:
    class Down:
        async def search(self, query: str, *, city: str, limit: int = 5) -> list[GeocodedAddress]:
            raise GeocoderError("timeout")

    s, _ = make_fake_services(clock=clock, geocoder=Down())
    await _user(s, OWNER)
    with pytest.raises(ServiceUnavailable):
        await s.properties.search_address(OWNER, "Шевченка 12")


# ----------------------------------------------------------- власність: власник


async def test_upload_validation(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop = await s.properties.create_property(OWNER, _flat())
    with pytest.raises(InvalidInput):
        await s.properties.upload_document(OWNER, prop.id, "photo.jpg", b"\xff\xd8\xff")
    big = PDF + b"0" * (10 * 1024 * 1024)
    with pytest.raises(InvalidInput):
        await s.properties.upload_document(OWNER, prop.id, "big.pdf", big)
    for i in range(5):
        await s.properties.upload_document(OWNER, prop.id, f"doc{i}.pdf", PDF)
    with pytest.raises(TooManyDocuments):
        await s.properties.upload_document(OWNER, prop.id, "doc6.pdf", PDF)
    assert len(db.storage.files) == 5


async def test_filename_is_sanitized(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    prop = await s.properties.create_property(OWNER, _flat())
    out = await s.properties.upload_document(OWNER, prop.id, "../../etc/довідка<1>", PDF)
    assert out.verification is not None
    assert out.verification.documents[0].filename == "довідка_1_.pdf"


async def test_remove_document_from_draft(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop = await s.properties.create_property(OWNER, _flat())
    out = await s.properties.upload_document(OWNER, prop.id, "a.pdf", PDF)
    assert out.verification is not None
    doc_id = out.verification.documents[0].id
    out = await s.properties.remove_document(OWNER, prop.id, doc_id)
    assert out.verification is not None and out.verification.documents == []
    assert db.storage.files == {}


async def test_submit_requires_documents(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    prop = await s.properties.create_property(OWNER, _flat())
    with pytest.raises(Conflict):
        await s.properties.submit_ownership(OWNER, prop.id)


async def _submitted(s: Services) -> tuple[PropertyOut, VerificationOut]:
    prop = await s.properties.create_property(OWNER, _flat())
    await s.properties.upload_document(OWNER, prop.id, "довідка.pdf", PDF)
    out = await s.properties.submit_ownership(OWNER, prop.id)
    assert out.verification is not None
    return prop, out.verification


async def test_submit_puts_into_queue_and_notifies_staff(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop, _ = await _submitted(s)
    own = await s.properties.ownership(OWNER, prop.id)
    assert own.ownership_status is OwnershipStatus.PENDING
    assert own.verification is not None
    assert own.verification.status is VerificationStatus.PENDING
    with pytest.raises(Conflict):  # на перевірці документи не додаються
        await s.properties.upload_document(OWNER, prop.id, "ще.pdf", PDF)
    admin = await s.users.find_by_telegram(ADMIN_TG)
    assert admin is not None
    assert [(n.user_id, n.type) for n in db.prop.notifications] == [(admin.id, "moderation_queue")]
    assert [name for _, name, _ in db.events][-1] == "ownership_submitted"


async def test_staff_queue_and_approve(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop, verification = await _submitted(s)
    with pytest.raises(NotStaff):
        await s.properties.verification_queue(OTHER)
    queue = await s.properties.verification_queue(ADMIN_TG)
    assert len(queue) == 1
    assert queue[0].owner.display_name == "Тарас"
    assert queue[0].documents_count == 1
    assert await s.properties.pending_count(ADMIN_TG) == 1

    case = await s.properties.approve(ADMIN_TG, verification.id, "Шевченко Тарас Григорович")
    assert case.property.ownership_status is OwnershipStatus.VERIFIED
    assert case.verification.owner_name_in_doc == "Шевченко Тарас Григорович"
    assert db.audit[-1]["action"] == "ownership.approve"
    assert db.prop.notifications[-1].type == "ownership_verified"
    owner = await s.users.find_by_telegram(OWNER)
    assert owner is not None
    verified = await s.properties.require_verified_owner(owner.id, prop.id)
    assert verified.ownership_status is OwnershipStatus.VERIFIED


async def test_reject_then_resubmit(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop, verification = await _submitted(s)
    case = await s.properties.reject(ADMIN_TG, verification.id, "Адреса не збігається")
    assert case.property.ownership_status is OwnershipStatus.REJECTED
    assert db.prop.notifications[-1].payload["reason"] == "Адреса не збігається"
    with pytest.raises(InvalidInput):  # без причини не можна
        await s.properties.reject(ADMIN_TG, verification.id, " ")
    # Нова спроба — нова перевірка.
    out = await s.properties.upload_document(OWNER, prop.id, "нова.pdf", PDF)
    assert out.verification is not None
    assert out.verification.id != verification.id
    out = await s.properties.submit_ownership(OWNER, prop.id)
    assert out.ownership_status is OwnershipStatus.PENDING


async def test_owner_unverified_cannot_publish(env: tuple[Services, FakeDb]) -> None:
    """Правило 5: без підтвердженої власності — ні оголошення, ні оренди."""
    s, _ = env
    prop = await s.properties.create_property(OWNER, _flat())
    owner = await s.users.find_by_telegram(OWNER)
    other = await s.users.find_by_telegram(OTHER)
    assert owner is not None and other is not None
    with pytest.raises(OwnershipNotVerified):
        await s.properties.require_verified_owner(owner.id, prop.id)
    with pytest.raises(NotFound):
        await s.properties.require_verified_owner(other.id, prop.id)


async def test_moderator_cannot_review_own_property(clock: FixedClock) -> None:
    s, _ = make_fake_services(clock=clock)
    await _user(s, ADMIN_TG)
    prop = await s.properties.create_property(ADMIN_TG, _flat())
    await s.properties.upload_document(ADMIN_TG, prop.id, "a.pdf", PDF)
    out = await s.properties.submit_ownership(ADMIN_TG, prop.id)
    assert out.verification is not None
    with pytest.raises(PermissionDenied):
        await s.properties.approve(ADMIN_TG, out.verification.id, "Олег Тестовий")
    # У dev (allow_self_review) — можна, щоб пройти весь шлях самому.
    s2, _ = make_fake_services(
        clock=clock, properties_config=PropertiesConfig(allow_self_review=True)
    )
    await _user(s2, ADMIN_TG)
    prop2 = await s2.properties.create_property(ADMIN_TG, _flat())
    await s2.properties.upload_document(ADMIN_TG, prop2.id, "a.pdf", PDF)
    out2 = await s2.properties.submit_ownership(ADMIN_TG, prop2.id)
    assert out2.verification is not None
    await s2.properties.approve(ADMIN_TG, out2.verification.id, "Олег Тестовий")


async def test_dev_bypass(clock: FixedClock) -> None:
    """FR-PRP-5: лише коли прапорець увімкнено (а config.py пускає його лише в dev)."""
    s, _ = make_fake_services(clock=clock)
    await _user(s, OWNER)
    prop = await s.properties.create_property(OWNER, _flat())
    with pytest.raises(PermissionDenied):
        await s.properties.dev_verify(OWNER, prop.id)

    s2, db2 = make_fake_services(
        clock=clock, properties_config=PropertiesConfig(dev_skip_ownership_check=True)
    )
    await _user(s2, OWNER)
    prop2 = await s2.properties.create_property(OWNER, _flat())
    assert (await s2.properties.ownership(OWNER, prop2.id)).dev_bypass_available
    out = await s2.properties.dev_verify(OWNER, prop2.id)
    assert out.ownership_status is OwnershipStatus.VERIFIED
    assert out.verification is not None and out.verification.is_dev_bypass
    assert db2.audit[-1]["action"] == "ownership.dev_bypass"


async def test_send_documents_to_moderator_chat(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, verification = await _submitted(s)
    assert await s.properties.send_documents_to_chat(ADMIN_TG, verification.id) == 1
    assert db.notifier.documents == [(ADMIN_TG, "довідка.pdf", PDF)]
    with pytest.raises(NotStaff):
        await s.properties.send_documents_to_chat(OWNER, verification.id)


async def test_purge_documents_after_retention(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    _, verification = await _submitted(s)
    await s.properties.approve(ADMIN_TG, verification.id, "Тарас Шевченко")
    assert await s.properties.purge_documents() == 0
    clock.advance(timedelta(days=31))
    assert await s.properties.purge_documents() == 1
    assert db.storage.files == {}
    case = await s.properties.verification_case(ADMIN_TG, verification.id)
    assert case.verification.documents[0].deleted


async def test_delete_property_removes_files(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop = await s.properties.create_property(OWNER, _flat())
    await s.properties.upload_document(OWNER, prop.id, "a.pdf", PDF)
    await s.properties.delete_property(OWNER, prop.id)
    assert db.storage.files == {}
    assert await s.properties.list_mine(OWNER) == []
