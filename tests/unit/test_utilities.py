"""Комуналка: лічильники, показники, нагадування, нарахування КП (M5, частина 3).

FR-UTL-1..5. Лише вигадані дані.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from PIL import Image

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from domovyk.core.tx import NullTxManager
from domovyk.domain.contracts import PartyRole
from domovyk.domain.leases import PayMethod
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.domain.utilities import (
    MeterKind,
    Window,
    clean_reading,
    current_or_next_window,
    estimate,
    reminder_stage,
    window_in_month,
)
from domovyk.interfaces.telegram.notifications import make_renderer
from domovyk.modules.leases.schemas import LeaseCreate, PayDetailsIn
from domovyk.modules.notifications.service import PendingNotification
from domovyk.modules.payments.schemas import UtilityChargeIn
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from domovyk.modules.utilities.schemas import MeterIn
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services

OWNER, TENANT, STRANGER = 42, 43, 45
CARD = "4111 1111 1111 1111"  # тестовий номер (Luhn), не справжня картка
WATER, POWER, GAS = 1, 2, 3  # постачальники з tests/fakes_utilities.py

FIRST_TO_FIFTH = {"from_day": 1, "to_day": 5}
TENTH_TO_LAST_WORKING = {"from_day": 10, "to": "last_working_day"}
AROUND_MONTH_END = {"from_days_before_month_end": 2, "to_day_next_month": 3}


# ------------------------------------------------------------------ домен


def test_windows() -> None:
    assert window_in_month(FIRST_TO_FIFTH, 2026, 12) == Window(date(2026, 12, 1), date(2026, 12, 5))
    # 31.01.2027 — неділя: останній робочий день — п'ятниця 29.01.
    assert window_in_month(TENTH_TO_LAST_WORKING, 2027, 1) == Window(
        date(2027, 1, 10), date(2027, 1, 29)
    )
    assert window_in_month(AROUND_MONTH_END, 2026, 11) == Window(
        date(2026, 11, 28), date(2026, 12, 3)
    )
    assert window_in_month({}, 2026, 11) is None
    assert window_in_month({"from_day": "1"}, 2026, 11) is None
    # 2 грудня — ще листопадове вікно світла.
    assert current_or_next_window(AROUND_MONTH_END, date(2026, 12, 2)) == Window(
        date(2026, 11, 28), date(2026, 12, 3)
    )
    # 6 грудня — газ уже подали, наступне вікно — у січні.
    assert current_or_next_window(FIRST_TO_FIFTH, date(2026, 12, 6)) == Window(
        date(2027, 1, 1), date(2027, 1, 5)
    )


def test_reminder_stage() -> None:
    w = Window(date(2026, 12, 1), date(2026, 12, 5))
    assert reminder_stage(w, date(2026, 11, 30)) is None
    assert reminder_stage(w, date(2026, 12, 1)) == "open"
    assert reminder_stage(w, date(2026, 12, 4)) == "last"
    assert reminder_stage(w, date(2026, 12, 5)) == "last"
    assert reminder_stage(None, date(2026, 12, 1)) is None


def test_clean_reading_and_estimate() -> None:
    assert clean_reading("1 234,5") == Decimal("1234.500")
    for bad in ("-1", "abc", "1.2345", "NaN"):
        with pytest.raises(InvalidInput):
            clean_reading(bad)
    assert estimate(Decimal("12.5"), Decimal("4.3200")) == Decimal("54.00")
    assert estimate(None, Decimal(1)) is None and estimate(Decimal(1), None) is None


# --------------------------------------------------------------- оточення


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 6, 10, tzinfo=UTC))


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (STRANGER, "Сусід")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _active(s: Services, db: FakeDb) -> tuple[uuid.UUID, uuid.UUID]:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Тестова", house_number="7"),
            apartment="3",
            rooms=2,
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    lease, invite = await s.leases.create_lease(
        OWNER,
        LeaseCreate(
            property_id=prop.id,
            start_date=date(2026, 12, 1),
            months=11,
            rent_amount=15000,
            rent_due_day=5,
            pay_method=PayMethod.CARD,
            pay_details=PayDetailsIn(card=CARD, recipient_name="Ірина Тестова"),
        ),
    )
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    async with NullTxManager().begin() as tx:
        await s.leases.activate(tx, lease.id)
    return prop.id, lease.id


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 200, 200)).save(buf, "JPEG")
    return buf.getvalue()


def _reminders(db: FakeDb) -> list[tuple[uuid.UUID, str, list[str]]]:
    return [
        (n.user_id, str(n.payload["event"]), list(n.payload["kinds"]))
        for n in db.prop.notifications
        if n.type == "meter_reminder"
    ]


# -------------------------------------------------------- лічильники й показники


async def test_owner_manages_meters(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    prop_id, _ = await _active(s, db)
    view = await s.utilities.add_meter(
        OWNER,
        prop_id,
        MeterIn(
            kind=MeterKind.WATER_COLD,
            provider_id=WATER,
            label="Кухня",
            tariff=Decimal("40.5"),
            initial_value=Decimal("100"),
        ),
    )
    assert view.can_manage and [p.id for p in view.providers] == [WATER, POWER, GAS]
    water = view.meters[0]
    assert water.last is not None and water.last.value == Decimal("100.000")
    # Постачальник не обслуговує такий лічильник.
    with pytest.raises(InvalidInput):
        await s.utilities.add_meter(OWNER, prop_id, MeterIn(kind=MeterKind.GAS, provider_id=WATER))
    with pytest.raises(NotFound):
        await s.utilities.for_property(STRANGER, prop_id)
    with pytest.raises(NotFound):
        await s.utilities.for_property(TENANT, prop_id)
    # Лічильник з показниками не видаляється, а вимикається.
    view = await s.utilities.remove_meter(OWNER, water.id)
    assert not view.meters[0].is_active
    view = await s.utilities.add_meter(OWNER, prop_id, MeterIn(kind=MeterKind.GAS))
    gas = next(m for m in view.meters if m.kind is MeterKind.GAS)
    view = await s.utilities.remove_meter(OWNER, gas.id)
    assert all(m.kind is not MeterKind.GAS for m in view.meters)


async def test_tenant_submits_reading(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    prop_id, lease_id = await _active(s, db)
    view = await s.utilities.add_meter(
        OWNER,
        prop_id,
        MeterIn(
            kind=MeterKind.WATER_COLD,
            provider_id=WATER,
            tariff=Decimal("40"),
            initial_value=Decimal("100"),
        ),
    )
    meter_id = view.meters[0].id
    clock.advance(timedelta(days=34))  # 10.12 — вікно водоканалу відкрилось

    tenant = await s.utilities.for_lease(TENANT, lease_id)
    assert not tenant.can_manage and tenant.can_submit and tenant.providers == []
    assert tenant.meters[0].due and tenant.meters[0].i_submit
    # Стартовий показник від власника у вікні не закриває подачу орендаря.
    await s.utilities.submit_reading(OWNER, meter_id, "100")
    assert (await s.utilities.for_lease(TENANT, lease_id)).meters[0].due
    clock.advance(timedelta(minutes=5))
    assert tenant.meters[0].window is not None and tenant.meters[0].window.open

    with pytest.raises(InvalidInput):
        await s.utilities.submit_reading(TENANT, meter_id, "99.9")
    with pytest.raises(NotFound):
        await s.utilities.submit_reading(STRANGER, meter_id, "120")
    result = await s.utilities.submit_reading(TENANT, meter_id, "112,5", _jpeg())
    assert result.reading.consumed == Decimal("12.500")
    assert result.reading.estimate == Decimal("500.00")
    assert result.reading.has_photo and result.reading.mine and result.reading.can_delete
    assert result.cabinet_url == "https://water.example"
    assert not result.meter.due

    sent = await s.utilities.mark_sent(TENANT, result.reading.id)
    assert sent.reading.sent_to_provider_at is not None
    assert await s.utilities.reading_photo(OWNER, result.reading.id)

    owner = await s.utilities.for_lease(OWNER, lease_id)
    assert owner.meters[0].last is not None and owner.meters[0].last.submitted_by_name == "Андрій"
    assert not owner.meters[0].i_submit

    clock.advance(timedelta(days=3))  # строк на виправлення минув
    with pytest.raises(Conflict):
        await s.utilities.delete_reading(TENANT, result.reading.id)
    view = await s.utilities.delete_reading(OWNER, result.reading.id)
    assert view.meters[0].last is not None and view.meters[0].last.value == Decimal("100.000")
    assert not db.storage.files or all(not k.startswith("meters/") for k in db.storage.files)


async def test_reminders_go_to_submitter_once(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    prop_id, _ = await _active(s, db)
    view = await s.utilities.add_meter(
        OWNER, prop_id, MeterIn(kind=MeterKind.WATER_COLD, provider_id=WATER)
    )
    water_id = view.meters[0].id
    await s.utilities.add_meter(
        OWNER,
        prop_id,
        MeterIn(kind=MeterKind.GAS, provider_id=GAS, submits=PartyRole.LANDLORD),
    )
    tenant = await s.users.require_active(TENANT)
    owner = await s.users.require_active(OWNER)

    assert await s.utilities.run_reminders() == 0  # 6.11 — ні вікна, ні заїзду
    clock.advance(timedelta(days=25))  # 1.12 — вікно газу; подає власник
    await s.utilities.run_reminders()
    await s.utilities.run_reminders()
    assert _reminders(db) == [(owner.id, "open", ["gas"])]

    clock.advance(timedelta(days=9))  # 10.12 — вікно води; подає орендар
    await s.utilities.run_reminders()
    assert (tenant.id, "open", ["water_cold"]) in _reminders(db)

    await s.utilities.submit_reading(TENANT, water_id, "5")
    clock.advance(timedelta(days=20))  # 30.12 — останні дні вікна, але показник уже є
    await s.utilities.run_reminders()
    assert [r for r in _reminders(db) if r[1] == "last"] == []

    render = make_renderer("https://example.test/app/")
    item = next(n for n in db.prop.notifications if n.type == "meter_reminder")
    message = render(PendingNotification(1, item.user_id, item.type, item.payload))
    assert message is not None
    assert "показники газу" in message.text and "05.12" in message.text
    assert "pmeters_" not in (message.buttons[0].web_app_url or "")  # оренда є — веде в неї


async def test_tenant_reminder_can_be_switched_off(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    prop_id, lease_id = await _active(s, db)
    await s.utilities.add_meter(
        OWNER, prop_id, MeterIn(kind=MeterKind.WATER_COLD, provider_id=WATER)
    )
    prefs = await s.leases.notification_prefs(TENANT, lease_id)
    prefs.meter_reminder = False
    await s.leases.set_notification_prefs(TENANT, lease_id, prefs)
    clock.advance(timedelta(days=34))
    await s.utilities.run_reminders()
    assert _reminders(db) == []


# ------------------------------------------------------------ КП власнику


async def test_utility_charge(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    _, lease_id = await _active(s, db)
    body = UtilityChargeIn(
        period_month=date(2026, 11, 1),
        amount=Decimal("1350.40"),
        due_date=date(2026, 11, 15),
        note="світло 900, вода 450,40",
    )
    with pytest.raises(PermissionDenied):
        await s.payments.add_utility_charge(TENANT, lease_id, body)
    view = await s.payments.add_utility_charge(OWNER, lease_id, body)
    charge = next(c for c in view.charges if c.kind == "utilities")
    assert charge.amount == Decimal("1350.40") and charge.note == "світло 900, вода 450,40"
    assert charge.can_cancel
    events = [n.payload.get("event") for n in db.prop.notifications if n.type == "payment"]
    assert "utilities_charged" in events
    with pytest.raises(Conflict):
        await s.payments.add_utility_charge(OWNER, lease_id, body)
    with pytest.raises(InvalidInput):
        await s.payments.add_utility_charge(
            OWNER, lease_id, body.model_copy(update={"due_date": date(2026, 11, 1)})
        )
    tenant_view = await s.payments.overview(TENANT, lease_id)
    assert not next(c for c in tenant_view.charges if c.kind == "utilities").can_cancel
    view = await s.payments.cancel_charge(OWNER, charge.id)
    assert all(c.kind != "utilities" for c in view.charges)
