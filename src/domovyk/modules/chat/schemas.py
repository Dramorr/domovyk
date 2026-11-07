"""DTO модуля chat."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SystemCode(StrEnum):
    """Системні повідомлення (FR-CHT-2). Текст — у файлі локалізації інтерфейсу."""

    LEASE_CREATED = "lease_created"
    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    TERMS_ACCEPTED = "terms_accepted"
    TERMS_CHANGE_REQUESTED = "terms_change_requested"
    TERMS_DECLINED = "terms_declined"
    TERMS_UPDATED = "terms_updated"
    LEASE_CANCELLED = "lease_cancelled"
    TERMS_PROPOSED = "terms_proposed"  # {name} — орендар запропонував зміни умов
    PROPOSAL_ACCEPTED = "proposal_accepted"  # власник погодив зміни
    PROPOSAL_DECLINED = "proposal_declined"  # власник відхилив зміни
    CONTRACT_READY = "contract_ready"  # M4: договір сформовано
    CONTRACT_SIGNED_BY = "contract_signed_by"  # {name, role}
    CONTRACT_EXTERNAL = "contract_external"  # власник завантажив чинний договір
    CONTRACT_EXTERNAL_REJECTED = "contract_external_rejected"
    LEASE_ACTIVATED = "lease_activated"  # {start}


class MessageOut(BaseModel):
    id: int
    sender_id: uuid.UUID | None  # None — системне повідомлення
    body: str | None
    system_code: SystemCode | None
    system_params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessagesPage(BaseModel):
    messages: list[MessageOut]
    last_read_message_id: int
    has_more: bool
