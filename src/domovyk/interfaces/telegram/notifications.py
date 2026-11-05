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

    def render(item: PendingNotification) -> RenderedMessage | None:
        p = item.payload
        match item.type:
            case NotificationType.MODERATION_QUEUE:
                return RenderedMessage(
                    uk.NOTIFY_MODERATION_QUEUE,
                    [Button(uk.BTN_OPEN_MODERATION, web_app_url=mini_app_url(base_url, "mod"))],
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
