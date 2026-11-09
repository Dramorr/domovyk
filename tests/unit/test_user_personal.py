"""Персональні дані й аватар (ADR 0016). Лише вигадані дані й згенеровані картинки."""

from __future__ import annotations

import io
import uuid
from datetime import date

import pytest
from PIL import Image

from domovyk.core.errors import InvalidInput, NotFound, TooYoung
from domovyk.domain.users import age_on, check_birth_date
from domovyk.modules.users.schemas import PersonalIn
from tests.fakes import PRIVACY_V, TERMS_V, make_fake_services

ME, OTHER = 51, 52


def picture() -> bytes:
    img = Image.new("RGB", (800, 800), (30, 77, 59))
    img.paste((242, 169, 59), (0, 0, 400, 300))
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


async def _users():  # type: ignore[no-untyped-def]
    s, db = make_fake_services()
    for tg, name in ((ME, "Тарас"), (OTHER, "Ірина")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
    return s, db


def test_age_counts_full_years() -> None:
    assert age_on(date(2008, 11, 5), date(2026, 11, 4)) == 17
    assert age_on(date(2008, 11, 4), date(2026, 11, 4)) == 18
    with pytest.raises(TooYoung):
        check_birth_date(date(2010, 1, 1), date(2026, 11, 4), 18)
    with pytest.raises(InvalidInput):
        check_birth_date(date(2027, 1, 1), date(2026, 11, 4), 18)
    with pytest.raises(InvalidInput):
        check_birth_date(date(1899, 12, 31), date(2026, 11, 4), 18)


async def test_personal_data_saved_and_only_for_owner() -> None:
    s, _ = await _users()
    await s.users.add_phone(ME, "+380 67 000 00 01")
    out = await s.users.update_personal(
        ME,
        PersonalIn(
            legal_name="  Шевченко   Тарас Григорович ",
            birth_date=date(1990, 3, 9),
            city_code="lviv",
        ),
    )
    assert out.legal_name == "Шевченко Тарас Григорович"
    assert out.birth_date == date(1990, 3, 9)
    assert out.city_code == "lviv"
    assert out.phone == "+380670000001"
    # Чужі персональні дані через цей метод не дістати — лише свої.
    assert (await s.users.personal(OTHER)).legal_name is None


async def test_personal_validation() -> None:
    s, _ = await _users()
    with pytest.raises(TooYoung):
        await s.users.update_personal(ME, PersonalIn(birth_date=date(2012, 5, 1)))
    with pytest.raises(InvalidInput) as e:
        await s.users.update_personal(ME, PersonalIn(legal_name="Тарас"))  # одне слово
    assert e.value.details["field"] == "legal_name"
    with pytest.raises(InvalidInput) as e:
        await s.users.update_personal(ME, PersonalIn(city_code="atlantis"))
    assert e.value.details["field"] == "city_code"
    # Порожні поля — це «не вказано», а не помилка.
    out = await s.users.update_personal(ME, PersonalIn(legal_name="   ", city_code=""))
    assert out.legal_name is None and out.city_code is None


async def test_avatar_upload_replace_view_and_remove() -> None:
    s, db = await _users()
    me = await s.users.set_avatar(ME, picture())
    assert me.avatar_version is not None
    other = await s.users.find_by_telegram(OTHER)
    assert other is not None and other.avatar_version is None
    # Інший користувач бачить аватар.
    data = await s.users.avatar(OTHER, me.id)
    assert data[:2] == b"\xff\xd8"  # JPEG
    first_keys = set(db.storage.files)
    await s.users.set_avatar(ME, picture())
    assert len(db.storage.files) == len(first_keys)  # старий файл видалено
    await s.users.remove_avatar(ME)
    assert not db.storage.files
    with pytest.raises(NotFound):
        await s.users.avatar(OTHER, me.id)
    with pytest.raises(NotFound):
        await s.users.avatar(OTHER, uuid.uuid4())


async def test_avatar_rejects_non_image() -> None:
    s, _ = await _users()
    with pytest.raises(InvalidInput) as e:
        await s.users.set_avatar(ME, b"not an image")
    assert e.value.details["field"] == "file"


async def test_delete_account_clears_personal_and_avatar() -> None:
    s, db = await _users()
    await s.users.update_personal(
        ME, PersonalIn(legal_name="Шевченко Тарас", birth_date=date(1990, 3, 9), city_code="lviv")
    )
    me = await s.users.set_avatar(ME, picture())
    await s.users.delete_account(ME)
    user = db.users[me.id]
    assert user.personal == PersonalIn()
    assert user.avatar_key is None
    assert not db.storage.files


async def test_register_with_birth_date_checks_age() -> None:
    s, _ = make_fake_services()
    with pytest.raises(TooYoung):
        await s.users.register(
            61,
            "Тарас",
            terms_version=TERMS_V,
            privacy_version=PRIVACY_V,
            birth_date=date(2012, 1, 1),
        )
    assert await s.users.find_by_telegram(61) is None  # акаунт не створено
    await s.users.register(
        61, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V, birth_date=date(1999, 2, 3)
    )
    assert (await s.users.personal(61)).birth_date == date(1999, 2, 3)
