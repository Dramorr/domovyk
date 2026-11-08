"""Фото об'єкта (FR-PRP-9, ADR 0011). Зображення генеруються в тесті — жодних справжніх фото."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime

import pytest
from PIL import Image

from domovyk.adapters.images.base import ImageRejected
from domovyk.adapters.images.pillow import PillowImageProcessor
from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import InvalidInput, NotFound
from domovyk.domain.leases import PayMethod
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.modules.leases.schemas import LeaseCreate, PayDetailsIn
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from domovyk.modules.properties.service import PropertiesConfig
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services

OWNER, TENANT, STRANGER = 42, 43, 44


def picture(w: int = 1200, h: int = 900, fmt: str = "JPEG", exif: bool = False) -> bytes:
    img = Image.new("RGB", (w, h), (30, 77, 59))
    img.paste((242, 169, 59), (0, 0, w // 2, h // 3))  # щоб pHash мав що рахувати
    out = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x0112] = 6  # Orientation: повернути на 90°
        e[0x010F] = "Тестовий телефон"  # Make
        img.save(out, fmt, exif=e.tobytes())
    else:
        img.save(out, fmt)
    return out.getvalue()


# ----------------------------------------------------------------- адаптер


async def test_prepare_strips_exif_rotates_and_shrinks() -> None:
    images = PillowImageProcessor(max_side=1000)
    result = await images.prepare(picture(2000, 1000, exif=True))
    assert (result.width, result.height) == (500, 1000)  # повернуто й зменшено
    out = Image.open(io.BytesIO(result.data))
    assert out.format == "JPEG"
    assert not out.getexif()  # метадані не переносимо
    png = await images.prepare(picture(800, 600, fmt="PNG"))
    assert (png.width, png.height) == (800, 600)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"%PDF-1.4 not a photo", "bad_type"),
        (b"\xff\xd8\xff broken jpeg", "bad_type"),
        (picture(300, 300), "too_small"),
    ],
)
async def test_prepare_rejects(data: bytes, reason: str) -> None:
    with pytest.raises(ImageRejected) as e:
        await PillowImageProcessor().prepare(data)
    assert e.value.reason == reason


# ----------------------------------------------------------------- сервіс


@pytest.fixture
async def env() -> tuple[Services, FakeDb]:
    s, db = make_fake_services(
        clock=FixedClock(datetime(2026, 11, 6, 10, tzinfo=UTC)),
        properties_config=PropertiesConfig(max_photos=3),
    )
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (STRANGER, "Оля")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _property(s: Services, db: FakeDb) -> uuid.UUID:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Тестова", house_number="7"),
            apartment="3",
            rooms=1,
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    return prop.id


async def test_add_cover_delete(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _property(s, db)
    for _ in range(3):
        photos = await s.properties.add_photo(OWNER, pid, picture())
    assert [p.position for p in photos] == [0, 1, 2]
    with pytest.raises(InvalidInput) as e:
        await s.properties.add_photo(OWNER, pid, picture())
    assert e.value.details["reason"] == "too_many"

    last = photos[2].id
    photos = await s.properties.make_cover(OWNER, pid, last)
    assert photos[0].id == last
    photos = await s.properties.delete_photo(OWNER, pid, photos[1].id)
    assert [p.position for p in photos] == [0, 1] and photos[0].id == last
    assert len(db.storage.files) == 2  # файл видаленого фото прибрано

    data = await s.properties.photo_file(OWNER, pid, last)
    assert Image.open(io.BytesIO(data)).format == "JPEG"
    with pytest.raises(NotFound):  # чужий об'єкт виглядає як неіснуючий
        await s.properties.photos(STRANGER, pid)

    await s.properties.delete_property(OWNER, pid)
    assert not db.prop.photos and not db.storage.files


async def test_rejected_photo_is_invalid_input(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _property(s, db)
    with pytest.raises(InvalidInput) as e:
        await s.properties.add_photo(OWNER, pid, b"MZ executable")
    assert e.value.details["reason"] == "bad_type"
    assert not db.storage.files


async def test_lease_members_see_photos(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _property(s, db)
    photo = (await s.properties.add_photo(OWNER, pid, picture()))[0]
    lease, invite = await s.leases.create_lease(
        OWNER,
        LeaseCreate(
            property_id=pid,
            start_date=date(2026, 12, 1),
            months=11,
            rent_amount=15000,
            rent_due_day=1,
            pay_method=PayMethod.CARD,
            pay_details=PayDetailsIn(card="4111 1111 1111 1111", recipient_name="Ірина Коваль"),
        ),
    )
    await s.leases.accept_invite(TENANT, invite.token)
    assert [p.id for p in await s.leases.photos(TENANT, lease.id)] == [photo.id]
    assert await s.leases.photo(TENANT, lease.id, photo.id)
    with pytest.raises(NotFound):
        await s.leases.photos(STRANGER, lease.id)
    with pytest.raises(NotFound):
        await s.leases.photo(TENANT, lease.id, uuid.uuid4())
