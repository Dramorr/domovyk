"""DTO модуля notifications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from domovyk.adapters.notifier.base import Button


class NotificationType(StrEnum):
    """Типи сповіщень (каталог — BOT_FLOWS §18). Текст для кожного — в інтерфейсі бота."""

    MODERATION_QUEUE = "moderation_queue"
    MODERATION_ALERT = "moderation_alert"  # підозріла дія — перевірити одразу
    OWNERSHIP_VERIFIED = "ownership_verified"
    OWNERSHIP_REJECTED = "ownership_rejected"
    CHAT_MESSAGE = "chat_message"
    LEASE_UPDATE = "lease_update"  # приєднався орендар, погоджено умови, прохання змін тощо
    PAYMENT = "payment"  # нагадування про оплату, «оплатив», підтвердження (M5)
    METER_REMINDER = "meter_reminder"  # час подати показники лічильників (FR-UTL-2)


# Типи, які людина може вимкнути для своєї оренди (NTF-LEASE, FR-NTF-1).
LEASE_OPTIONAL_TYPES = (
    NotificationType.CHAT_MESSAGE,
    NotificationType.LEASE_UPDATE,
    NotificationType.METER_REMINDER,
)
QUIET_HOURS_DEFAULT = (22, 8)


@dataclass(frozen=True)
class PendingNotification:
    id: int
    user_id: uuid.UUID
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    buttons: list[Button] = field(default_factory=list)


class LeaseNotificationPrefs(BaseModel):
    """Налаштування сповіщень однієї людини для однієї оренди."""

    chat_message: bool = True
    lease_update: bool = True
    meter_reminder: bool = True
    quiet_from: int = Field(default=QUIET_HOURS_DEFAULT[0], ge=0, le=23)
    quiet_to: int = Field(default=QUIET_HOURS_DEFAULT[1], ge=0, le=23)
