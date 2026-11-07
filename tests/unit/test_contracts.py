"""Договір і підписи через портал Дії (M4, FR-CTR-1..7, FR-LSE-6, FR-LSE-8, ADR 0010).

Підписи — вигадані CAdES/ASiC з tests/signing_fixtures.py (жодних справжніх сертифікатів).
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from domovyk.adapters.pdf.weasy import WeasyPrintRenderer
from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.crypto import identifier_hash
from domovyk.core.errors import (
    Banned,
    Conflict,
    InvalidInput,
    LeaseLimitReached,
    PermissionDenied,
    ServiceUnavailable,
)
from domovyk.core.tx import NullTxManager
from domovyk.domain.contracts import (
    ContractStatus,
    SignatureCheck,
    clean_legal_name,
    names_match,
)
from domovyk.domain.leases import LeaseStatus, MemberRole, PayMethod
from domovyk.domain.properties import OwnershipStatus, PropertyKind, VerificationStatus
from domovyk.modules.contracts.render import months_word, render_contract
from domovyk.modules.contracts.service import ContractsConfig
from domovyk.modules.leases.schemas import LeaseCreate, PayDetailsIn
from domovyk.modules.moderation.schemas import BanIdentifiers
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate
from tests.fakes import ADMIN_TG, PRIVACY_V, TERMS_V, TEST_HMAC, FakeDb, make_fake_services
from tests.fakes_properties import FakePropertiesRepository
from tests.signing_fixtures import FAKE_TAX_ID, asice, cades, with_unparseable_signed_attr

OWNER, TENANT, ROOMMATE, OTHER_TENANT = 42, 43, 44, 46
OWNER_NAME = "Коваль Ірина Петрівна"
TENANT_NAME = "Петренко Андрій Іванович"
CARD = "4111 1111 1111 1111"  # тестовий номер (Luhn), не справжня картка


# ------------------------------------------------------------------ домен


def test_legal_name() -> None:
    assert clean_legal_name("  коваль   ірина  петрівна ") == "Коваль Ірина Петрівна"
    assert clean_legal_name("Д’Артаньян Шарль") == "Д'Артаньян Шарль"
    assert clean_legal_name("Ян Лі") == "Ян Лі"
    for bad in (
        "Ірина",
        "К Р",
        "Коваль І. П.",
        "Коваль Ірина 3",
        "a b c d e",
        "Коваль <b>Ірина</b>",
    ):
        with pytest.raises(InvalidInput):
            clean_legal_name(bad)


def test_names_match() -> None:
    assert names_match("КОВАЛЬ ІРИНА ПЕТРІВНА", "Коваль Ірина Петрівна")
    assert names_match("Ірина Петрівна Коваль", "Коваль Ірина Петрівна")
    assert names_match("Коваль Ірина", "Коваль Ірина Петрівна")  # без по батькові
    assert not names_match("Коваль Олена Петрівна", "Коваль Ірина Петрівна")
    assert not names_match("Коваль", "Коваль Ірина Петрівна")
    assert not names_match(None, "Коваль Ірина")


def test_months_word() -> None:
    assert [months_word(n) for n in (1, 2, 5, 11, 21, 22, 35)] == [
        "місяць",
        "місяці",
        "місяців",
        "місяців",
        "місяць",
        "місяці",
        "місяців",
    ]


# --------------------------------------------------------------- оточення


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 7, 10, tzinfo=UTC))


async def _env(clock: FixedClock, **kw: object) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock, **kw)  # type: ignore[arg-type]
    for tg, name in (
        (OWNER, "Ірина"),
        (TENANT, "Андрій"),
        (ROOMMATE, "Оля"),
        (OTHER_TENANT, "Іван"),
        (ADMIN_TG, "Модератор"),
    ):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    return await _env(clock)


async def _flat(
    s: Services, db: FakeDb, house: str = "12", owner_doc: str | None = None
) -> uuid.UUID:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Шевченка", house_number=house),
            apartment="5",
            rooms=2,
            area=Decimal("54"),
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    if owner_doc:
        vid = await FakePropertiesRepository(db.prop).create_verification(
            prop.owner_user_id, prop.id, VerificationStatus.VERIFIED
        )
        db.prop.verifications[vid].owner_name_in_doc = owner_doc
    return prop.id


async def _agreed_lease(
    s: Services,
    db: FakeDb,
    *,
    house: str = "12",
    tenant: int = TENANT,
    owner_doc: str | None = None,
) -> uuid.UUID:
    pid = await _flat(s, db, house, owner_doc)
    lease, invite = await s.leases.create_lease(
        OWNER,
        LeaseCreate(
            property_id=pid,
            start_date=date(2026, 12, 1),
            months=11,
            rent_amount=18000,
            rent_due_day=5,
            deposit_amount=18000,
            pay_method=PayMethod.CARD,
            pay_details=PayDetailsIn(card=CARD, recipient_name="Ірина Коваль"),
        ),
    )
    await s.leases.accept_invite(tenant, invite.token)
    await s.leases.accept_terms(tenant, lease.id)
    return lease.id


async def _generated(s: Services, db: FakeDb, **kw: object) -> tuple[uuid.UUID, uuid.UUID, bytes]:
    lease_id = await _agreed_lease(s, db, **kw)  # type: ignore[arg-type]
    tenant = kw.get("tenant", TENANT)
    await s.contracts.set_legal_name(OWNER, lease_id, OWNER_NAME)
    await s.contracts.set_legal_name(tenant, lease_id, TENANT_NAME)  # type: ignore[arg-type]
    state = await s.contracts.generate(OWNER, lease_id)
    assert state.contract is not None
    _, pdf = await s.contracts.document(OWNER, state.contract.id)
    return lease_id, state.contract.id, pdf


async def _approve_all(s: Services, db: FakeDb) -> None:
    for case in await s.contracts.review_queue(ADMIN_TG):
        await s.contracts.review_signature(ADMIN_TG, case.signature_id, True, None)


# ------------------------------------------------------------- формування


async def test_generate_needs_agreed_terms_and_names(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _agreed_lease(s, db)
    state = await s.contracts.state(OWNER, lease_id)
    assert state.can_generate and state.needs_names and state.contract is None
    with pytest.raises(InvalidInput):
        await s.contracts.generate(OWNER, lease_id)
    with pytest.raises(PermissionDenied):
        await s.contracts.generate(TENANT, lease_id)
    await s.contracts.set_legal_name(OWNER, lease_id, OWNER_NAME)
    await s.contracts.set_legal_name(TENANT, lease_id, TENANT_NAME)
    state = await s.contracts.generate(OWNER, lease_id)
    assert state.contract is not None and state.contract.status is ContractStatus.SENT
    assert not state.needs_names and not state.stale
    # Текст договору: сторони, адреса, строк, піднайм заборонено, без реквізитів картки.
    html = db.pdf.last_html or ""
    assert OWNER_NAME in html and TENANT_NAME in html
    assert "вул. Шевченка" in html and "18 000" in html and "«01» грудня 2026" in html
    assert "заборонений" in html and "4111" not in html
    assert any(n.payload.get("event") == "contract_ready" for n in db.prop.notifications)


async def test_regenerate_voids_previous(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id, first, _ = await _generated(s, db)
    state = await s.contracts.generate(OWNER, lease_id)
    assert state.contract is not None and state.contract.id != first
    assert state.contract.version == 2
    assert db.contract.contracts[first].status is ContractStatus.VOID


async def test_pdf_failure_is_service_unavailable(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _agreed_lease(s, db)
    await s.contracts.set_legal_name(OWNER, lease_id, OWNER_NAME)
    await s.contracts.set_legal_name(TENANT, lease_id, TENANT_NAME)
    db.pdf.fail = True
    with pytest.raises(ServiceUnavailable):
        await s.contracts.generate(OWNER, lease_id)


async def test_conditions_change_makes_contract_stale(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id, contract_id, pdf = await _generated(s, db)
    lease = await s.leases.get(OWNER, lease_id)
    await s.leases.update_conditions(
        OWNER,
        lease_id,
        LeaseCreate(
            property_id=lease.property_id,
            start_date=lease.start_date,
            months=11,
            rent_amount=19000,
            rent_due_day=5,
            pay_method=PayMethod.CASH,
        ),
    )
    state = await s.contracts.state(OWNER, lease_id)
    assert state.stale and not state.can_generate  # спершу орендар погоджує нові умови
    with pytest.raises(Conflict):
        await s.contracts.upload_signature(TENANT, contract_id, "a.p7s", cades(pdf, TENANT_NAME))
    await s.leases.accept_terms(TENANT, lease_id)
    assert (await s.contracts.state(OWNER, lease_id)).can_generate


# ---------------------------------------------------------------- підписи


async def test_full_signing_with_moderator(env: tuple[Services, FakeDb]) -> None:
    """ADR 0010: підпис зараховує модератор; обидва прийнято → оренда active."""
    s, db = env
    lease_id, contract_id, pdf = await _generated(s, db, owner_doc=OWNER_NAME)
    state = await s.contracts.upload_signature(
        TENANT, contract_id, "dogovir.pdf.p7s", cades(pdf, TENANT_NAME.upper())
    )
    me = next(p for p in state.parties if p.is_me)
    assert me.signature is not None and me.signature.state == "review"
    assert state.contract is not None and state.contract.status is ContractStatus.PARTIALLY_SIGNED
    sig = next(iter(db.contract.signatures.values()))
    assert sig.check_status is SignatureCheck.AUTO_OK
    assert sig.signer_tax_id_hash == identifier_hash(FAKE_TAX_ID, TEST_HMAC)
    assert FAKE_TAX_ID not in str(sig.check_details)  # РНОКПП лише як хеш
    await s.contracts.upload_signature(OWNER, contract_id, "x.asice", asice(pdf, OWNER_NAME))
    assert (await s.leases.get(OWNER, lease_id)).status is LeaseStatus.AWAITING_SIGNATURES

    queue = await s.contracts.review_queue(ADMIN_TG)
    assert {c.party_role for c in queue} == {"landlord", "tenant"}
    with pytest.raises(PermissionDenied):
        await s.contracts.review_queue(TENANT)
    sent = await s.contracts.send_review_files(ADMIN_TG, queue[0].signature_id)
    assert sent == 2 and len(db.notifier.documents) == 2
    await _approve_all(s, db)

    assert (await s.leases.get(TENANT, lease_id)).status is LeaseStatus.ACTIVE
    state = await s.contracts.state(TENANT, lease_id)
    assert state.contract is not None and state.contract.status is ContractStatus.SIGNED
    name, data = await s.contracts.package(TENANT, contract_id)
    files = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert name.endswith(".zip") and len(files) == 3
    assert any(n.payload.get("event") == "lease_active" for n in db.prop.notifications)


async def test_auto_accept_without_moderator(clock: FixedClock) -> None:
    s, db = await _env(
        clock, contracts_config=ContractsConfig(hmac_secret=TEST_HMAC, require_moderator=False)
    )
    lease_id, contract_id, pdf = await _generated(s, db)
    await s.contracts.upload_signature(TENANT, contract_id, "t.p7s", cades(pdf, TENANT_NAME))
    await s.contracts.upload_signature(OWNER, contract_id, "o.p7s", cades(pdf, OWNER_NAME))
    assert (await s.leases.get(OWNER, lease_id)).status is LeaseStatus.ACTIVE


async def test_signature_errors(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id, contract_id, pdf = await _generated(s, db)
    cases = [
        (b"not a signature", "unreadable"),
        (b"%PDF-1.7 signed inside", "pades"),
        (cades(b"%PDF other file", TENANT_NAME), "other_document"),
        (cades(pdf, "ТОВ Ромашка", tax_id=None, organization="ТОВ Ромашка"), "legal_entity"),
    ]
    for data, reason in cases:
        with pytest.raises(InvalidInput) as exc:
            await s.contracts.upload_signature(TENANT, contract_id, "a.p7s", data)
        assert exc.value.details["reason"] == reason
    with pytest.raises(InvalidInput):
        await s.contracts.upload_signature(TENANT, contract_id, "a.p7s", b"x" * (6 * 1024 * 1024))
    with pytest.raises(PermissionDenied):  # співмешканець не підписує
        co = await s.leases.new_invite(TENANT, lease_id, MemberRole.CO_TENANT)
        await s.leases.accept_invite(ROOMMATE, co.token)
        await s.contracts.upload_signature(
            ROOMMATE, contract_id, "a.p7s", cades(pdf, "Оля Іванова")
        )
    assert not db.contract.signatures


async def test_name_mismatch_goes_to_moderator(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, contract_id, pdf = await _generated(s, db, owner_doc=OWNER_NAME)
    await s.contracts.upload_signature(OWNER, contract_id, "o.p7s", cades(pdf, "Сидоренко Олег"))
    sig = next(iter(db.contract.signatures.values()))
    assert sig.check_status is SignatureCheck.MISMATCH
    assert any(r[1] == "signature_mismatch" for r in db.risk_signals)
    case = (await s.contracts.review_queue(ADMIN_TG))[0]
    assert case.expected_name == OWNER_NAME and case.owner_name_in_doc == OWNER_NAME
    assert (case.contract_name_match, case.owner_doc_match) == (False, False)
    with pytest.raises(InvalidInput):  # відхилення — лише з причиною
        await s.contracts.review_signature(ADMIN_TG, case.signature_id, False, " ")
    await s.contracts.review_signature(ADMIN_TG, case.signature_id, False, "Не той підписувач")
    state = await s.contracts.state(OWNER, case.lease_id)
    me = next(p for p in state.parties if p.is_me)
    assert me.signature is not None and me.signature.state == "rejected" and state.can_sign
    assert any(n.payload.get("event") == "signature_rejected" for n in db.prop.notifications)
    # Повторне завантаження замінює відхилений підпис.
    await s.contracts.upload_signature(OWNER, contract_id, "o2.p7s", cades(pdf, OWNER_NAME))
    assert len(db.contract.signatures) == 1


async def test_banned_tax_id_cannot_sign(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    _, contract_id, pdf = await _generated(s, db)
    async with NullTxManager().begin() as tx:
        await s.moderation.ban(
            tx,
            BanIdentifiers(identity_hash=identifier_hash(FAKE_TAX_ID, TEST_HMAC)),
            reason="ріелтор",
            user_id=None,
            actor_id=None,
        )
    with pytest.raises(Banned):
        await s.contracts.upload_signature(TENANT, contract_id, "t.p7s", cades(pdf, TENANT_NAME))
    assert any(r[1] == "banned_signer" for r in db.risk_signals)


async def test_tenant_limit_by_tax_id(clock: FixedClock) -> None:
    """FR-LSE-8 з M4: третя оренда з тим самим РНОКПП блокується, навіть з іншого акаунта."""
    s, db = await _env(clock)
    for house in ("1", "2"):
        _, cid, pdf = await _generated(s, db, house=house)
        await s.contracts.upload_signature(TENANT, cid, "t.p7s", cades(pdf, TENANT_NAME))
    _, cid, pdf = await _generated(s, db, house="3", tenant=OTHER_TENANT)
    with pytest.raises(LeaseLimitReached):
        await s.contracts.upload_signature(OTHER_TENANT, cid, "t.p7s", cades(pdf, TENANT_NAME))


# ------------------------------------------------------- поза Домовиком


async def test_external_contract(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _agreed_lease(s, db)
    with pytest.raises(InvalidInput):
        await s.contracts.upload_external(OWNER, lease_id, "a.pdf", b"not a pdf")
    state = await s.contracts.upload_external(OWNER, lease_id, "Наш договір.pdf", b"%PDF-1.4 ok")
    assert state.contract is not None and state.contract.source.value == "uploaded_external"
    contract_id = state.contract.id
    with pytest.raises(Conflict):  # зовнішній договір підтверджують, а не підписують
        await s.contracts.upload_signature(
            TENANT, contract_id, "t.p7s", cades(b"%PDF-1.4 ok", TENANT_NAME)
        )
    with pytest.raises(PermissionDenied):
        await s.contracts.decide_external(OWNER, contract_id, True)
    tenant_view = await s.contracts.state(TENANT, lease_id)
    assert tenant_view.awaiting_my_confirmation
    await s.contracts.decide_external(TENANT, contract_id, False)
    assert db.contract.contracts[contract_id].status is ContractStatus.VOID
    state = await s.contracts.upload_external(OWNER, lease_id, "dogovir.pdf", b"%PDF-1.4 ok2")
    assert state.contract is not None
    await s.contracts.decide_external(TENANT, state.contract.id, True)
    assert (await s.leases.get(OWNER, lease_id)).status is LeaseStatus.ACTIVE


# ------------------------------------------------------------ нагадування


async def test_reminders(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    _, contract_id, pdf = await _generated(s, db)
    assert await s.contracts.remind_unsigned() == 0
    await s.contracts.upload_signature(TENANT, contract_id, "t.p7s", cades(pdf, TENANT_NAME))
    clock.advance(timedelta(hours=25))
    assert await s.contracts.remind_unsigned() == 1  # лише власнику
    assert await s.contracts.remind_unsigned() == 0  # без дублів
    clock.advance(timedelta(hours=48))
    assert await s.contracts.remind_unsigned() == 1


# ------------------------------------------------------- справжній PDF


async def test_real_pdf_render() -> None:
    """WeasyPrint з кирилицею. Без Pango (немає системних бібліотек) — пропускаємо."""
    import os

    renderer = WeasyPrintRenderer(os.environ.get("WEASYPRINT_DLL_DIRECTORIES", ""))
    snapshot = {
        "number": "20261107-ABCDEF12",
        "template_code": "lease_v1",
        "version": 1,
        "generated_on": "2026-11-07",
        "city": "Львів",
        "street": "вул. Шевченка",
        "house_number": "12",
        "apartment": "5",
        "kind": "apartment",
        "unit_kind": "entire",
        "unit_name": "",
        "area": "54",
        "rooms": 2,
        "landlord_name": OWNER_NAME,
        "tenant_name": TENANT_NAME,
        "start_date": "2026-12-01",
        "end_date": "2027-10-31",
        "months": 11,
        "rent_amount": 18000,
        "currency": "UAH",
        "rent_due_day": 5,
        "deposit_amount": 18000,
        "pay_method": "card",
        "terms": {"sublet_allowed": False},
        "co_tenants": [],
    }
    try:
        pdf = await renderer.render(render_contract(snapshot))
    except Exception as exc:  # PdfError: немає Pango на цій машині
        pytest.skip(f"WeasyPrint недоступний: {exc}")
    assert pdf.startswith(b"%PDF-") and len(pdf) > 5000


async def test_attached_cades_is_default_of_diia(env: tuple[Services, FakeDb]) -> None:
    """Сервіс підписання Дії за замовчуванням: CAdES, «підпис та дані в одному файлі»."""
    s, db = env
    _, contract_id, pdf = await _generated(s, db)
    await s.contracts.upload_signature(
        TENANT, contract_id, "dogovir.pdf.p7s", cades(pdf, TENANT_NAME, detached=False)
    )
    sig = next(iter(db.contract.signatures.values()))
    assert sig.check_status is SignatureCheck.AUTO_OK
    assert sig.check_details["document_match"] == "match"
    with pytest.raises(InvalidInput):  # вкладено інший документ
        await s.contracts.upload_signature(
            OWNER, contract_id, "x.p7s", cades(b"%PDF other", OWNER_NAME, detached=False)
        )


async def test_cades_x_long_attributes_do_not_break_parsing(env: tuple[Services, FakeDb]) -> None:
    """Регресія: справжній підпис Дії (CAdES-X Long) мав атрибут, на якому падав розбір."""
    s, db = env
    _, contract_id, pdf = await _generated(s, db)
    data = with_unparseable_signed_attr(cades(pdf, TENANT_NAME, detached=False))
    await s.contracts.upload_signature(TENANT, contract_id, "dogovir.pdf.p7s", data)
    sig = next(iter(db.contract.signatures.values()))
    assert sig.check_status is SignatureCheck.AUTO_OK and sig.signer_name == TENANT_NAME


async def test_purged_lease_removes_contract_files(env: tuple[Services, FakeDb]) -> None:
    """FR-LSE-9: повне видалення скасованої оренди прибирає PDF і файли підписів."""
    s, db = env
    lease_id, contract_id, pdf = await _generated(s, db)
    await s.contracts.upload_signature(TENANT, contract_id, "t.p7s", cades(pdf, TENANT_NAME))
    keys = [k for k in db.storage.files if k.startswith(f"contracts/{lease_id}/")]
    assert len(keys) == 2
    await s.leases.cancel(OWNER, lease_id)
    await s.leases.hide(OWNER, lease_id)
    await s.leases.hide(TENANT, lease_id)
    assert not [k for k in db.storage.files if k.startswith(f"contracts/{lease_id}/")]


async def test_mismatch_only_with_ownership_doc(env: tuple[Services, FakeDb]) -> None:
    """ПІБ збігся з договором, але не з довідкою (помилка модератора) — видно, з чим саме."""
    s, db = env
    _, contract_id, pdf = await _generated(s, db, owner_doc="Інше Прізвище")
    await s.contracts.upload_signature(OWNER, contract_id, "o.p7s", cades(pdf, OWNER_NAME))
    sig = next(iter(db.contract.signatures.values()))
    assert sig.check_status is SignatureCheck.MISMATCH
    assert sig.check_details["contract_name_match"] is True
    assert sig.check_details["owner_doc_match"] is False
    case = (await s.contracts.review_queue(ADMIN_TG))[0]
    assert (case.contract_name_match, case.owner_doc_match) == (True, False)


async def test_preview_draft_then_real_file(env: tuple[Services, FakeDb]) -> None:
    """Перегляд у застосунку: до формування — чернетка з умов; після — той самий PDF."""
    s, db = env
    lease_id = await _agreed_lease(s, db)
    _, data, draft = await s.contracts.preview(TENANT, lease_id)
    assert draft and data.startswith(b"%PDF") and "ЧЕРНЕТКА" in (db.pdf.last_html or "")
    await s.contracts.set_legal_name(OWNER, lease_id, OWNER_NAME)
    await s.contracts.set_legal_name(TENANT, lease_id, TENANT_NAME)
    state = await s.contracts.generate(OWNER, lease_id)
    assert state.contract is not None
    _, real = await s.contracts.document(OWNER, state.contract.id)
    _, data, draft = await s.contracts.preview(TENANT, lease_id)
    assert not draft and data == real
