"""DTO модуля notifications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from domovyk.adapters.notifier.base import Button


class NotificationType(StrEnum):
    """Типи сповіщень (каталог — BOT_FLOWS §18). Текст для кожного — в інтерфейсі бота."""

    MODERATION_QUEUE = "moderation_queue"
    OWNERSHIP_VERIFIED = "ownership_verified"
    OWNERSHIP_REJECTED = "ownership_rejected"


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
