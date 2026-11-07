"""Тексти сповіщень з outbox (BOT_FLOWS §18). Воркер викликає `make_renderer`."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any

from domovyk.adapters.notifier.base import Button
from domovyk.interfaces.telegram.keyboards import mini_app_url
from domovyk.interfaces.telegram.texts import uk
from domovyk.modules.notifications.service import (
    NotificationType,
    PendingNotification,
    RenderedMessage,
    Renderer,
)


def format_address(payload: Mapping[str, Any]) -> str:
    street = escape(str(payload.get("street") or ""))
    house = escape(str(payload.get("house_number") or ""))
    apartment = payload.get("apartment")
    if apartment:
        return uk.ADDRESS_APT.format(street=street, house=house, apartment=escape(str(apartment)))
    return uk.ADDRESS.format(street=street, house=house)


def make_renderer(base_url: str) -> Renderer:
    def property_button(payload: Mapping[str, Any]) -> list[Button]:
        pid = str(payload.get("property_id") or "")
        return [Button(uk.BTN_OPEN_PROPERTY, web_app_url=mini_app_url(base_url, f"prop_{pid}"))]

    def lease_button(payload: Mapping[str, Any], text: str, prefix: str) -> list[Button]:
        lid = str(payload.get("lease_id") or "")
        return [Button(text, web_app_url=mini_app_url(base_url, f"{prefix}_{lid}"))]

    def render(item: PendingNotification) -> RenderedMessage | None:
        p = item.payload
        match item.type:
            case NotificationType.MODERATION_QUEUE:
                return RenderedMessage(
                    uk.NOTIFY_MODERATION_QUEUE,
                    [Button(uk.BTN_OPEN_MODERATION, web_app_url=mini_app_url(base_url, "mod"))],
                )
            case NotificationType.MODERATION_ALERT:
                reason = str(p.get("reason") or "")
                return RenderedMessage(
                    uk.NOTIFY_MODERATION_ALERT.format(
                        reason=uk.ALERT_REASONS.get(reason, escape(reason)),
                        name=escape(str(p.get("user_name") or "")),
                        tg_id=escape(str(p.get("telegram_uid") or "—")),
                    ),
                    [Button(uk.BTN_OPEN_MODERATION, web_app_url=mini_app_url(base_url, "mod"))],
                )
            case NotificationType.CHAT_MESSAGE:
                return RenderedMessage(
                    uk.NOTIFY_CHAT_MESSAGE.format(
                        sender=escape(str(p.get("sender") or "")),
                        address=format_address(p),
                        preview=escape(str(p.get("preview") or "")),
                    ),
                    lease_button(p, uk.BTN_OPEN_CHAT, "chat"),
                )
            case NotificationType.LEASE_UPDATE:
                event = str(p.get("event") or "")
                template = uk.LEASE_EVENTS.get(event)
                if template is None:
                    return None
                return RenderedMessage(
                    template.format(
                        address=format_address(p),
                        name=escape(str(p.get("name") or "")),
                        reason=escape(str(p.get("reason") or "")),
                        date=escape(str(p.get("date") or "")),
                    ),
                    lease_button(p, uk.BTN_OPEN_LEASE, "lease"),
                )
            case NotificationType.PAYMENT:
                event = str(p.get("event") or "")
                template = uk.PAYMENT_EVENTS.get(event)
                if template is None:
                    return None
                return RenderedMessage(
                    template.format(
                        address=format_address(p),
                        name=escape(str(p.get("name") or "")),
                        amount=escape(str(p.get("amount") or "")),
                        reason=escape(str(p.get("reason") or "")),
                        due=escape(str(p.get("due") or "")),
                        period=uk.period_label(
                            str(p.get("period") or ""), str(p.get("kind") or "rent")
                        ),
                        how=uk.PAYMENT_HOW_CASH if p.get("cash") else uk.PAYMENT_HOW_RECEIPT,
                    ),
                    lease_button(p, uk.BTN_OPEN_PAYMENTS, "lease"),
                )
            case NotificationType.OWNERSHIP_VERIFIED:
                return RenderedMessage(
                    uk.NOTIFY_OWNERSHIP_VERIFIED.format(address=format_address(p)),
                    property_button(p),
                )
            case NotificationType.OWNERSHIP_REJECTED:
                return RenderedMessage(
                    uk.NOTIFY_OWNERSHIP_REJECTED.format(
                        address=format_address(p), reason=escape(str(p.get("reason") or ""))
                    ),
                    property_button(p),
                )
        return None

    return render
