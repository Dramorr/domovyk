"""Публічний інтерфейс модуля payments: нарахування й облік оплат (M5, частина 1).

FR-PAY-1..6. Домовик гроші не проводить (правило 1): люди платять напряму, а тут —
нарахування, позначки «оплатив» (готівка чи квитанція) і підтвердження власника.
Розпізнавання квитанцій і автозарахування — частина 2.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from domovyk.adapters.notifier.base import Notifier, NotifierError
from domovyk.adapters.storage.base import FileStorage
from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.errors import (
    Conflict,
    InvalidInput,
    NotFound,
    PermissionDenied,
    ServiceUnavailable,
)
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.contracts import sha256_hex
from domovyk.domain.leases import LeaseStatus, PayMethod
from domovyk.domain.payments import (
    IN_FLIGHT,
    PAYMENT,
    RECEIPT_MAX_BYTES,
    RECEIPT_TYPES,
    ChargeKind,
    ChargeStatus,
    PaymentStatus,
    charge_status,
    clean_amount,
    clean_note,
    reminder_offset,
    rent_periods,
    sniff_receipt,
)
from domovyk.modules.chat.service import ChatService, SystemCode
from domovyk.modules.leases.service import LeaseForContract, LeasesService
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.payments.repository import (
    ChargeRecord,
    NewCharge,
    NewPayment,
    PaymentRecord,
    PaymentsRepository,
)
from domovyk.modules.payments.schemas import (
    CashIn,
    ChargeOut,
    DecisionIn,
    PayInfo,
    PaymentOut,
    PaymentsOverview,
)
from domovyk.modules.users.service import UsersService

__all__ = [
    "CashIn",
    "ChargeOut",
    "DecisionIn",
    "PayInfo",
    "PaymentOut",
    "PaymentsConfig",
    "PaymentsOverview",
    "PaymentsService",
]

log = logging.getLogger(__name__)

MAX_AMOUNT = Decimal("10000000")


@dataclass(frozen=True)
class PaymentsConfig:
    # FR-PAY-1: нарахування за N днів до дати; нагадування −3, 0, +1 день.
    generate_days_ahead: int = 7
    reminder_offsets: tuple[int, ...] = (-3, 0, 1)
    # FR-PAY-4: власник не відповів на «оплатив» — нагадати через N днів.
    pending_reminder_days: int = 2
    receipt_max_bytes: int = RECEIPT_MAX_BYTES


class PaymentsService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], PaymentsRepository],
        users: UsersService,
        leases: LeasesService,
        chat: ChatService,
        notifications: NotificationsService,
        storage: FileStorage,
        clock: Clock,
        config: PaymentsConfig,
        notifier: Notifier | None = None,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._leases = leases
        self._chat = chat
        self._notifications = notifications
        self._storage = storage
        self._clock = clock
        self._config = config
        self._notifier = notifier

    # -------------------------------------------------------------- читання

    async def overview(self, telegram_uid: int, lease_id: uuid.UUID) -> PaymentsOverview:
        info = await self._leases.for_contract(telegram_uid, lease_id)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            charges = await repo.charges(lease_id)
            payments = await repo.payments([c.id for c in charges])
            out = [await self._charge_out(c, payments) for c in charges]
        open_ = [
            c
            for c in out
            if c.status in (ChargeStatus.UPCOMING, ChargeStatus.DUE, ChargeStatus.OVERDUE)
            or (c.status is ChargeStatus.REPORTED and c.remaining > 0)
        ]
        next_charge = min(open_, key=lambda c: c.due_date) if open_ else None
        pending = sum(
            1 for c in out for p in c.payments if p.status is PaymentStatus.PENDING_LANDLORD
        )
        return PaymentsOverview(
            lease_id=lease_id,
            my_role=info.lease.my_role,
            pay_method=info.lease.pay.method,
            next_charge=next_charge,
            charges=out,
            awaiting_my_decision=pending if info.lease.my_role == "landlord" else 0,
        )

    async def pay_info(self, telegram_uid: int, charge_id: uuid.UUID) -> PayInfo:
        """PAY-REQ: сума, отримувач, повні реквізити — лише учасникам живої оренди."""
        charge = await self._charge(charge_id)
        info = await self._leases.for_contract(telegram_uid, charge.lease_id)
        if info.lease.status not in (LeaseStatus.ACTIVE, LeaseStatus.ENDING):
            raise Conflict("Оренда не активна", field="status")
        async with self._tx.begin() as tx:
            details = await self._leases.pay_requisites(tx, charge.lease_id)
            payments = await self._repo(tx).payments([charge.id])
        paid = _sum(payments, {PaymentStatus.CONFIRMED} | IN_FLIGHT)
        return PayInfo(
            charge_id=charge.id,
            kind=charge.kind,
            period_month=charge.period_month,
            amount=max(charge.amount - paid, Decimal(0)),
            method=info.lease.pay.method,
            card=details.get("card"),
            iban=details.get("iban"),
            jar_url=details.get("jar_url"),
            recipient=details.get("recipient_name"),
            street=info.lease.street,
            house_number=info.lease.house_number,
            apartment=info.lease.apartment,
        )

    # ------------------------------------------------------ орендар: «оплатив»

    async def report_cash(
        self, telegram_uid: int, charge_id: uuid.UUID, data: CashIn
    ) -> PaymentsOverview:
        """FR-PAY-4: «Оплатив готівкою» → власник підтверджує."""
        return await self._report(
            telegram_uid, charge_id, PayMethod.CASH, data.amount, data.note, None
        )

    async def report_receipt(
        self,
        telegram_uid: int,
        charge_id: uuid.UUID,
        amount: Decimal,
        filename: str,
        data: bytes,
        note: str | None = None,
    ) -> PaymentsOverview:
        """FR-PAY-3: квитанція (PDF або фото). Поки без розпізнавання — власник підтверджує."""
        if not data or len(data) > self._config.receipt_max_bytes:
            raise InvalidInput("Квитанція — PDF або фото до 10 МБ", field="file", reason="too_big")
        mime = sniff_receipt(data)
        if mime is None:
            raise InvalidInput("Квитанція — PDF або фото до 10 МБ", field="file", reason="bad_type")
        return await self._report(
            telegram_uid, charge_id, None, amount, note, (filename, data, mime)
        )

    async def _report(
        self,
        telegram_uid: int,
        charge_id: uuid.UUID,
        method: PayMethod | None,
        raw_amount: Decimal,
        raw_note: str | None,
        receipt: tuple[str, bytes, str] | None,
    ) -> PaymentsOverview:
        user = await self._users.require_active(telegram_uid)
        amount = clean_amount(raw_amount, MAX_AMOUNT)
        note = clean_note(raw_note)
        charge = await self._charge(charge_id)
        info = await self._leases.for_contract(telegram_uid, charge.lease_id)
        if info.lease.my_role == "landlord":
            raise PermissionDenied("Оплату позначає орендар; власник — «Отримав»")
        self._check_payable(info, charge)
        key = name = mime = sha = None
        if receipt is not None:
            name, data, mime = receipt
            sha = sha256_hex(data)
            async with self._tx.begin() as tx:
                if await self._repo(tx).receipt_exists(sha):
                    raise Conflict("Цю квитанцію вже надсилали", field="file", reason="duplicate")
            key = f"receipts/{charge.lease_id}/{uuid.uuid4()}{RECEIPT_TYPES[mime]}"
            await self._storage.put(key, data)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.add_payment(
                NewPayment(
                    charge_id=charge.id,
                    payer_user_id=user.id,
                    method=method or info.lease.pay.method,
                    amount=amount,
                    paid_at=now,
                    receipt_storage_key=key,
                    receipt_filename=_safe_name(name) if name else None,
                    receipt_mime=mime,
                    receipt_sha256=sha,
                    # Частина 2 розпізнаватиме PDF; поки — завжди на ручне підтвердження.
                    mismatches=[] if receipt is None else ["unparsed"],
                    note=note,
                    status=PaymentStatus.PENDING_LANDLORD,
                )
            )
            await self._recompute(repo, charge.id)
            await self._system(
                tx,
                charge.lease_id,
                SystemCode.PAYMENT_REPORTED,
                {"name": user.display_name, "period": charge.period_month.isoformat()},
            )
            await self._notify(
                tx,
                info,
                [info.landlord_user_id],
                "payment_reported",
                charge,
                {"name": user.display_name, "amount": _money(amount), "cash": receipt is None},
            )
        return await self.overview(telegram_uid, charge.lease_id)

    # ------------------------------------------------------ власник: рішення

    async def mark_received(
        self, telegram_uid: int, charge_id: uuid.UUID, data: CashIn
    ) -> PaymentsOverview:
        """FR-PAY-4: власник сам позначає «Отримав» (готівкою чи на картку) — одразу зараховано."""
        user = await self._users.require_active(telegram_uid)
        amount = clean_amount(data.amount, MAX_AMOUNT)
        charge = await self._charge(charge_id)
        info = await self._leases.for_contract(telegram_uid, charge.lease_id)
        if info.lease.my_role != "landlord":
            raise PermissionDenied("Позначає власник")
        self._check_payable(info, charge)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.add_payment(
                NewPayment(
                    charge_id=charge.id,
                    payer_user_id=info.responsible_user_id or user.id,
                    method=PayMethod.CASH,
                    amount=amount,
                    paid_at=now,
                    receipt_storage_key=None,
                    receipt_filename=None,
                    receipt_mime=None,
                    receipt_sha256=None,
                    mismatches=[],
                    note=clean_note(data.note),
                    status=PaymentStatus.CONFIRMED,
                    decided_by=user.id,
                    decided_at=now,
                )
            )
            await self._recompute(repo, charge.id)
            await self._system(
                tx,
                charge.lease_id,
                SystemCode.PAYMENT_CONFIRMED,
                {"period": charge.period_month.isoformat(), "amount": _money(amount)},
            )
            await self._notify(
                tx,
                info,
                [info.responsible_user_id],
                "payment_confirmed",
                charge,
                {"amount": _money(amount)},
            )
        return await self.overview(telegram_uid, charge.lease_id)

    async def decide(
        self, telegram_uid: int, payment_id: uuid.UUID, data: DecisionIn
    ) -> PaymentsOverview:
        """PAY: власник «Отримав» або «Не отримав» (з причиною) — FR-PAY-4, FR-PAY-6."""
        user = await self._users.require_active(telegram_uid)
        reason = " ".join((data.reason or "").split())[:500] or None
        if not data.accept and not reason:
            raise InvalidInput("Вкажи, що не так з оплатою", field="reason")
        async with self._tx.begin() as tx:
            payment = await self._repo(tx).payment(payment_id)
        if payment is None:
            raise NotFound("Оплату не знайдено")
        charge = await self._charge(payment.charge_id)
        info = await self._leases.for_contract(telegram_uid, charge.lease_id)
        if info.lease.my_role != "landlord":
            raise PermissionDenied("Оплату підтверджує власник")
        status = PaymentStatus.CONFIRMED if data.accept else PaymentStatus.REJECTED
        PAYMENT.transition(payment.status, status)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.update_payment(
                payment.id,
                status=status,
                decided_by=user.id,
                decided_at=now,
                dispute_reason=None if data.accept else reason,
            )
            await self._recompute(repo, charge.id)
            if data.accept:
                code, event = SystemCode.PAYMENT_CONFIRMED, "payment_confirmed"
                params: dict[str, Any] = {"amount": _money(payment.amount)}
            else:
                code, event = SystemCode.PAYMENT_REJECTED, "payment_rejected"
                params = {"amount": _money(payment.amount), "reason": reason}
            await self._system(
                tx,
                charge.lease_id,
                code,
                {"period": charge.period_month.isoformat(), **params},
            )
            await self._notify(tx, info, [payment.payer_user_id], event, charge, params)
        return await self.overview(telegram_uid, charge.lease_id)

    async def receipt(self, telegram_uid: int, payment_id: uuid.UUID) -> tuple[str, bytes, str]:
        """Файл квитанції — учасникам оренди."""
        async with self._tx.begin() as tx:
            payment = await self._repo(tx).payment(payment_id)
        if payment is None or payment.receipt_storage_key is None:
            raise NotFound("Квитанцію не знайдено")
        charge = await self._charge(payment.charge_id)
        await self._leases.for_contract(telegram_uid, charge.lease_id)
        data = await self._storage.get(payment.receipt_storage_key)
        return (
            payment.receipt_filename or "kvytantsiia",
            data,
            payment.receipt_mime or "application/octet-stream",
        )

    async def send_receipt_to_chat(self, telegram_uid: int, payment_id: uuid.UUID) -> int:
        name, data, _ = await self.receipt(telegram_uid, payment_id)
        if self._notifier is None:
            raise ServiceUnavailable("Надсилання файлів не налаштовано")
        try:
            await self._notifier.send_document(telegram_uid, name, data)
        except NotifierError as exc:
            raise ServiceUnavailable("Не вдалося надіслати файл у чат") from exc
        return 1

    # ------------------------------------------------------ фонові задачі

    async def run_daily(self) -> dict[str, int]:
        """Нарахування наперед, статуси за датами, нагадування (FR-PAY-1, FR-PAY-2, FR-PAY-4)."""
        now = self._clock.now()
        today = to_kyiv(now).date()
        horizon = today + timedelta(days=self._config.generate_days_ahead)
        stats = {"created": 0, "reminders": 0, "overdue": 0, "pending": 0}
        for lease_id in await self._leases.billable_ids():
            async with self._tx.begin() as tx:
                info = await self._leases.for_contract_by_id(tx, lease_id)
                repo = self._repo(tx)
                stats["created"] += await self._ensure_charges(repo, info, today, horizon)
                for charge in await repo.charges(lease_id):
                    was = charge.status
                    after = await self._recompute(repo, charge.id, today)
                    if after is ChargeStatus.OVERDUE and was is not ChargeStatus.OVERDUE:
                        stats["overdue"] += 1
                        await self._notify(
                            tx, info, [info.landlord_user_id], "rent_overdue_landlord", charge, {}
                        )
                    if after in (ChargeStatus.UPCOMING, ChargeStatus.DUE, ChargeStatus.OVERDUE):
                        offset = reminder_offset(
                            charge.due_date, today, self._config.reminder_offsets
                        )
                        if offset is not None:
                            stats["reminders"] += 1
                            await self._notify(
                                tx,
                                info,
                                [info.responsible_user_id],
                                f"rent_reminder_{_offset_name(offset)}",
                                charge,
                                {"amount": _money(charge.amount)},
                                stamp=f"{charge.id}:{offset}",
                            )
        # Власник не відповів на «оплатив» N днів — нагадати один раз.
        cutoff = now - timedelta(days=self._config.pending_reminder_days)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            for payment in await repo.pending_before(cutoff):
                pending_charge = await repo.charge(payment.charge_id)
                if pending_charge is None:
                    continue
                charge = pending_charge
                info = await self._leases.for_contract_by_id(tx, charge.lease_id)
                if info.lease.status not in (LeaseStatus.ACTIVE, LeaseStatus.ENDING):
                    continue
                stats["pending"] += 1
                await self._notify(
                    tx,
                    info,
                    [info.landlord_user_id],
                    "payment_pending_reminder",
                    charge,
                    {"amount": _money(payment.amount)},
                    stamp=f"pending:{payment.id}",
                )
        return stats

    async def _ensure_charges(
        self, repo: PaymentsRepository, info: LeaseForContract, today: date, horizon: date
    ) -> int:
        lease = info.lease
        last_day = lease.termination.date if lease.termination else lease.end_date
        created = 0
        if lease.deposit_amount:
            due = max(lease.start_date, today)  # активували пізніше заїзду — до сплати сьогодні
            if await repo.add_charge(
                NewCharge(
                    lease_id=lease.id,
                    period_month=lease.start_date.replace(day=1),
                    kind=ChargeKind.DEPOSIT.value,
                    amount=Decimal(lease.deposit_amount),
                    currency=lease.currency,
                    due_date=due,
                    status=ChargeStatus.UPCOMING,
                )
            ):
                created += 1
        for period, due in rent_periods(lease.start_date, last_day, lease.rent_due_day):
            if due > horizon:
                break
            if await repo.add_charge(
                NewCharge(
                    lease_id=lease.id,
                    period_month=period,
                    kind=ChargeKind.RENT.value,
                    amount=Decimal(lease.rent_amount),
                    currency=lease.currency,
                    due_date=due,
                    status=ChargeStatus.UPCOMING,
                )
            ):
                created += 1
        return created

    # ------------------------------------------------------------ службове

    async def _recompute(
        self, repo: PaymentsRepository, charge_id: uuid.UUID, today: date | None = None
    ) -> ChargeStatus:
        charge = await repo.charge(charge_id)
        if charge is None:
            raise NotFound("Нарахування не знайдено")
        if charge.status in (ChargeStatus.WAIVED, ChargeStatus.DISPUTED):
            return charge.status
        payments = await repo.payments([charge_id])
        status = charge_status(
            charge.due_date,
            today or to_kyiv(self._clock.now()).date(),
            charge.amount,
            _sum(payments, {PaymentStatus.CONFIRMED}),
            any(p.status in IN_FLIGHT for p in payments),
        )
        if status is not charge.status:
            await repo.set_charge_status(charge_id, status)
        return status

    async def _charge(self, charge_id: uuid.UUID) -> ChargeRecord:
        async with self._tx.begin() as tx:
            charge = await self._repo(tx).charge(charge_id)
        if charge is None:
            raise NotFound("Нарахування не знайдено")
        return charge

    @staticmethod
    def _check_payable(info: LeaseForContract, charge: ChargeRecord) -> None:
        if info.lease.status not in (LeaseStatus.ACTIVE, LeaseStatus.ENDING):
            raise Conflict("Оренда не активна", field="status")
        if charge.status in (ChargeStatus.PAID, ChargeStatus.WAIVED):
            raise Conflict("Це нарахування вже закрите", field="charge", reason="closed")

    async def _charge_out(self, c: ChargeRecord, payments: list[PaymentRecord]) -> ChargeOut:
        mine = [p for p in payments if p.charge_id == c.id]
        paid = _sum(mine, {PaymentStatus.CONFIRMED})
        names: dict[uuid.UUID, str] = {}
        out: list[PaymentOut] = []
        for p in mine:
            if p.payer_user_id not in names:
                u = await self._users.get_by_id(p.payer_user_id)
                names[p.payer_user_id] = u.display_name if u else ""
            out.append(
                PaymentOut(
                    id=p.id,
                    method=p.method,
                    amount=p.amount,
                    status=p.status,
                    payer_name=names[p.payer_user_id],
                    has_receipt=p.receipt_storage_key is not None,
                    receipt_mime=p.receipt_mime,
                    mismatches=p.mismatches,
                    note=p.note,
                    dispute_reason=p.dispute_reason,
                    created_at=p.created_at,
                    decided_at=p.decided_at,
                )
            )
        kind: Literal["rent", "deposit", "utilities", "other"] = (
            c.kind if c.kind in ("rent", "deposit", "utilities", "other") else "other"  # type: ignore[assignment]
        )
        return ChargeOut(
            id=c.id,
            kind=kind,
            period_month=c.period_month,
            due_date=c.due_date,
            amount=c.amount,
            paid=paid,
            remaining=max(c.amount - paid, Decimal(0)),
            status=c.status,
            payments=out,
        )

    async def _system(
        self, tx: Tx, lease_id: uuid.UUID, code: SystemCode, params: dict[str, Any]
    ) -> None:
        conversation = await self._chat.ensure_for_lease(tx, lease_id)
        await self._chat.system(tx, conversation, code, params)

    async def _notify(
        self,
        tx: Tx,
        info: LeaseForContract,
        user_ids: list[uuid.UUID | None],
        event: str,
        charge: ChargeRecord,
        params: dict[str, Any],
        *,
        stamp: str | None = None,
    ) -> None:
        mark = stamp or self._clock.now().strftime("%Y%m%d%H%M%S%f")
        for uid in user_ids:
            if uid is None:
                continue
            await self._notifications.enqueue_for_lease(
                tx,
                uid,
                info.lease.id,
                NotificationType.PAYMENT,
                {
                    "lease_id": str(info.lease.id),
                    "event": event,
                    "street": info.lease.street,
                    "house_number": info.lease.house_number,
                    "period": charge.period_month.isoformat(),
                    "kind": charge.kind,
                    "due": charge.due_date.strftime("%d.%m"),
                    **{k: v for k, v in params.items() if isinstance(v, str | int | float | bool)},
                },
                dedupe_key=f"pay:{info.lease.id}:{event}:{uid}:{mark}",
            )


def _sum(payments: list[PaymentRecord], statuses: set[PaymentStatus]) -> Decimal:
    return sum((p.amount for p in payments if p.status in statuses), Decimal(0))


def _money(value: Decimal) -> str:
    text = f"{value:,.2f}".replace(",", " ")
    return text[:-3] if text.endswith(".00") else text


def _offset_name(offset: int) -> str:
    return "before" if offset < 0 else "today" if offset == 0 else "after"


def _safe_name(filename: str) -> str:
    base = "".join(ch for ch in filename if ch.isalnum() or ch in "._- ")[:80].strip(" .")
    return base or "kvytantsiia"
