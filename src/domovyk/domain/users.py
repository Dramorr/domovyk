"""Доменні правила акаунтів: ролі, статуси, ім'я (FR-USR-1, FR-USR-4)."""

from __future__ import annotations

import re
from enum import StrEnum

from domovyk.core.errors import InvalidInput, NotAdmin, ProtectedRole


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
