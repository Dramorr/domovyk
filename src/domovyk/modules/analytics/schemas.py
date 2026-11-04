"""DTO модуля analytics."""

from __future__ import annotations

from enum import StrEnum


class EventName(StrEnum):
    """Події для оцінки реального інтересу (SPEC, FR-BETA-2)."""

    REGISTERED = "registered"
    PROPERTY_CREATED = "property_created"
    OWNERSHIP_SUBMITTED = "ownership_submitted"
    OWNERSHIP_VERIFIED = "ownership_verified"
    LISTING_PUBLISHED = "listing_published"
    FILTER_SAVED = "filter_saved"
    MATCH_NOTIFIED = "match_notified"
    APPLICATION_SENT = "application_sent"
    APPLICATION_APPROVED = "application_approved"
    SHOWING_DONE = "showing_done"
    LEASE_CREATED = "lease_created"
    INVITE_ACCEPTED = "invite_accepted"
    CONTRACT_SIGNED = "contract_signed"
    PAYMENT_REPORTED = "payment_reported"
    PAYMENT_AUTO_CONFIRMED = "payment_auto_confirmed"
    METER_SUBMITTED = "meter_submitted"
    DONATE_CLICKED = "donate_clicked"
    FEEDBACK_SENT = "feedback_sent"


class EventSource(StrEnum):
    BOT = "telegram_bot"
    MINI_APP = "mini_app"
    API = "api"
    WORKER = "worker"
