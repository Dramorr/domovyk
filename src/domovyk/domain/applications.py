"""Доменні правила заявок (FR-APP-1..7). Чиста логіка без I/O.

Заявка відкрита (pending, approved, showing_scheduled, showing_done) — одна на оголошення
від людини. Контакти, квартира й поверх відкриваються лише після схвалення (правило 9).
Покази (M6.5): approved ⇄ showing_scheduled → showing_done | no_show.
"""

from __future__ import annotations

from enum import StrEnum

from domovyk.core.errors import ContactsInText, InvalidInput
from domovyk.domain.property_profile import find_contacts
from domovyk.domain.state_machine import TransitionTable

MESSAGE_MAX = 500


class ApplicationStatus(StrEnum):
    PENDING = "pending"  # чекає рішення власника
    APPROVED = "approved"  # контакти й адреса відкриті, є чат
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"  # орендар відкликав
    EXPIRED = "expired"  # власник мовчав (FR-APP-3)
    SHOWING_SCHEDULED = "showing_scheduled"  # записаний на показ (FR-APP-4)
    SHOWING_DONE = "showing_done"  # показ відбувся (FR-APP-5) — заявка й далі відкрита
    NO_SHOW = "no_show"  # хтось не прийшов — заявку закрито
    LEASE_CREATED = "lease_created"  # з заявки створено оренду (FR-APP-6)


class DeclineReason(StrEnum):
    RENTED = "rented"  # уже здано
    TERMS = "terms"  # не підходять умови
    OTHER = "other"


OPEN = frozenset(
    {
        ApplicationStatus.PENDING,
        ApplicationStatus.APPROVED,
        ApplicationStatus.SHOWING_SCHEDULED,
        ApplicationStatus.SHOWING_DONE,
    }
)
# Після схвалення сторони бачать контакти, адресу й чат.
REVEALED = frozenset(
    {
        ApplicationStatus.APPROVED,
        ApplicationStatus.SHOWING_SCHEDULED,
        ApplicationStatus.SHOWING_DONE,
        ApplicationStatus.LEASE_CREATED,
    }
)

APPLICATION = TransitionTable[ApplicationStatus](
    "application",
    {
        ApplicationStatus.PENDING: frozenset(
            {
                ApplicationStatus.APPROVED,
                ApplicationStatus.DECLINED,
                ApplicationStatus.WITHDRAWN,
                ApplicationStatus.EXPIRED,
            }
        ),
        ApplicationStatus.APPROVED: frozenset(
            {
                ApplicationStatus.SHOWING_SCHEDULED,
                ApplicationStatus.DECLINED,
                ApplicationStatus.WITHDRAWN,
                ApplicationStatus.LEASE_CREATED,
            }
        ),
        ApplicationStatus.SHOWING_SCHEDULED: frozenset(
            {
                ApplicationStatus.APPROVED,  # скасували запис або «перенесли»
                ApplicationStatus.SHOWING_DONE,
                ApplicationStatus.NO_SHOW,
                ApplicationStatus.DECLINED,
                ApplicationStatus.WITHDRAWN,
                ApplicationStatus.LEASE_CREATED,
            }
        ),
        ApplicationStatus.SHOWING_DONE: frozenset(
            {
                ApplicationStatus.SHOWING_SCHEDULED,  # ще один показ (прийти з рідними)
                ApplicationStatus.DECLINED,
                ApplicationStatus.WITHDRAWN,
                ApplicationStatus.LEASE_CREATED,
            }
        ),
    },
)


def clean_application_message(text: str | None) -> str | None:
    """Пара слів про себе. Контакти до схвалення не передаються (правило 9)."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > MESSAGE_MAX:
        raise InvalidInput("Повідомлення задовге", field="message")
    if find_contacts(text):
        raise ContactsInText("Контакти в повідомленні", field="message")
    return text
