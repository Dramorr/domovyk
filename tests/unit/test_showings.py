"""Покази (M6, частина 5): FR-APP-4, FR-APP-5. Лише вигадані дані."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from domovyk.domain.applications import ApplicationStatus
from domovyk.domain.showings import ShowingAnswer, SlotRules, clean_slot, resolve_showing
from domovyk.interfaces.telegram.notifications import make_renderer
from domovyk.modules.applications.schemas import ApplicationIn, ShowingAnswerIn, ShowingIn
from domovyk.modules.notifications.service import PendingNotification
from domovyk.modules.showings.schemas import SlotIn
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services
from tests.unit.test_applications import OTHER, OWNER, TENANT, _listing, _uid

TOMORROW = date(2026, 11, 19)
HI = ApplicationIn(message="Хочу подивитись у четвер.")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 18, 10, tzinfo=UTC))  # 12:00 за Києвом


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (OTHER, "Оля")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


def _events(db: FakeDb, user_id: uuid.UUID) -> list[str]:
    return [
        str(n.payload["event"])
        for n in db.prop.notifications
        if n.type == "application" and n.user_id == user_id
    ]


async def _approved(
    s: Services, db: FakeDb, *, tenants: tuple[int, ...] = (TENANT,)
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    prop_id, listing = await _listing(s, db)
    apps = []
    for tg in tenants:
        app = await s.applications.apply(tg, listing, HI)
        await s.applications.approve(OWNER, app.id)
        apps.append(app.id)
    return prop_id, apps


async def _slot(s: Services, prop_id: uuid.UUID, at: time = time(11, 0), cap: int = 1) -> uuid.UUID:
    board = await s.applications.board(OWNER, prop_id)
    before = {x.id for x in board.slots}
    await s.showings.create(
        OWNER, prop_id, SlotIn(day=TOMORROW, start=at, minutes=30, capacity=cap)
    )
    after = await s.applications.board(OWNER, prop_id)
    return next(x.id for x in after.slots if x.id not in before)


# ------------------------------------------------------------------ домен


def test_clean_slot_is_kyiv_time() -> None:
    now = datetime(2026, 11, 18, 10, tzinfo=UTC)
    starts, ends, cap = clean_slot(TOMORROW, time(11, 0), 30, 3, now, SlotRules())
    assert starts == datetime(2026, 11, 19, 9, tzinfo=UTC)  # 11:00 за Києвом взимку = 09:00 UTC
    assert ends - starts == timedelta(minutes=30) and cap == 3
    with pytest.raises(InvalidInput):  # раніше, ніж за годину
        clean_slot(date(2026, 11, 18), time(12, 30), 30, 1, now, SlotRules())
    with pytest.raises(InvalidInput):  # далі, ніж на 60 днів
        clean_slot(date(2027, 3, 1), time(11, 0), 30, 1, now, SlotRules())
    with pytest.raises(InvalidInput):
        clean_slot(TOMORROW, time(11, 0), 5, 1, now, SlotRules())
    with pytest.raises(InvalidInput):
        clean_slot(TOMORROW, time(11, 0), 30, 21, now, SlotRules())


def test_resolve_showing() -> None:
    done, no, moved = ShowingAnswer.DONE, ShowingAnswer.NO_SHOW, ShowingAnswer.MOVED
    assert resolve_showing(done, None, deadline_passed=False) is None  # чекаємо другу сторону
    assert resolve_showing(done, done, deadline_passed=False) is ApplicationStatus.SHOWING_DONE
    assert resolve_showing(no, done, deadline_passed=False) is ApplicationStatus.SHOWING_DONE
    assert resolve_showing(no, no, deadline_passed=False) is ApplicationStatus.NO_SHOW
    assert resolve_showing(None, moved, deadline_passed=False) is ApplicationStatus.APPROVED
    assert resolve_showing(no, None, deadline_passed=True) is ApplicationStatus.NO_SHOW
    assert resolve_showing(None, None, deadline_passed=True) is ApplicationStatus.APPROVED


# ----------------------------------------------------------------- сервіс


async def test_owner_adds_slots(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop_id, _ = await _approved(s, db)
    slot = await _slot(s, prop_id)
    board = await s.applications.board(OWNER, prop_id)
    assert board.can_add and [x.id for x in board.slots] == [slot] and board.slots[0].booked == []
    with pytest.raises(InvalidInput):  # перетинається з наявним
        await s.showings.create(
            OWNER, prop_id, SlotIn(day=TOMORROW, start=time(11, 15), minutes=30)
        )
    with pytest.raises(NotFound):  # чужий об'єкт
        await s.showings.create(TENANT, prop_id, SlotIn(day=TOMORROW, start=time(15, 0)))
    with pytest.raises(NotFound):  # скасувати чужий слот
        await s.showings.cancel(TENANT, slot)


async def test_book_needs_approval_and_respects_capacity(
    env: tuple[Services, FakeDb],
) -> None:
    s, db = env
    prop_id, listing = await _listing(s, db)
    first = await s.applications.apply(TENANT, listing, HI)
    second = await s.applications.apply(OTHER, listing, HI)
    slot = await _slot(s, prop_id)

    with pytest.raises(Conflict):  # до схвалення записатись не можна
        await s.applications.book(TENANT, first.id, ShowingIn(slot_id=slot))
    assert await s.applications.slots(TENANT, first.id) == []

    await s.applications.approve(OWNER, first.id)
    await s.applications.approve(OWNER, second.id)
    choices = await s.applications.slots(TENANT, first.id)
    assert [c.id for c in choices] == [slot] and choices[0].free == 1
    with pytest.raises(PermissionDenied):  # час обирає орендар
        await s.applications.book(OWNER, first.id, ShowingIn(slot_id=slot))

    card = await s.applications.book(TENANT, first.id, ShowingIn(slot_id=slot))
    assert card.status is ApplicationStatus.SHOWING_SCHEDULED and card.showing is not None
    assert card.showing.starts_at == datetime(2026, 11, 19, 9, tzinfo=UTC)
    assert card.can_cancel_showing and card.can_book and not card.showing.can_answer
    assert "showing_booked" in _events(db, await _uid(s, OWNER))

    assert await s.applications.slots(OTHER, second.id) == []  # місце одне — зайняте
    with pytest.raises(Conflict):
        await s.applications.book(OTHER, second.id, ShowingIn(slot_id=slot))

    board = await s.applications.board(OWNER, prop_id)
    assert [b.display_name for b in board.slots[0].booked] == ["Андрій"]


async def test_group_showing(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop_id, apps = await _approved(s, db, tenants=(TENANT, OTHER))
    slot = await _slot(s, prop_id, cap=2)
    await s.applications.book(TENANT, apps[0], ShowingIn(slot_id=slot))
    card = await s.applications.book(OTHER, apps[1], ShowingIn(slot_id=slot))
    assert card.showing is not None and card.showing.group
    board = await s.applications.board(OWNER, prop_id)
    assert len(board.slots[0].booked) == 2


async def test_cancel_and_rebook(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop_id, (app,) = await _approved(s, db)
    morning = await _slot(s, prop_id, time(11, 0))
    evening = await _slot(s, prop_id, time(18, 0))
    await s.applications.book(TENANT, app, ShowingIn(slot_id=morning))
    moved = await s.applications.book(TENANT, app, ShowingIn(slot_id=evening))  # змінити час
    assert moved.showing is not None and moved.showing.slot_id == evening

    cancelled = await s.applications.cancel_showing(OWNER, app)  # власник теж може
    assert cancelled.status is ApplicationStatus.APPROVED and cancelled.showing is None
    assert "showing_cancelled" in _events(db, await _uid(s, TENANT))


async def test_reminder_check_and_result(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    prop_id, (app,) = await _approved(s, db)
    slot = await _slot(s, prop_id)  # завтра 09:00–09:30 UTC
    await s.applications.book(TENANT, app, ShowingIn(slot_id=slot))
    tenant, owner = await _uid(s, TENANT), await _uid(s, OWNER)

    assert (await s.applications.showings_tick())["reminders"] == 0  # ще рано
    clock.advance(timedelta(hours=21, minutes=30))  # 19.11 07:30 UTC — за 1,5 год
    assert (await s.applications.showings_tick())["reminders"] == 2
    assert (await s.applications.showings_tick())["reminders"] == 0  # без дублів
    reminder = next(n for n in db.prop.notifications if n.payload["event"] == "showing_reminder")
    assert reminder.payload["address"] == "вул. Тестова, 7"

    with pytest.raises(Conflict):  # показ ще не почався
        await s.applications.answer_showing(TENANT, app, ShowingAnswerIn(answer=ShowingAnswer.DONE))

    clock.advance(timedelta(hours=3))  # кінець + 1 год минув
    assert (await s.applications.showings_tick())["checks"] == 2
    assert "showing_check" in _events(db, tenant) and "showing_check" in _events(db, owner)

    card = await s.applications.card(TENANT, app)
    assert card.showing is not None and card.showing.can_answer and not card.can_cancel_showing
    after_one = await s.applications.answer_showing(
        TENANT, app, ShowingAnswerIn(answer=ShowingAnswer.DONE)
    )
    assert after_one.status is ApplicationStatus.SHOWING_SCHEDULED  # чекаємо власника
    done = await s.applications.answer_showing(
        OWNER, app, ShowingAnswerIn(answer=ShowingAnswer.DONE)
    )
    assert done.status is ApplicationStatus.SHOWING_DONE and done.can_create_lease
    assert done.showing is not None and done.showing.my_answer is ShowingAnswer.DONE
    # Після показу заявка відкрита: другий відгук на те саме оголошення не створиться.
    with pytest.raises(Conflict):
        await s.applications.apply(TENANT, done.listing.listing_id, HI)


async def test_moved_returns_to_choosing(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    prop_id, (app,) = await _approved(s, db)
    slot = await _slot(s, prop_id)
    await s.applications.book(TENANT, app, ShowingIn(slot_id=slot))
    clock.advance(timedelta(hours=23, minutes=5))  # показ іде
    card = await s.applications.answer_showing(
        OWNER, app, ShowingAnswerIn(answer=ShowingAnswer.MOVED)
    )
    assert card.status is ApplicationStatus.APPROVED and card.showing is None
    assert (await s.applications.card(TENANT, app)).can_book  # орендар обирає новий час


async def test_deadline_settles_with_what_we_have(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    prop_id, apps = await _approved(s, db, tenants=(TENANT, OTHER))
    slot = await _slot(s, prop_id, cap=2)
    for tg, app in zip((TENANT, OTHER), apps, strict=True):
        await s.applications.book(tg, app, ShowingIn(slot_id=slot))
    clock.advance(timedelta(hours=24))
    await s.applications.answer_showing(
        TENANT, apps[0], ShowingAnswerIn(answer=ShowingAnswer.NO_SHOW)
    )
    clock.advance(timedelta(hours=49))
    assert (await s.applications.showings_tick())["settled"] == 2
    assert (await s.applications.card(TENANT, apps[0])).status is ApplicationStatus.NO_SHOW
    # Ніхто не відповів — краще дати ще шанс: заявка знову обирає час.
    assert (await s.applications.card(OTHER, apps[1])).status is ApplicationStatus.APPROVED


async def test_slot_cancelled_frees_bookings(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop_id, (app,) = await _approved(s, db)
    slot = await _slot(s, prop_id)
    await s.applications.book(TENANT, app, ShowingIn(slot_id=slot))
    await s.showings.cancel(OWNER, slot)
    card = await s.applications.card(TENANT, app)
    assert card.status is ApplicationStatus.APPROVED and card.showing is None
    assert "slot_cancelled" in _events(db, await _uid(s, TENANT))
    assert (await s.applications.board(OWNER, prop_id)).slots == []


def test_reminder_text_has_route() -> None:
    render = make_renderer("https://example.test/app/")
    aid = uuid.uuid4()
    msg = render(
        PendingNotification(
            1,
            uuid.uuid4(),
            "application",
            {
                "application_id": str(aid),
                "event": "showing_reminder",
                "street": "вул. Тестова",
                "rooms": 2,
                "price": 15000,
                "starts_at": "2026-11-19T09:00:00+00:00",
                "address": "вул. Тестова, 7",
                "city": "Львів",
            },
        )
    )
    assert msg is not None and "19.11 о 11:00" in msg.text and "вул. Тестова, 7" in msg.text
    assert (msg.buttons[0].url or "").startswith("https://www.google.com/maps/search/")
    assert f"app_{aid}" in (msg.buttons[1].web_app_url or "")
