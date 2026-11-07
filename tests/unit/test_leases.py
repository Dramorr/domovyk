"""Оренда за запрошенням, учасники, умови, чат, сповіщення (M3, FR-LSE-1..5, FR-CHT-1)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from domovyk.container import Services
from domovyk.core.clock import FixedClock
from domovyk.core.errors import (
    Conflict,
    HasOpenLease,
    InvalidInput,
    InviteInvalid,
    NotFound,
    OwnershipNotVerified,
    PermissionDenied,
)
from domovyk.domain.leases import (
    LeaseStatus,
    MemberRole,
    PayMethod,
    add_months,
    end_for_term,
    iban_ok,
    luhn_ok,
    months_between,
    validate_term,
)
from domovyk.domain.properties import OwnershipStatus, PropertyKind
from domovyk.modules.leases.schemas import LeaseCreate, LeaseTerms, PayDetailsIn
from domovyk.modules.notifications.schemas import LeaseNotificationPrefs
from domovyk.modules.properties.schemas import AddressIn, PropertyCreate, RoomIn
from tests.fakes import PRIVACY_V, TERMS_V, FakeDb, make_fake_services

OWNER, TENANT, ROOMMATE, STRANGER = 42, 43, 44, 45
CARD = "4111 1111 1111 1111"  # тестовий номер (проходить Luhn), не справжня картка
IBAN = "UA213223130000026007233566001"  # приклад IBAN з документації, вигаданий


# ------------------------------------------------------------------ домен


def test_term_math() -> None:
    assert end_for_term(date(2026, 12, 1), 11) == date(2027, 10, 31)
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert months_between(date(2026, 12, 1), date(2027, 10, 31)) == 10
    with pytest.raises(InvalidInput):
        end_for_term(date(2026, 12, 1), 36)
    with pytest.raises(InvalidInput):  # правило 6: понад 35 місяців — нотаріус
        validate_term(date(2026, 1, 1), date(2029, 1, 1))
    validate_term(date(2026, 1, 1), date(2028, 12, 1))


def test_payment_checksums() -> None:
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert iban_ok(IBAN)
    assert not iban_ok(IBAN[:-1] + "2")


# ------------------------------------------------------------------ сервіс


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 6, 12, tzinfo=UTC))  # 14:00 за Києвом


@pytest.fixture
async def env(clock: FixedClock) -> tuple[Services, FakeDb]:
    s, db = make_fake_services(clock=clock)
    for tg, name in ((OWNER, "Ірина"), (TENANT, "Андрій"), (ROOMMATE, "Оля"), (STRANGER, "Іван")):
        await s.users.register(tg, name, terms_version=TERMS_V, privacy_version=PRIVACY_V)
        await s.users.add_phone(tg, f"+38067{tg:07d}")
    return s, db


async def _verified_flat(s: Services, db: FakeDb) -> uuid.UUID:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Шевченка", house_number="12"),
            apartment="5",
            rooms=2,
            area=Decimal("54"),
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    return prop.id


def _lease(pid: uuid.UUID, **kw: object) -> LeaseCreate:
    data: dict[str, object] = {
        "property_id": pid,
        "start_date": date(2026, 12, 1),
        "months": 11,
        "rent_amount": 18000,
        "rent_due_day": 5,
        "deposit_amount": 18000,
        "pay_method": PayMethod.CARD,
        "pay_details": PayDetailsIn(card=CARD, recipient_name="Ірина Коваль"),
    }
    data.update(kw)
    return LeaseCreate.model_validate(data)


async def _joined(s: Services, db: FakeDb) -> uuid.UUID:
    pid = await _verified_flat(s, db)
    lease, invite = await s.leases.create_lease(OWNER, _lease(pid))
    await s.leases.accept_invite(TENANT, invite.token)
    return lease.id


async def test_create_requires_verified_ownership(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _verified_flat(s, db)
    db.prop.properties[pid].ownership = OwnershipStatus.PENDING
    with pytest.raises(OwnershipNotVerified):
        await s.leases.create_lease(OWNER, _lease(pid))


async def test_create_masks_and_encrypts_pay_details(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _verified_flat(s, db)
    lease, invite = await s.leases.create_lease(OWNER, _lease(pid))
    assert lease.status is LeaseStatus.AWAITING_TENANT
    assert lease.end_date == date(2027, 10, 31) and lease.months_total == 11
    assert (lease.pay.masked, lease.pay.recipient) == ("**** 1111", "Ірина К.")
    assert lease.terms == LeaseTerms()
    stored = db.lease.leases[lease.id]
    assert "4111" not in json.dumps(stored.pay_details)
    assert stored.pay_details_enc is not None
    assert json.loads(db.cipher.decrypt(stored.pay_details_enc))["card"] == "4111111111111111"
    # У базі лише хеш токена.
    assert invite.token not in {i.token_hash for i in db.lease.invites}
    assert invite.expires_at == datetime(2026, 11, 13, 12, tzinfo=UTC)


async def test_validation(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _verified_flat(s, db)
    bad_card = PayDetailsIn(card="4111 1111 1111 1112", recipient_name="Ірина Коваль")
    with pytest.raises(InvalidInput) as exc:
        await s.leases.create_lease(OWNER, _lease(pid, pay_details=bad_card))
    assert exc.value.details["field"] == "card"
    with pytest.raises(InvalidInput):
        await s.leases.create_lease(OWNER, _lease(pid, rent_due_day=31))
    iban = PayDetailsIn(iban=IBAN, recipient_name="Ірина Коваль")
    lease, _ = await s.leases.create_lease(
        OWNER, _lease(pid, pay_method=PayMethod.IBAN, pay_details=iban)
    )
    assert lease.pay.masked == "UA** **** 6001"


async def test_unit_conflicts(env: tuple[Services, FakeDb]) -> None:
    """FR-PRP-3: весь об'єкт і кімнату одночасно не здаєш."""
    s, db = env
    pid = await _verified_flat(s, db)
    prop = await s.properties.add_room(OWNER, pid, RoomIn(name="Мала"))
    room = next(u for u in prop.units if u.kind == "room")
    await s.leases.create_lease(OWNER, _lease(pid, unit_id=room.id))
    with pytest.raises(Conflict):
        await s.leases.create_lease(OWNER, _lease(pid))  # весь — коли кімната здана
    with pytest.raises(Conflict):
        await s.leases.create_lease(OWNER, _lease(pid, unit_id=room.id))


async def test_invite_flow_and_terms(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _verified_flat(s, db)
    lease, invite = await s.leases.create_lease(OWNER, _lease(pid))

    preview = await s.leases.preview_invite(TENANT, invite.token)
    assert (
        preview.valid
        and preview.landlord_name == "Ірина"
        and preview.role is MemberRole.RESPONSIBLE
    )
    assert (await s.leases.preview_invite(OWNER, invite.token)).reason == "own_lease"
    with pytest.raises(InviteInvalid):
        await s.leases.accept_invite(OWNER, invite.token)

    joined = await s.leases.accept_invite(TENANT, f"inv_{invite.token}")
    assert joined.my_role == "responsible"
    assert joined.unread == 0  # системні повідомлення до приходу — не «нові»
    assert [m.role for m in joined.members] == ["landlord", "responsible"]
    with pytest.raises(InviteInvalid):  # одноразове
        await s.leases.accept_invite(STRANGER, invite.token)

    accepted = await s.leases.accept_terms(TENANT, lease.id)
    assert accepted.status is LeaseStatus.AWAITING_SIGNATURES and accepted.terms_accepted_at

    # Власник змінив умови — погоджувати знову.
    changed = await s.leases.update_conditions(OWNER, lease.id, _lease(pid, rent_amount=19000))
    assert changed.status is LeaseStatus.AWAITING_TENANT and changed.terms_accepted_at is None
    assert changed.rent_amount == 19000

    # FR-LSE-10: власник, змінивши умови, уже їх погодив; повторно — без змін стану.
    assert changed.agreement.landlord == "agreed" and changed.agreement.tenant == "none"
    with pytest.raises(NotFound):  # чужа оренда виглядає як неіснуюча
        await s.leases.get(STRANGER, lease.id)


async def test_expired_invite(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    pid = await _verified_flat(s, db)
    _, invite = await s.leases.create_lease(OWNER, _lease(pid))
    clock.advance(timedelta(days=8))
    assert (await s.leases.preview_invite(TENANT, invite.token)).reason == "expired"
    with pytest.raises(InviteInvalid):
        await s.leases.accept_invite(TENANT, invite.token)


async def test_new_invite_revokes_old(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    pid = await _verified_flat(s, db)
    lease, first = await s.leases.create_lease(OWNER, _lease(pid))
    second = await s.leases.new_invite(OWNER, lease.id, MemberRole.RESPONSIBLE)
    assert (await s.leases.preview_invite(TENANT, first.token)).reason == "revoked"
    await s.leases.accept_invite(TENANT, second.token)
    with pytest.raises(Conflict):
        await s.leases.new_invite(OWNER, lease.id, MemberRole.RESPONSIBLE)


async def test_co_tenant(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _joined(s, db)
    invite = await s.leases.new_invite(TENANT, lease_id, MemberRole.CO_TENANT)
    out = await s.leases.accept_invite(ROOMMATE, invite.token)
    assert out.my_role == "co_tenant" and not out.can_invite_co_tenant and out.pending_invites == []
    with pytest.raises(PermissionDenied):
        await s.leases.new_invite(ROOMMATE, lease_id, MemberRole.CO_TENANT)
    await s.leases.decline(ROOMMATE, lease_id)
    with pytest.raises(NotFound):
        await s.leases.get(ROOMMATE, lease_id)
    roles = [m.role for m in (await s.leases.get(OWNER, lease_id)).members]
    assert roles == ["landlord", "responsible"]


async def test_responsible_declines_before_signing(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _joined(s, db)
    await s.leases.accept_terms(TENANT, lease_id)
    await s.leases.decline(TENANT, lease_id)
    lease = await s.leases.get(OWNER, lease_id)
    assert lease.status is LeaseStatus.AWAITING_TENANT
    assert [m.role for m in lease.members] == ["landlord"]
    await s.leases.new_invite(OWNER, lease_id, MemberRole.RESPONSIBLE)  # слот вільний


async def test_chat_and_notifications(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _joined(s, db)
    await s.leases.post_message(TENANT, lease_id, "Добрий день! Коли можна заїхати?")
    await s.leases.post_message(TENANT, lease_id, "І ще: чи є пральна машина?")

    owner_view = await s.leases.get(OWNER, lease_id)
    assert owner_view.unread >= 2
    page = await s.leases.messages(OWNER, lease_id)
    texts = [m.body for m in page.messages if m.body]
    assert texts[-2:] == ["Добрий день! Коли можна заїхати?", "І ще: чи є пральна машина?"]
    assert page.messages[0].system_code == "lease_created"
    await s.leases.mark_read(OWNER, lease_id, page.messages[-1].id)
    assert (await s.leases.get(OWNER, lease_id)).unread == 0

    owner = await s.users.find_by_telegram(OWNER)
    assert owner is not None
    chat_notes = [
        n for n in db.prop.notifications if n.type == "chat_message" and n.user_id == owner.id
    ]
    assert len(chat_notes) == 1  # друге за ті ж 10 хв — без нового сповіщення
    assert chat_notes[0].payload["sender"] == "Андрій"

    with pytest.raises(NotFound):
        await s.leases.post_message(STRANGER, lease_id, "Привіт")
    with pytest.raises(InvalidInput):
        await s.leases.post_message(TENANT, lease_id, "   ")


async def test_prefs_and_quiet_hours(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    s, db = env
    lease_id = await _joined(s, db)
    owner = await s.users.find_by_telegram(OWNER)
    assert owner is not None

    await s.leases.set_notification_prefs(
        OWNER, lease_id, LeaseNotificationPrefs(chat_message=False)
    )
    await s.leases.post_message(TENANT, lease_id, "Тиша?")
    assert not [
        n for n in db.prop.notifications if n.type == "chat_message" and n.user_id == owner.id
    ]

    await s.leases.set_notification_prefs(OWNER, lease_id, LeaseNotificationPrefs())
    clock.advance(timedelta(hours=9))  # 23:00 за Києвом — тихі години
    await s.leases.post_message(TENANT, lease_id, "Вибач, що пізно")
    (note,) = [
        n for n in db.prop.notifications if n.type == "chat_message" and n.user_id == owner.id
    ]
    assert note.payload["_silent"] is True
    prefs = await s.leases.notification_prefs(OWNER, lease_id)
    assert (prefs.quiet_from, prefs.quiet_to) == (22, 8)


async def test_cancel_and_delete_guards(env: tuple[Services, FakeDb]) -> None:
    s, db = env
    lease_id = await _joined(s, db)
    pid = (await s.leases.get(OWNER, lease_id)).property_id
    with pytest.raises(HasOpenLease):
        await s.users.delete_account(TENANT)
    with pytest.raises(HasOpenLease):
        await s.properties.delete_property(OWNER, pid)
    with pytest.raises(PermissionDenied):
        await s.leases.cancel(TENANT, lease_id)
    cancelled = await s.leases.cancel(OWNER, lease_id)
    assert cancelled.status is LeaseStatus.CANCELLED
    with pytest.raises(Conflict):
        await s.leases.post_message(TENANT, lease_id, "Ой")
    await s.users.delete_account(TENANT)  # тепер можна
    await s.properties.delete_property(OWNER, pid)


async def test_tenant_lease_limit(env: tuple[Services, FakeDb]) -> None:
    """FR-LSE-8: відповідальним — до двох живих оренд; співмешканцем — без обмежень."""
    from domovyk.core.errors import LeaseLimitReached

    s, db = env
    pid = await _verified_flat(s, db)
    prop = await s.properties.add_room(OWNER, pid, RoomIn(name="Перша"))
    prop = await s.properties.add_room(OWNER, pid, RoomIn(name="Друга"))
    rooms = [u.id for u in prop.units if u.kind == "room"]
    # Три окремі юніти — три оренди, де TENANT міг би бути відповідальним.
    first, i1 = await s.leases.create_lease(OWNER, _lease(pid, unit_id=rooms[0]))
    _second, i2 = await s.leases.create_lease(OWNER, _lease(pid, unit_id=rooms[1]))
    third_pid = await _verified_flat_other(s, db)
    third, i3 = await s.leases.create_lease(OWNER, _lease(third_pid))
    await s.leases.accept_invite(TENANT, i1.token)
    await s.leases.accept_invite(TENANT, i2.token)

    assert (await s.leases.preview_invite(TENANT, i3.token)).reason == "lease_limit"
    with pytest.raises(LeaseLimitReached) as exc:
        await s.leases.accept_invite(TENANT, i3.token)
    assert exc.value.details["limit"] == 2

    # Співмешканцем — можна.
    await s.leases.accept_invite(ROOMMATE, i3.token)
    co = await s.leases.new_invite(ROOMMATE, third.id, MemberRole.CO_TENANT)
    await s.leases.accept_invite(TENANT, co.token)

    # Скасована оренда звільняє місце.
    await s.leases.cancel(OWNER, first.id)
    _lease4, i4 = await s.leases.create_lease(OWNER, _lease(pid, unit_id=rooms[0]))
    await s.leases.accept_invite(TENANT, i4.token)


async def _verified_flat_other(s: Services, db: FakeDb) -> uuid.UUID:
    prop = await s.properties.create_property(
        OWNER,
        PropertyCreate(
            kind=PropertyKind.APARTMENT,
            address=AddressIn(street="вул. Зелена", house_number="40"),
            apartment="7",
            rooms=1,
        ),
    )
    db.prop.properties[prop.id].ownership = OwnershipStatus.VERIFIED
    return prop.id


async def test_hide_and_purge_cancelled(env: tuple[Services, FakeDb], clock: FixedClock) -> None:
    """FR-LSE-9: «Видалити» — лише скасовану й лише у себе; стирається, коли прибрали всі."""
    s, db = env
    pid = await _verified_flat(s, db)
    lease, invite = await s.leases.create_lease(OWNER, _lease(pid))
    await s.leases.accept_invite(TENANT, invite.token)
    with pytest.raises(Conflict):
        await s.leases.hide(OWNER, lease.id)
    await s.leases.cancel(OWNER, lease.id)
    await s.leases.hide(OWNER, lease.id)
    assert await s.leases.list_mine(OWNER) == []
    assert len(await s.leases.list_mine(TENANT)) == 1  # в орендаря лишилась
    await s.leases.hide(TENANT, lease.id)
    assert lease.id not in db.lease.leases  # прибрали всі → стерто

    # Ніхто не прибрав — стирається через строк зберігання.
    lease2, _ = await s.leases.create_lease(OWNER, _lease(pid))
    await s.leases.cancel(OWNER, lease2.id)
    assert await s.leases.purge_cancelled() == 0
    clock.advance(timedelta(days=31))
    assert await s.leases.purge_cancelled() == 1
    assert lease2.id not in db.lease.leases


async def test_proposal_accept_and_decline(env: tuple[Services, FakeDb]) -> None:
    """FR-LSE-10: орендар пропонує зміни → обидві згоди знімаються; власник погоджує чи ні."""
    from domovyk.modules.leases.schemas import ProposalIn

    s, db = env
    pid = await _verified_flat(s, db)
    lease, invite = await s.leases.create_lease(OWNER, _lease(pid))
    await s.leases.accept_invite(TENANT, invite.token)
    view = await s.leases.get(TENANT, lease.id)
    assert (view.agreement.tenant, view.agreement.landlord) == ("none", "agreed")
    await s.leases.accept_terms(TENANT, lease.id)
    assert (await s.leases.get(OWNER, lease.id)).status is LeaseStatus.AWAITING_SIGNATURES

    proposal = ProposalIn(
        start_date=date(2027, 1, 1), months=12, rent_amount=16000, rent_due_day=10
    )
    with pytest.raises(PermissionDenied):
        await s.leases.propose_changes(OWNER, lease.id, proposal)
    view = await s.leases.propose_changes(TENANT, lease.id, proposal)
    assert view.status is LeaseStatus.AWAITING_TENANT
    assert (view.agreement.tenant, view.agreement.landlord) == ("proposed", "none")
    assert view.proposal is not None and view.proposal.rent_amount == 16000
    assert view.rent_amount == 18000  # умови ще старі
    with pytest.raises(Conflict):  # спершу відповідь на пропозицію
        await s.leases.accept_terms(OWNER, lease.id)

    # Власник відхиляє → його половина «відхилив», обидва погоджують старі умови знову.
    view = await s.leases.respond_proposal(OWNER, lease.id, False)
    assert view.proposal is None and view.rent_amount == 18000
    assert (view.agreement.tenant, view.agreement.landlord) == ("none", "declined")
    await s.leases.accept_terms(OWNER, lease.id)
    view = await s.leases.accept_terms(TENANT, lease.id)
    assert view.status is LeaseStatus.AWAITING_SIGNATURES

    # Нова пропозиція — власник погоджує: умови оновлено, обидві сторони погодили.
    await s.leases.propose_changes(TENANT, lease.id, proposal)
    view = await s.leases.respond_proposal(OWNER, lease.id, True)
    assert view.status is LeaseStatus.AWAITING_SIGNATURES
    assert (view.rent_amount, view.rent_due_day, view.start_date) == (16000, 10, date(2027, 1, 1))
    assert (view.agreement.tenant, view.agreement.landlord) == ("agreed", "agreed")
    with pytest.raises(Conflict):
        await s.leases.respond_proposal(OWNER, lease.id, True)
