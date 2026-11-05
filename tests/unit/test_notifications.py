"""Outbox сповіщень і їхні тексти (BOT_FLOWS §18)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from domovyk.adapters.notifier.base import NotifierError
from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.tx import Tx
from domovyk.interfaces.telegram.notifications import make_renderer
from domovyk.modules.notifications.service import NotificationType, PendingNotification
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.fakes_properties import FakeNotifier

APP = "https://example.test/app/"


async def _setup(clock: FixedClock) -> tuple[Services, FakeDb, uuid.UUID]:
    s, db = make_fake_services(clock=clock)
    await s.users.register(42, "Тарас", terms_version=TERMS_V, privacy_version=PRIVACY_V)
    user = await s.users.find_by_telegram(42)
    assert user is not None
    return s, db, user.id


async def _enqueue(
    s: Services, user_id: uuid.UUID, type_: NotificationType, payload: dict[str, Any], key: str
) -> bool:
    # Сервіс приймає tx бізнес-операції; у фейках це порожній Tx.
    return await s.notifications.enqueue(Tx(), user_id, type_, payload, dedupe_key=key)


async def test_send_and_dedupe() -> None:
    clock = FixedClock(datetime(2026, 11, 5, 12, tzinfo=UTC))
    s, _, user_id = await _setup(clock)
    payload = {
        "property_id": "p1",
        "street": "вул. Шевченка",
        "house_number": "12",
        "apartment": "5",
    }
    assert await _enqueue(s, user_id, NotificationType.OWNERSHIP_VERIFIED, payload, "k1")
    assert not await _enqueue(s, user_id, NotificationType.OWNERSHIP_VERIFIED, payload, "k1")

    notifier = FakeNotifier()
    assert await s.notifications.send_pending(make_renderer(APP), notifier) == 1
    (msg,) = notifier.messages
    assert msg.chat_id == 42
    assert "вул. Шевченка, 12, кв. 5" in msg.text
    assert msg.buttons[0].web_app_url == APP + "?start=prop_p1"
    assert await s.notifications.send_pending(make_renderer(APP), notifier) == 0


async def test_transient_failure_retries_later_permanent_gives_up() -> None:
    clock = FixedClock(datetime(2026, 11, 5, 12, tzinfo=UTC))
    s, db, user_id = await _setup(clock)
    await _enqueue(s, user_id, NotificationType.MODERATION_QUEUE, {}, "q1")
    await _enqueue(s, user_id, NotificationType.MODERATION_QUEUE, {}, "q2")

    flaky = FakeNotifier(fail=NotifierError("network"))
    assert await s.notifications.send_pending(make_renderer(APP), flaky) == 0
    assert all(n.attempts == 1 for n in db.prop.notifications)
    # Одразу не повторюємо: чекаємо затримку.
    ok = FakeNotifier()
    assert await s.notifications.send_pending(make_renderer(APP), ok) == 0
    clock.advance(timedelta(minutes=3))
    assert await s.notifications.send_pending(make_renderer(APP), ok) == 2

    await _enqueue(s, user_id, NotificationType.MODERATION_QUEUE, {}, "q3")
    blocked = FakeNotifier(fail=NotifierError("forbidden", permanent=True))
    await s.notifications.send_pending(make_renderer(APP), blocked)
    clock.advance(timedelta(hours=1))
    assert await s.notifications.send_pending(make_renderer(APP), ok) == 0


def test_rejected_text_escapes_reason() -> None:
    render = make_renderer(APP)
    payload = {"property_id": "p", "street": "Зелена", "house_number": "40", "reason": "<b>x</b>"}
    msg = render(PendingNotification(1, uuid.uuid4(), NotificationType.OWNERSHIP_REJECTED, payload))
    assert msg is not None
    assert "&lt;b&gt;x&lt;/b&gt;" in msg.text
    assert render(PendingNotification(2, uuid.uuid4(), "unknown", {})) is None
