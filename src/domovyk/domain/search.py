"""Доменні правила пошуку (FR-SRC-1..6). Чиста логіка без I/O.

Фільтр перевіряє оголошення разом з анкетою об'єкта (ADR 0018): правила й зручності
живуть в анкеті, тож «з тваринами» чи «є посудомийка» — це питання до неї.
Невідповідь в анкеті («ще не відповів») фільтр не проходить: орендар просив певності.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from domovyk.core.errors import InvalidInput
from domovyk.domain.properties import PropertyKind

ROOMS_PLUS = 4  # «4+» у фільтрі
COMPLETENESS_MAX = 100
PAGE_SIZE = 20


class Amenity(StrEnum):
    """Зручності для фільтра — найчастіші запитання орендарів, а не весь перелік анкети."""

    FURNISHED = "furnished"
    WASHING_MACHINE = "washing_machine"
    DISHWASHER = "dishwasher"
    AIR_CONDITIONER = "air_conditioner"
    INTERNET = "internet"
    BACKUP_POWER = "backup_power"  # своя автономність квартири
    SHELTER = "shelter"
    ELEVATOR = "elevator"
    PARKING = "parking"


def _has(key: str, value: str) -> Callable[[Mapping[str, object]], bool]:
    def check(d: Mapping[str, object]) -> bool:
        items = d.get(key)
        return isinstance(items, list) and value in items

    return check


def _not_none(key: str) -> Callable[[Mapping[str, object]], bool]:
    return lambda d: d.get(key) not in (None, "none")


AMENITY_CHECKS: dict[Amenity, Callable[[Mapping[str, object]], bool]] = {
    Amenity.FURNISHED: lambda d: d.get("furnishing") in ("partial", "full"),
    Amenity.WASHING_MACHINE: _has("appliances", "washing_machine"),
    Amenity.DISHWASHER: _has("appliances", "dishwasher"),
    Amenity.AIR_CONDITIONER: _has("comfort", "air_conditioner"),
    Amenity.INTERNET: _not_none("internet"),
    Amenity.BACKUP_POWER: _not_none("apartment_backup_power"),
    Amenity.SHELTER: _not_none("shelter"),
    Amenity.ELEVATOR: lambda d: d.get("has_elevator") is True,
    Amenity.PARKING: lambda d: d.get("parking") in ("yard", "underground", "garage"),
}

PETS_OK = frozenset({"cats", "small_dogs", "any", "discuss"})
CHILDREN_OK = frozenset({"yes", "discuss"})


@dataclass(frozen=True)
class Criteria:
    """Фільтр орендаря після перевірки меж."""

    kinds: frozenset[PropertyKind] = frozenset()
    districts: frozenset[str] = frozenset()  # назви районів
    rooms: frozenset[int] = frozenset()  # 0 — студія, 4 — «4+»
    price_min: int | None = None
    price_max: int | None = None
    area_min: float | None = None
    floor_not_first: bool = False
    floor_not_last: bool = False
    pets: bool = False
    children: bool = False
    registration: bool = False
    amenities: frozenset[Amenity] = frozenset()
    completeness_min: int = 0


@dataclass(frozen=True)
class Candidate:
    """Те, що фільтр знає про оголошення й об'єкт. Поверх і адреса — лише для перевірки."""

    kind: PropertyKind
    district: str | None
    rooms: int
    area: float | None
    floor: int | None
    price: int
    completeness: int
    details: Mapping[str, object]


def clean_criteria(
    *,
    kinds: list[PropertyKind],
    districts: list[str],
    rooms: list[int],
    price_min: int | None,
    price_max: int | None,
    area_min: float | None,
    floor_not_first: bool,
    floor_not_last: bool,
    pets: bool,
    children: bool,
    registration: bool,
    amenities: list[Amenity],
    completeness_min: int,
    completeness_floor: int,
) -> Criteria:
    """Межі фільтра. Повзунок заповненості — справжні відсотки від мінімуму (FR-SRC-6)."""
    if any(r < 0 or r > ROOMS_PLUS for r in rooms):
        raise InvalidInput("Кімнатність поза межами", field="rooms")
    for name, value in (("price_min", price_min), ("price_max", price_max)):
        if value is not None and not 0 <= value <= 1_000_000:
            raise InvalidInput("Ціна поза межами", field=name)
    if price_min is not None and price_max is not None and price_min > price_max:
        raise InvalidInput("Мінімальна ціна більша за максимальну", field="price_min")
    if area_min is not None and not 0 <= area_min <= 1000:
        raise InvalidInput("Площа поза межами", field="area_min")
    if not 0 <= completeness_min <= COMPLETENESS_MAX:
        raise InvalidInput("Заповненість поза межами", field="completeness_min")
    return Criteria(
        kinds=frozenset(kinds),
        districts=frozenset(districts),
        rooms=frozenset(rooms),
        price_min=price_min or None,
        price_max=price_max or None,
        area_min=area_min or None,
        floor_not_first=floor_not_first,
        floor_not_last=floor_not_last,
        pets=pets,
        children=children,
        registration=registration,
        amenities=frozenset(amenities),
        completeness_min=max(completeness_min, completeness_floor),
    )


def matches(c: Criteria, x: Candidate) -> bool:
    d = x.details
    checks = (
        not c.kinds or x.kind in c.kinds,
        not c.districts or x.district in c.districts,
        not c.rooms or min(x.rooms, ROOMS_PLUS) in c.rooms,
        c.price_min is None or x.price >= c.price_min,
        c.price_max is None or x.price <= c.price_max,
        c.area_min is None or (x.area is not None and x.area >= c.area_min),
        not c.floor_not_first or (x.floor is not None and x.floor > 1),
        not c.floor_not_last or _not_last(x.floor, d.get("floors_total")),
        not c.pets or d.get("pets") in PETS_OK,
        not c.children or d.get("children") in CHILDREN_OK,
        not c.registration or d.get("residence_registration") is True,
        all(AMENITY_CHECKS[a](d) for a in c.amenities),
        x.completeness >= c.completeness_min,
    )
    return all(checks)


def _not_last(floor: int | None, floors_total: object) -> bool:
    if floor is None or not isinstance(floors_total, int):
        return False
    return floor < floors_total
