"""Доменні правила адрес і власності (FR-PRP-2, FR-PRP-4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from domovyk.core.errors import InvalidInput, InvalidTransition
from domovyk.domain.properties import (
    OWNERSHIP,
    VERIFICATION,
    OwnershipStatus,
    VerificationStatus,
    building_key,
    can_collect_documents,
    normalize_apartment,
    normalize_house_number,
    street_key,
    validate_area,
)


@pytest.mark.parametrize(
    "street",
    ["вул. Шевченка", "вулиця Шевченка", "Шевченка", "  ШЕВЧЕНКА  ", "Шевченка вул."],
)
def test_street_key_ignores_type_spelling_and_case(street: str) -> None:
    assert street_key(street) == "вул шевченка"


def test_street_key_keeps_type_full_name_and_unifies_apostrophe() -> None:
    assert street_key("просп. Свободи") == street_key("проспект Свободи") == "просп свободи"
    assert street_key("Городоцька вулиця") == "вул городоцька"
    assert street_key("вул. Січових Стрільців") == "вул січових стрільців"
    assert street_key("Об’їзна") == street_key("Об'їзна") == "вул об'їзна"
    # Слово «вул» усередині назви не зачіпаємо.
    assert street_key("Вулецька") == "вул вулецька"
    # Вулиця й проспект з однаковою назвою — різні (так буває у Львові).
    assert street_key("вулиця Тараса Шевченка") != street_key("проспект Тараса Шевченка")


@pytest.mark.parametrize(
    ("raw", "norm"),
    [("12", "12"), ("12 А", "12а"), ("буд. 12а", "12а"), ("12-а", "12а"), ("5/1", "5/1")],
)
def test_house_number(raw: str, norm: str) -> None:
    assert normalize_house_number(raw) == norm


@pytest.mark.parametrize("raw", ["", "абв", "12345", "1;DROP", "12-14", "12;14"])
def test_bad_house_number(raw: str) -> None:
    with pytest.raises(InvalidInput):
        normalize_house_number(raw)


def test_apartment() -> None:
    assert normalize_apartment("кв. 5") == "5"
    assert normalize_apartment(" 12Б ") == "12б"
    assert normalize_apartment("  ") is None
    assert normalize_apartment(None) is None
    with pytest.raises(InvalidInput):
        normalize_apartment("квартира номер п'ять")


def test_building_key_same_for_different_spelling() -> None:
    a = building_key("lviv", "вул. Шевченка", "12 А")
    b = building_key("LVIV", "Шевченка", "12а")
    assert a == b == "lviv|вул шевченка|12а"


def test_area() -> None:
    assert validate_area("54.25") == Decimal("54.2")
    assert validate_area(None) is None
    with pytest.raises(InvalidInput):
        validate_area(2)
    with pytest.raises(InvalidInput):
        validate_area("багато")


def test_ownership_transitions() -> None:
    assert OWNERSHIP.can(OwnershipStatus.UNVERIFIED, OwnershipStatus.PENDING)
    assert OWNERSHIP.can(OwnershipStatus.REJECTED, OwnershipStatus.PENDING)
    with pytest.raises(InvalidTransition):
        OWNERSHIP.transition(OwnershipStatus.VERIFIED, OwnershipStatus.PENDING)
    with pytest.raises(InvalidTransition):
        VERIFICATION.transition(VerificationStatus.DRAFT, VerificationStatus.VERIFIED)
    assert can_collect_documents(OwnershipStatus.REJECTED)
    assert not can_collect_documents(OwnershipStatus.PENDING)
