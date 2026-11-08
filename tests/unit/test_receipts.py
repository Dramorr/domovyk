"""Розпізнавання квитанцій і автозарахування (FR-PAY-3, ADR 0012). Лише вигадані квитанції."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from domovyk.adapters.receipts.pdf import PdfReceiptReader
from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import Conflict
from domovyk.domain.payments import (
    ChargeStatus,
    PaymentStatus,
    card_matches,
    name_matches,
    receipt_mismatches,
)
from domovyk.modules.payments.schemas import DecisionIn
from tests.fakes import FakeDb
from tests.receipt_fixtures import monobank_pdf, signed

# Фікстури clock і env — з test_payments (той самий набір людей і оренда).
from tests.unit.test_payments import CARD, OWNER, TENANT, _active, _events, _rent, clock, env  # noqa: F401

WHEN = "27.11.2026 21:04"  # за 8 днів до дати оплати 5.12, «сьогодні» в тестах — 28.11

# ------------------------------------------------------------------ адаптер


async def test_reader_parses_signed_monobank_receipt() -> None:
    reader = PdfReceiptReader()
    doc = reader.open(signed(monobank_pdf(amount="15 000.50", fee="12.00", when=WHEN)))
    assert doc is not None and doc.pdf[:5] == b"%PDF-"
    assert doc.signed_by is not None and "УНІВЕРСАЛ" in doc.signed_by.organization
    parsed = await reader.parse(doc)
    assert parsed is not None
    assert parsed.bank == "monobank" and parsed.reference == "TEST-0000-AAAA-0001"
    assert parsed.amount == Decimal("15000.50") and parsed.fee == Decimal("12.00")
    assert parsed.operated_at == datetime(2026, 11, 27, 19, 4, tzinfo=UTC)  # 21:04 за Києвом
    assert parsed.recipient_card == "4111111111111111"  # одержувач, не відправник
    assert parsed.recipient_name == "Ірина К."
    assert parsed.bank_signed


async def test_reader_unsigned_and_foreign_files() -> None:
    reader = PdfReceiptReader()
    plain = reader.open(monobank_pdf())
    assert plain is not None and plain.signed_by is None
    parsed = await reader.parse(plain)
    assert parsed is not None and not parsed.bank_signed
    # Підписав не банк — теж «без підпису банку».
    other = reader.open(signed(monobank_pdf(), organization="ТОВ Хтось"))
    assert other is not None
    parsed = await reader.parse(other)
    assert parsed is not None and not parsed.bank_signed
    assert reader.open(b"MZ not a pdf") is None
    assert reader.open(b"\x30\x03\x02\x01\x00") is None  # DER, але не CMS
    assert reader.open(signed(b"not a pdf inside")) is None
    from weasyprint import HTML

    other_bank = bytes(HTML(string="<p>Квитанція іншого банку</p>").write_pdf())
    doc = reader.open(other_bank)
    assert doc is not None and await reader.parse(doc) is None


# ------------------------------------------------------------------- домен


def test_card_and_name_rules() -> None:
    assert card_matches(CARD, "4111111111111111", name_ok=False)
    assert not card_matches(CARD, "4111111111111112", name_ok=True)
    # Замаскована картка — лише разом з ім'ям.
    assert card_matches(CARD, "411111******1111", name_ok=True)
    assert not card_matches(CARD, "411111******1111", name_ok=False)
    assert not card_matches(CARD, "************1111", name_ok=True)  # самих 4 цифр замало
    assert name_matches("Ірина Коваль", "Ірина К.")
    assert name_matches("Коваль Ірина Петрівна", "Ірина К.")
    assert not name_matches("Ірина Коваль", "Ірина Л.")
    assert not name_matches("Ірина Коваль", "Ірина")


def test_receipt_mismatches() -> None:
    ok = {
        "amount": Decimal(18000),
        "remaining": Decimal(18000),
        "operated_on": date(2026, 11, 27),
        "earliest": date(2026, 11, 4),
        "latest": date(2026, 11, 28),
        "recipient_ok": True,
        "bank_signed": True,
    }
    assert receipt_mismatches(**ok) == []  # type: ignore[arg-type]
    bad = ok | {"amount": Decimal(100), "recipient_ok": False, "bank_signed": False}
    bad |= {"operated_on": date(2026, 11, 29)}
    assert receipt_mismatches(**bad) == ["amount", "recipient", "date", "unsigned"]  # type: ignore[arg-type]


# ------------------------------------------------------------------- сервіс


async def _payment(s: Services, lease_id: uuid.UUID, charge_id: uuid.UUID, user: int = TENANT):
    view = await s.payments.overview(user, lease_id)
    return next(c for c in view.charges if c.id == charge_id)


async def test_auto_accept_then_confirmed_after_window(
    env: tuple[Services, FakeDb],  # noqa: F811
    clock: FixedClock,  # noqa: F811
) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    receipt = signed(monobank_pdf(when=WHEN))
    # Сума у формі ігнорується — беремо з квитанції.
    await s.payments.report_receipt(TENANT, charge_id, Decimal(1), "receipt.pdf", receipt)
    charge = await _payment(s, lease_id, charge_id)
    p = charge.payments[0]
    assert p.status is PaymentStatus.AUTO_ACCEPTED and p.mismatches == []
    assert p.amount == Decimal(18000) and p.receipt_bank == "monobank"
    assert p.dispute_deadline == clock.now() + timedelta(days=3)
    assert charge.status is ChargeStatus.REPORTED
    assert "payment_auto_accepted" in _events(db)
    stored = db.payment.payments[p.id]
    assert stored.bank_reference == "monobank:TEST-0000-AAAA-0001"
    assert stored.parsed["recipient"] == "**** 1111"  # повного номера не зберігаємо
    assert "Андрій" not in str(stored.parsed)  # дані відправника не зберігаємо

    # Власнику показуємо PDF зсередини контейнера.
    name, data, mime = await s.payments.receipt(OWNER, p.id)
    assert data[:5] == b"%PDF-" and mime == "application/pdf" and name.endswith(".pdf")

    # Та сама квитанція (інший файл, той самий номер) удруге не пройде.
    with pytest.raises(Conflict):
        await s.payments.report_receipt(
            TENANT, charge_id, Decimal(1), "again.pdf", monobank_pdf(when=WHEN)
        )

    assert await s.payments.confirm_expired() == 0  # вікно ще триває
    clock.advance(timedelta(days=3, minutes=1))
    assert await s.payments.confirm_expired() == 1
    charge = await _payment(s, lease_id, charge_id)
    assert charge.payments[0].status is PaymentStatus.CONFIRMED
    assert charge.status is ChargeStatus.PAID
    assert "payment_confirmed" in _events(db)


async def test_landlord_disputes_auto_accepted(
    env: tuple[Services, FakeDb],  # noqa: F811
    clock: FixedClock,  # noqa: F811
) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    await s.payments.report_receipt(
        TENANT, charge_id, Decimal(1), "r.pdf", signed(monobank_pdf(when=WHEN))
    )
    p = (await _payment(s, lease_id, charge_id)).payments[0]
    view = await s.payments.decide(OWNER, p.id, DecisionIn(accept=False, reason="Не надійшло"))
    charge = next(c for c in view.charges if c.id == charge_id)
    assert charge.payments[0].status is PaymentStatus.DISPUTED
    assert charge.status is not ChargeStatus.REPORTED
    assert "payment_disputed" in _events(db)
    clock.advance(timedelta(days=5))
    assert await s.payments.confirm_expired() == 0  # оскаржене саме не підтверджується


@pytest.mark.parametrize(
    ("kwargs", "wrap", "expected"),
    [
        ({"recipient_card": "5555555555554444"}, True, ["recipient"]),
        ({"recipient_card": "411111******1111", "recipient_name": "Олег К."}, True, ["recipient"]),
        ({"amount": "9000.00"}, True, ["amount"]),
        ({"when": "01.10.2026 10:00"}, True, ["date"]),
        ({}, False, ["unsigned"]),
    ],
)
async def test_mismatch_goes_to_landlord(
    env: tuple[Services, FakeDb],  # noqa: F811
    clock: FixedClock,  # noqa: F811
    kwargs: dict[str, str],
    wrap: bool,
    expected: list[str],
) -> None:
    s, db = env
    lease_id = await _active(s, db)
    charge_id = await _rent(s, lease_id, clock)
    pdf = monobank_pdf(**({"when": WHEN} | kwargs))
    await s.payments.report_receipt(
        TENANT, charge_id, Decimal(1), "r.pdf", signed(pdf) if wrap else pdf
    )
    p = (await _payment(s, lease_id, charge_id)).payments[0]
    assert p.status is PaymentStatus.PENDING_LANDLORD
    assert p.mismatches == expected
    assert "payment_reported" in _events(db)


def test_auto_accept_and_dispute_texts() -> None:
    from domovyk.interfaces.telegram.notifications import make_renderer
    from domovyk.modules.notifications.schemas import PendingNotification

    render = make_renderer("https://example.test/app/")
    base = {
        "lease_id": str(uuid.uuid4()),
        "street": "вул. Тестова",
        "house_number": "1",
        "period": "2026-12-01",
        "kind": "rent",
        "name": "Андрій",
        "amount": "18 000",
    }
    auto = render(
        PendingNotification(
            1, uuid.uuid4(), "payment", base | {"event": "payment_auto_accepted", "days": 3}
        )
    )
    assert auto is not None and "протягом 3 днів" in auto.text and "грудень 2026" in auto.text
    one = render(
        PendingNotification(
            2, uuid.uuid4(), "payment", base | {"event": "payment_auto_accepted", "days": 1}
        )
    )
    assert one is not None and "протягом 1 дня" in one.text
    disputed = render(
        PendingNotification(
            3,
            uuid.uuid4(),
            "payment",
            base | {"event": "payment_disputed", "reason": "Не надійшло"},
        )
    )
    assert disputed is not None and "Не надійшло" in disputed.text
