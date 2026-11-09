"""Доменні правила акаунтів: ролі, статуси, ім'я (FR-USR-1, FR-USR-4)."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum

from domovyk.core.errors import InvalidInput, NotAdmin, ProtectedRole, TooYoung


class UserRole(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    RESTRICTED = "restricted"
    BANNED = "banned"
    DELETED = "deleted"


class IdentityProvider(StrEnum):
    TELEGRAM = "telegram"
    PHONE = "phone"
    EMAIL = "email"
    BANKID = "bankid"
    DIIA = "diia"


class AppMode(StrEnum):
    """Що людина бачить за замовчуванням (ADR 0006). Не роль: ролі — в конкретній оренді."""

    TENANT = "tenant"  # шукаю житло
    LANDLORD = "landlord"  # здаю житло


class ConsentDocument(StrEnum):
    TERMS = "terms"
    PRIVACY = "privacy"


# Ім'я: 2–40 символів, літери (будь-якої абетки), пробіл, апостроф, дефіс (BOT_FLOWS ONB-2).
_NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ '’ʼ-])*$")
NAME_MIN, NAME_MAX = 2, 40


def normalize_display_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not (NAME_MIN <= len(name) <= NAME_MAX) or not _NAME_RE.fullmatch(name):
        raise InvalidInput("Некоректне ім'я", field="display_name")
    return name


BIRTH_MIN = date(1900, 1, 1)
LEGAL_NAME_MIN, LEGAL_NAME_MAX = 3, 150


def age_on(birth: date, today: date) -> int:
    """Повних років на дату `today`."""
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def check_birth_date(birth: date, today: date, min_age: int) -> None:
    """Дата реальна й людині щонайменше `min_age` (повна дієздатність — 18, ЦКУ ст. 34)."""
    if birth < BIRTH_MIN or birth > today:
        raise InvalidInput("Некоректна дата народження", field="birth_date")
    if age_on(birth, today) < min_age:
        raise TooYoung(f"Орендувати й здавати житло можна з {min_age} років", field="birth_date")


def normalize_legal_name(raw: str) -> str:
    """ПІБ як у документах: літери, пробіли, апостроф, дефіс; щонайменше два слова."""
    name = " ".join(raw.split())
    if (
        not (LEGAL_NAME_MIN <= len(name) <= LEGAL_NAME_MAX)
        or not _NAME_RE.fullmatch(name)
        or len(name.split()) < 2
    ):
        raise InvalidInput("Некоректне ПІБ", field="legal_name")
    return name


def is_staff(role: UserRole) -> bool:
    return role in (UserRole.MODERATOR, UserRole.ADMIN)


def require_admin(role: UserRole) -> None:
    if role is not UserRole.ADMIN:
        raise NotAdmin("Лише для адміністратора")


def new_role_for_moderator_change(target: UserRole, *, grant: bool) -> UserRole:
    """Адмін видає/забирає роль модератора. Роль іншого адміна так не змінюється."""
    if target is UserRole.ADMIN:
        raise ProtectedRole("Роль адміністратора не змінюється цією дією")
    return UserRole.MODERATOR if grant else UserRole.USER


def can_use_app(status: UserStatus) -> bool:
    return status not in (UserStatus.BANNED, UserStatus.DELETED)
