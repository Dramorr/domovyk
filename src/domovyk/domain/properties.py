"""Доменні правила об'єктів і власності (FR-PRP-1..5). Чиста логіка без I/O."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from domovyk.core.errors import InvalidInput
from domovyk.domain.state_machine import TransitionTable


class PropertyKind(StrEnum):
    APARTMENT = "apartment"
    HOUSE = "house"


class UnitKind(StrEnum):
    ENTIRE = "entire"
    ROOM = "room"


class OwnershipStatus(StrEnum):
    """Статус власності на об'єкті (SPEC §6: unverified → pending → verified / rejected)."""

    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class VerificationStatus(StrEnum):
    """Заявка на перевірку. draft — власник ще додає документи, модератор її не бачить."""

    DRAFT = "draft"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"


OWNERSHIP = TransitionTable[OwnershipStatus](
    "ownership",
    {
        OwnershipStatus.UNVERIFIED: frozenset({OwnershipStatus.PENDING, OwnershipStatus.VERIFIED}),
        OwnershipStatus.PENDING: frozenset({OwnershipStatus.VERIFIED, OwnershipStatus.REJECTED}),
        OwnershipStatus.REJECTED: frozenset({OwnershipStatus.PENDING, OwnershipStatus.VERIFIED}),
        OwnershipStatus.VERIFIED: frozenset(),
    },
)
# UNVERIFIED/REJECTED → VERIFIED напряму — лише dev-обхід (FR-PRP-5).

VERIFICATION = TransitionTable[VerificationStatus](
    "verification",
    {
        VerificationStatus.DRAFT: frozenset({VerificationStatus.PENDING}),
        VerificationStatus.PENDING: frozenset(
            {VerificationStatus.VERIFIED, VerificationStatus.REJECTED}
        ),
        VerificationStatus.VERIFIED: frozenset({VerificationStatus.EXPIRED}),
        VerificationStatus.REJECTED: frozenset(),
        VerificationStatus.EXPIRED: frozenset(),
    },
)


def can_collect_documents(status: OwnershipStatus) -> bool:
    """Нові документи можна додавати, поки власність не на перевірці й не підтверджена."""
    return status in (OwnershipStatus.UNVERIFIED, OwnershipStatus.REJECTED)


# ---------------------------------------------------------------- адреси (FR-PRP-2)

DEFAULT_CITY = "Львів"

# Типи вулиць, які люди пишуть по-різному або взагалі не пишуть.
# Типи вулиць, які люди пишуть по-різному (або взагалі не пишуть) → канонічна форма.
# Тип лишається в ключі: «вулиця Шевченка, 12» і «проспект Шевченка, 12» — різні будинки.
_STREET_TYPES: dict[str, str] = {
    "вулиця": "вул",
    "вул": "вул",
    "проспект": "просп",
    "просп": "просп",
    "пр-т": "просп",
    "площа": "пл",
    "пл": "пл",
    "бульвар": "бульв",
    "бульв": "бульв",
    "б-р": "бульв",
    "провулок": "пров",
    "пров": "пров",
    "узвіз": "узвіз",
    "тупик": "тупик",
    "шосе": "шосе",
    "набережна": "наб",
    "наб": "наб",
    "майдан": "майдан",
}
DEFAULT_STREET_TYPE = "вул"
_TYPE_RE = re.compile(
    r"(?<![\w'])(" + "|".join(re.escape(t) for t in _STREET_TYPES) + r")(?![\w'])\.?",
    re.IGNORECASE,
)
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "´": "'", "‘": "'"})
_SPACES = re.compile(r"\s+")
_HOUSE_RE = re.compile(r"^\d{1,4}(?:[а-яіїєґa-z])?(?:/\d{1,4}(?:[а-яіїєґa-z])?)?$")
_APARTMENT_RE = re.compile(r"^[0-9а-яіїєґa-z/-]{1,10}$")
_APARTMENT_PREFIX = re.compile(r"^(?:кв(?:артира)?\.?|№)\s*", re.IGNORECASE)
_HOUSE_PREFIX = re.compile(r"^(?:буд(?:инок)?\.?|№)\s*", re.IGNORECASE)


def _base(text: str) -> str:
    return _SPACES.sub(" ", text.translate(_APOSTROPHES)).strip()


def clean_street(street: str) -> str:
    """Назва вулиці для показу: без зайвих пробілів, з однаковим апострофом."""
    value = _base(street).strip(" ,.")
    if not 2 <= len(value) <= 100:
        raise InvalidInput("Некоректна назва вулиці", field="street")
    return value


def street_key(street: str) -> str:
    """Ключ вулиці для пошуку дублів: «тип назва» без регістру й крапок.

    «вул. Шевченка», «Шевченка вулиця», «Шевченка» → «вул шевченка»;
    «просп. Свободи», «проспект Свободи» → «просп свободи».
    """
    value = _base(street).lower()
    found = _TYPE_RE.search(value)
    street_type = _STREET_TYPES[found.group(1).lower()] if found else DEFAULT_STREET_TYPE
    name = re.sub(r"[.,]", " ", _TYPE_RE.sub(" ", value))
    return f"{street_type} {_SPACES.sub(' ', name).strip()}"


def normalize_house_number(raw: str) -> str:
    """«12 А», «буд. 12а», «12-а» → «12а»; «5/1» лишається."""
    value = _HOUSE_PREFIX.sub("", _base(raw).lower())
    # «12-а» → «12а», але «12-14» не склеюємо в «1214» — такий номер відхиляємо.
    value = re.sub(r"-(?=[а-яіїєґa-z])", "", value.replace(" ", ""))
    if not _HOUSE_RE.fullmatch(value):
        raise InvalidInput("Некоректний номер будинку", field="house_number")
    return value


def normalize_apartment(raw: str | None) -> str | None:
    """«кв. 5», «5 » → «5». Порожнє — None."""
    if raw is None:
        return None
    value = _APARTMENT_PREFIX.sub("", _base(raw).lower()).replace(" ", "")
    if not value:
        return None
    if not _APARTMENT_RE.fullmatch(value):
        raise InvalidInput("Некоректний номер квартири", field="apartment")
    return value


def building_key(city: str, street: str, house_number: str) -> str:
    """Ключ будинку: 'львів|тараса шевченка|12а'. Однаковий для різного написання адреси."""
    return "|".join((_base(city).lower(), street_key(street), normalize_house_number(house_number)))


# ---------------------------------------------------------------- параметри об'єкта

ROOMS_MAX = 30  # 0 = студія; як CHECK у БД
AREA_MIN, AREA_MAX = Decimal(5), Decimal(1000)  # BOT_FLOWS PRP-NEW, крок 6
FLOOR_MIN, FLOOR_MAX = -3, 100
ROOM_NAME_MAX = 60


def validate_rooms(rooms: int) -> int:
    if not 0 <= rooms <= ROOMS_MAX:
        raise InvalidInput("Некоректна кількість кімнат", field="rooms")
    return rooms


def validate_area(area: Decimal | float | str | None, field: str = "area") -> Decimal | None:
    if area is None:
        return None
    try:
        value = Decimal(str(area)).quantize(Decimal("0.1"))
    except InvalidOperation as exc:
        raise InvalidInput("Некоректна площа", field=field) from exc
    if not AREA_MIN <= value <= AREA_MAX:
        raise InvalidInput("Площа має бути від 5 до 1000 м²", field=field)
    return value


def validate_floor(floor: int | None) -> int | None:
    if floor is not None and not FLOOR_MIN <= floor <= FLOOR_MAX:
        raise InvalidInput("Некоректний поверх", field="floor")
    return floor


def clean_short(value: str | None, field: str, max_len: int = 10) -> str | None:
    if value is None:
        return None
    value = _base(value)
    if not value:
        return None
    if len(value) > max_len:
        raise InvalidInput("Задовге значення", field=field)
    return value


def clean_room_name(name: str) -> str:
    value = _base(name)
    if not 1 <= len(value) <= ROOM_NAME_MAX:
        raise InvalidInput("Назва кімнати — до 60 символів", field="name")
    return value


def clean_owner_name(name: str) -> str:
    """ПІБ власника(ів) з довідки — для звірки з підписом (FR-PRP-4, п. 3)."""
    value = _base(name)
    if not 3 <= len(value) <= 300:
        raise InvalidInput("Впиши ПІБ власника з довідки", field="owner_name")
    return value


def clean_reason(reason: str) -> str:
    value = _base(reason)
    if not 2 <= len(value) <= 500:
        raise InvalidInput("Потрібна причина", field="reason")
    return value
