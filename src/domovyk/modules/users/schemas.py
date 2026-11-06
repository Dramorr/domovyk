"""DTO модуля users — те, що повертає й приймає service.py."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from domovyk.domain.users import AppMode, UserRole, UserStatus


class UserOut(BaseModel):
    id: uuid.UUID
    display_name: str
    role: UserRole
    status: UserStatus
    telegram_uid: int | None
    has_phone: bool
    created_at: datetime
    mode: AppMode | None = None  # None — ще не обрав (ONB-4)


class TenantProfile(BaseModel):
    """Профіль орендаря (FR-USR-3). Усе необов'язкове; його бачить власник у заявці."""

    about: str | None = Field(default=None, max_length=1000)
    occupation: str | None = Field(default=None, max_length=100)
    household_size: int | None = Field(default=None, ge=1, le=20)
    has_children: bool | None = None
    has_pets: bool | None = None
    pets_details: str | None = Field(default=None, max_length=300)
    smoker: bool | None = None


class ConsentInfo(BaseModel):
    terms_version: str
    privacy_version: str
    terms_url: str
    privacy_url: str
