"""DTO модуля moderation."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class AuditAction(StrEnum):
    ROLE_BOOTSTRAP = "role.bootstrap"
    ROLE_GRANT = "role.grant"
    ROLE_REVOKE = "role.revoke"
    BAN_CREATE = "ban.create"
    BAN_LIFT = "ban.lift"
    ACCOUNT_DELETE = "account.delete"


class BanIdentifiers(BaseModel):
    """Ідентифікатори для бану/перевірки. Телефон і РНОКПП — лише як HMAC-хеші."""

    telegram_uid: str | None = None
    phone_hash: str | None = None
    identity_hash: str | None = None

    def is_empty(self) -> bool:
        return not (self.telegram_uid or self.phone_hash or self.identity_hash)


class BanOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    reason: str
    created_at: datetime


class AuditEntry(BaseModel):
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str
    details: dict[str, Any]
