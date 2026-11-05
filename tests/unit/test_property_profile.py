"""Анкета об'єкта й рівень заповненості (FR-PRP-8)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import InvalidInput, NotFound
from domovyk.domain.properties import PropertyKind
from domovyk.domain.property_profile import (
    DESCRIPTION_MIN,
    FIELDS,
    CompletenessLevel,
    completeness,
    is_answered,
)
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate, PropertyDetails
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services

OWNER, OTHER = 42, 43
LEVELS = {"medium_from": 40, "high_from": 75}

FULL = PropertyDetails(
    description="Світла квартира в тихому дворі, вікна на парк. Поруч зупинка, школа й ринок. "
    "Сусіди спокійні, будинок доглянутий.",
    building_type="czech",
    wall_material="panel",
    year_built=1985,
    floors_total=9,
    has_elevator=True,
    parking="yard",
    shelter="basement",
    building_features=[],
    area_living=Decimal("32"),
    area_kitchen=Decimal("9"),
    layout="separate",
    bathroom="separate",
    ceiling_height=Decimal("2.6"),
    balconies=1,
    windows_view="yard",
    repair="cosmetic",
    first_rental=False,
    heating="central",
    hot_water="central",
    stove="gas",
    internet="fiber",
    meters=["electricity", "water", "gas"],
    utilities_winter_uah=3200,
    utilities_summer_uah=1100,
    apartment_backup_power="none",
    blackout_ready=["water"],
    furnishing="full",
    appliances=["fridge", "washing_machine"],
    comfort=[],
    accessibility=[],
    pets="cats",
    children="yes",
    smoking="no",
    max_occupants=3,
    residence_registration=False,
)


def test_answered_semantics() -> None:
    assert not is_answered("heating", None)
    assert is_answered("has_elevator", False)  # «ні» — теж відповідь
    assert is_answered("comfort", [])  # «нічого з переліку»
    assert is_answered("utilities_summer_uah", 0)
    assert not is_answered("description", "Здам квартиру")
    assert is_answered("description", "x" * DESCRIPTION_MIN)


def test_completeness_levels_and_important_first() -> None:
    empty = completeness({"rooms": 2}, **LEVELS)
    assert empty.level is CompletenessLevel.LOW
    assert "heating" in empty.missing_important
    assert "utilities_winter_uah" in empty.missing_important
    assert "rooms" not in empty.missing_important

    full = FULL.model_dump()
    full.update(rooms=2, area_total=Decimal(54), floor=3)
    result = completeness(full, **LEVELS)
    assert (result.percent, result.level, result.missing_important) == (
        100,
        CompletenessLevel.HIGH,
        (),
    )
    assert result.answered == result.total == len(FIELDS)
    assert set(result.sections.values()) == {100}


def test_important_questions_weigh_more() -> None:
    """Опалення й комуналка взимку важать більше за вид з вікна й рік побудови."""
    important = completeness({"heating": "central", "utilities_winter_uah": 3000}, **LEVELS)
    minor = completeness({"windows_view": "yard", "year_built": 1985}, **LEVELS)
    assert important.percent > minor.percent


def test_details_validation() -> None:
    with pytest.raises(ValidationError):
        PropertyDetails(heating="firewood")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PropertyDetails(year_built=1500)
    d = PropertyDetails(appliances=["fridge", "fridge", "tv"])  # type: ignore[list-item]
    assert d.appliances == ["fridge", "tv"]


@pytest.fixture
async def env() -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=FixedClock(datetime(2026, 11, 6, 12, tzinfo=UTC)))
    for tg in (OWNER, OTHER):
        await s.users.register(tg, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _flat(s: Services) -> object:
    return await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="Зелена", house_number="40"),
            apartment="5",
            floor=3,
            rooms=2,
            area=Decimal("54"),
        ),
    )


async def test_profile_saved_and_scored(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop = await _flat(s)
    assert prop.completeness is not None  # type: ignore[attr-defined]
    assert prop.completeness.level is CompletenessLevel.LOW  # type: ignore[attr-defined]

    updated = await s.properties.update_profile(OWNER, prop.id, FULL)  # type: ignore[attr-defined]
    assert updated.completeness is not None
    assert updated.completeness.percent == 100
    assert updated.details.heating == "central"
    stored = next(iter(db.prop.properties.values())).details
    assert stored["v"] == 1 and "smoking" in stored
    listed = await s.properties.list_mine(OWNER)
    assert listed[0].completeness is not None and listed[0].completeness.percent == 100


async def test_profile_consistency_checks(env: tuple[Services, FakeDb]) -> None:
    s, _ = env
    prop = await _flat(s)
    pid = prop.id  # type: ignore[attr-defined]
    with pytest.raises(InvalidInput) as exc:
        await s.properties.update_profile(OWNER, pid, PropertyDetails(area_living=Decimal(80)))
    assert exc.value.details["field"] == "area_living"
    with pytest.raises(InvalidInput):
        await s.properties.update_profile(OWNER, pid, PropertyDetails(floors_total=2))
    with pytest.raises(NotFound):
        await s.properties.update_profile(OTHER, pid, FULL)
