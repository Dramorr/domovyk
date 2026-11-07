"""Нарахування й облік оплат (M5, частина 1; FR-PAY-1..6). Лише вигадані дані."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict, InvalidInput, PermissionDenied
from domovyk.core.tx import NullTxManager
from domovyk.domain.leases import PayMethod
from domovyk.domain.payments import (
    ChargeStatus,
    PaymentStatus,
    charge_status,
    reminder_offset,
    rent_periods,
    sniff_receipt,
)
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.modules.leases.schemas import LeaseCreate, PayDetailsIn
from domovyk.modules.payments.schemas import CashIn, DecisionIn
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services

OWNER, TENANT, ROOMMATE = 42, 43, 44
CARD = "4111 1111 1111 1111"  # тестовий номер (Luhn), не справжня картка
PDF = b"%PDF-1.4 fake receipt"


# ------------------------------------------------------------------ домен


def test_rent_periods() -> None:
    periods = rent_periods(date(2026, 12, 1), date(2027, 10, 31), 5)
    assert len(periods) == 11
    assert periods[0] == (date(2026, 12, 1), date(2026, 12, 5))
    assert periods[-1] == (date(2027, 10, 1), date(2027, 10, 5))
    # Заїзд після дня оплати — перша оплата в день заїзду; 31-ше в лютому → 28-ме.
    mid = rent_periods(date(2026, 12, 15), date(2027, 3, 14), 31)
    assert mid[0] == (date(2026, 12, 1), date(2026, 12, 31))
    assert (date(2027, 2, 1), date(2027, 2, 28)) in mid
    # Оренда закінчилась до дня оплати місяця — його не нараховуємо.
    assert rent_periods(date(2026, 12, 1), date(2027, 1, 3), 5)[-1][0] == date(2026, 12, 1)


def test_charge_status_and_reminders() -> None:
    due, amount = date(2027, 1, 5), Decimal(18000)
    assert charge_status(due, date(2027, 1, 1), amount, Decimal(0), False) is ChargeStatus.UPCOMING
    assert charge_status(due, due, amount, Decimal(0), False) is ChargeStatus.DUE
    assert charge_status(due, date(2027, 1, 6), amount, Decimal(0), False) is ChargeStatus.OVERDUE
    assert charge_status(due, date(2027, 1, 6), amount, Decimal(0), True) is ChargeStatus.REPORTED
    assert charge_status(due, date(2027, 1, 6), amount, Decimal(18000), False) is ChargeStatus.PAID
    assert reminder_offset(due, date(2027, 1, 2), (-3, 0, 1)) == -3
    assert reminder_offset(due, date(2027, 1, 6), (-3, 0, 1)) == 1
    assert reminder_offset(due, date(2027, 1, 4), (-3, 0, 1)) is None


def test_sniff_receipt() -> None:
    assert sniff_receipt(PDF) == "application/pdf"
    assert sniff_receipt(b"\xff\xd8\xff\xe0 jpeg") == "image/jpeg"
    assert sniff_receipt(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert sniff_receipt(b"MZ executable") is None


# --------------------------------------------------------------- оточення


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 6, 10, tzinfo=UTC))


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (ROOMMATE, "Оля")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _active(s: Services, db: FakeDb, *, deposit: int | None = 18000) -> uuid.UUID:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Шевченка", house_number="12"),
            apartment="5",
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
            rent_amount=18000,
            rent_due_day=5,
            deposit_amount=deposit,
            pay_method=PayMethod.CARD,
            pay_details=PayDetailsIn(card=CARD, recipient_name="Ірина Коваль"),
        ),
    )
    await s.leases.accept_invite(TENANT, invite.token)
    await s.leases.accept_terms(TENANT, lease.id)
    async with NullTxManager().begin() as tx:
        await s.leases.activate(tx, lease.id)
    return lease.id


def _events(db: FakeDb, kind: str | None = None) -> list[str]:
    return [
        str(n.payload.get("event"))
        for n in db.prop.notifications
        if n.type == "payment" and (kind is None or n.payload.get("kind") == kind)
    ]


# ------------------------------------------------------------- нарахування


async def test_charges_generated_ahead_with_reminders(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    lease_id = await _active(s, db)
    await s.payments.run_daily()  # 6.11: лише депозит (до заїзду), оренда ще не за 7 днів
    view = await s.payments.overview(TENANT, lease_id)
    assert [c.kind for c in view.charges] == ["deposit"]
    clock.advance(timedelta(days=22))  # 28.11 — за 7 днів до 5.12
    await s.payments.run_daily()
    view = await s.payments.overview(TENANT, lease_id)
    assert {c.kind for c in view.charges} == {"deposit", "rent"}
    assert view.next_charge is not None and view.next_charge.kind == "deposit"
    clock.advance(timedelta(days=4))  # 2.12 — за 3 дні до оплати
    await s.payments.run_daily()
    await s.payments.run_daily()  # повтор не дублює нагадування
    assert _events(db, "rent").count("rent_reminder_before") == 1
    assert _events(db, "deposit").count("rent_reminder_before") == 1  # за 3 дні до заїзду
    clock.advance(timedelta(days=4))  # 6.12 — прострочено
    await s.payments.run_daily()
    rent = next(c for c in (await s.payments.overview(OWNER, lease_id)).charges if c.kind == "rent")
    assert rent.status is ChargeStatus.OVERDUE
    assert "rent_overdue_landlord" in _events(db) and "rent_reminder_after" in _events(db)


# -------------------------------------------------------- оплата й рішення


async def _rent(s: Services, lease_id: uuid.UUID, clock: FixedClock) -> uuid.UUID:
    clock.advance(timedelta(days=22))
    await s.payments.run_daily()
    view = await s.payments.overview(TENANT, lease_id)
    return next(c.id for c in view.charges if c.kind == "rent")


async def test_pay_info_shows_full_requisites(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    info = await s.payments.pay_info(TENANT, charge_id)
    assert info.card == "4111111111111111" and info.recipient == "Ірина Коваль"
    assert info.amount == Decimal(18000) and info.street == "вул. Шевченка"


async def test_cash_flow_and_partial_payment(
    env: tuple[Services, FakeDb], clock: FixedClock
) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    with pytest.raises(PermissionDenied):
        await s.payments.report_cash(OWNER, charge_id, CashIn(amount=Decimal(1)))
    view = await s.payments.report_cash(TENANT, charge_id, CashIn(amount=Decimal(10000)))
    charge = next(c for c in view.charges if c.id == charge_id)
    assert charge.status is ChargeStatus.REPORTED
    assert "payment_reported" in _events(db)
    owner = await s.payments.overview(OWNER, lease_id)
    assert owner.awaiting_my_decision == 1
    payment_id = charge.payments[0].id
    with pytest.raises(InvalidInput):  # «не отримав» — лише з причиною
        await s.payments.decide(OWNER, payment_id, DecisionIn(accept=False))
    view = await s.payments.decide(OWNER, payment_id, DecisionIn(accept=True))
    charge = next(c for c in view.charges if c.id == charge_id)
    assert charge.paid == Decimal(10000) and charge.remaining == Decimal(8000)
    assert charge.status is not ChargeStatus.PAID  # часткова оплата
    view = await s.payments.mark_received(OWNER, charge_id, CashIn(amount=Decimal(8000)))
    charge = next(c for c in view.charges if c.id == charge_id)
    assert charge.status is ChargeStatus.PAID
    with pytest.raises(Conflict):
        await s.payments.report_cash(TENANT, charge_id, CashIn(amount=Decimal(1)))


async def test_receipt_upload_and_reject(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    with pytest.raises(InvalidInput):
        await s.payments.report_receipt(TENANT, charge_id, Decimal(18000), "a.exe", b"MZ")
    view = await s.payments.report_receipt(TENANT, charge_id, Decimal(18000), "kv.pdf", PDF)
    payment = next(c for c in view.charges if c.id == charge_id).payments[0]
    assert payment.status is PaymentStatus.PENDING_LANDLORD and payment.mismatches == ["unparsed"]
    assert payment.has_receipt and payment.receipt_mime == "application/pdf"
    with pytest.raises(Conflict):  # той самий файл удруге
        await s.payments.report_receipt(TENANT, charge_id, Decimal(18000), "kv.pdf", PDF)
    _, data, mime = await s.payments.receipt(OWNER, payment.id)
    assert data == PDF and mime == "application/pdf"
    view = await s.payments.decide(
        OWNER, payment.id, DecisionIn(accept=False, reason="Гроші не надійшли")
    )
    charge = next(c for c in view.charges if c.id == charge_id)
    assert charge.payments[0].status is PaymentStatus.REJECTED
    assert charge.status is not ChargeStatus.REPORTED
    assert "payment_rejected" in _events(db)


async def test_pending_landlord_reminder(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    db.payment.payments.clear()
    await s.payments.report_cash(TENANT, charge_id, CashIn(amount=Decimal(18000)))
    # Фейк ставить created_at у 2026-01-01 — «давно»: нагадування власнику одразу.
    await s.payments.run_daily()
    assert "payment_pending_reminder" in _events(db)
