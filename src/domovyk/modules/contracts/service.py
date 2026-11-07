"""Публічний інтерфейс модуля contracts: договір, підписи через портал Дії, CTR-EXT (M4).

FR-CTR-1..7, FR-LSE-6, FR-LSE-8 (ліміт за РНОКПП). Поки немає криптоперевірки
підпису, кожен підпис підтверджує модератор (ADR 0010, `require_moderator`).
"""

from __future__ import annotations

import io
import logging
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from domovyk.adapters.notifier.base import Notifier, NotifierError
from domovyk.adapters.pdf.base import PdfError, PdfRenderer
from domovyk.adapters.signing.base import SignatureParseError, SignatureVerifier
from domovyk.adapters.storage.base import FileStorage
from domovyk.core.clock import Clock, to_kyiv
from domovyk.core.crypto import identifier_hash
from domovyk.core.errors import (
    Banned,
    Conflict,
    InvalidInput,
    LeaseLimitReached,
    NotFound,
    PermissionDenied,
    ServiceUnavailable,
)
from domovyk.core.tx import Tx, TxManager
from domovyk.domain.contracts import (
    CONTRACT,
    EXTERNAL_PDF_MAX_BYTES,
    LIVE_STATUSES,
    SIGNATURE_MAX_BYTES,
    TEMPLATE_CODE,
    ContractSource,
    ContractStatus,
    PartyRole,
    SignatureCheck,
    accepted_checks,
    clean_legal_name,
    looks_like_pdf,
    names_match,
    sha256_hex,
)
from domovyk.domain.leases import LeaseOrigin, LeaseStatus, months_between
from domovyk.modules.chat.service import ChatService, SystemCode
from domovyk.modules.contracts.render import render_contract
from domovyk.modules.contracts.repository import (
    ContractRecord,
    ContractsRepository,
    NewContract,
    NewSignature,
    SignatureRecord,
)
from domovyk.modules.contracts.schemas import (
    ContractOut,
    ContractState,
    PartyOut,
    SignatureCase,
    SignatureOut,
    SignatureState,
)
from domovyk.modules.leases.service import LeaseForContract, LeasesService
from domovyk.modules.moderation.service import (
    AuditAction,
    BanIdentifiers,
    ModerationService,
    RiskSignal,
)
from domovyk.modules.notifications.service import NotificationsService, NotificationType
from domovyk.modules.properties.service import PropertiesService
from domovyk.modules.users.service import UserOut, UsersService

__all__ = [
    "ContractOut",
    "ContractState",
    "ContractsConfig",
    "ContractsService",
    "PartyOut",
    "SignatureCase",
    "SignatureOut",
]

log = logging.getLogger(__name__)

DIIA_SIGN_URL = "https://id.gov.ua/sign"
REQUIRED_ROLES = (PartyRole.LANDLORD, PartyRole.TENANT)


@dataclass(frozen=True)
class ContractsConfig:
    hmac_secret: str
    # ADR 0010: поки немає криптоперевірки (ДСТУ 4145, ЦЗО, OCSP) — кожен підпис дивиться модератор.
    require_moderator: bool = True
    allow_self_review: bool = False
    signature_max_bytes: int = SIGNATURE_MAX_BYTES
    external_max_bytes: int = EXTERNAL_PDF_MAX_BYTES
    reminder_hours: tuple[int, ...] = (24, 72)
    review_limit: int = 50
    diia_sign_url: str = DIIA_SIGN_URL


class ContractsService:
    def __init__(
        self,
        tx: TxManager,
        repo: Callable[[Tx], ContractsRepository],
        users: UsersService,
        properties: PropertiesService,
        leases: LeasesService,
        chat: ChatService,
        notifications: NotificationsService,
        moderation: ModerationService,
        storage: FileStorage,
        pdf: PdfRenderer,
        verifier: SignatureVerifier,
        clock: Clock,
        config: ContractsConfig,
        notifier: Notifier | None = None,
    ) -> None:
        self._tx = tx
        self._repo = repo
        self._users = users
        self._properties = properties
        self._leases = leases
        self._chat = chat
        self._notifications = notifications
        self._moderation = moderation
        self._storage = storage
        self._pdf = pdf
        self._verifier = verifier
        self._clock = clock
        self._config = config
        self._notifier = notifier

    # ------------------------------------------------------------------ стан

    async def state(self, telegram_uid: int, lease_id: uuid.UUID) -> ContractState:
        info = await self._leases.for_contract(telegram_uid, lease_id)
        return await self._state(info)

    async def set_legal_name(
        self, telegram_uid: int, lease_id: uuid.UUID, full_name: str
    ) -> ContractState:
        """ПІБ для тексту договору: кожна сторона вказує своє (власнику підказуємо з довідки)."""
        name = clean_legal_name(full_name)
        info = await self._leases.for_contract(telegram_uid, lease_id)
        role = _party_role(info)
        if role is None:
            raise PermissionDenied("ПІБ для договору вказують власник і відповідальний орендар")
        if info.lease.status not in (LeaseStatus.AWAITING_TENANT, LeaseStatus.AWAITING_SIGNATURES):
            raise Conflict("Договір уже підписано", field="status")
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            current = await repo.current_for_lease(lease_id)
            if current is not None and await self._has_signatures(repo, current):
                # Ім'я вже у підписаному кимось договорі — міняти можна лише разом з новим.
                raise Conflict("Договір уже підписують — спершу сформуй новий", field="full_name")
            if info.viewer_user_id is None:
                raise NotFound("Оренду не знайдено")
            await repo.set_party_name(lease_id, info.viewer_user_id, role, name, self._clock.now())
        return await self._state(await self._leases.for_contract(telegram_uid, lease_id))

    # ------------------------------------------------------------- генерація

    async def generate(self, telegram_uid: int, lease_id: uuid.UUID) -> ContractState:
        """CTR-GEN: PDF з погоджених умов. Попередня версія (якщо була) стає недійсною."""
        info = await self._leases.for_contract(telegram_uid, lease_id)
        if info.lease.my_role != "landlord":
            raise PermissionDenied("Договір формує власник")
        if info.lease.status is not LeaseStatus.AWAITING_SIGNATURES:
            raise Conflict(
                "Договір формується після того, як орендар погодив умови", field="status"
            )
        if info.responsible_user_id is None:
            raise Conflict("Спершу запроси відповідального орендаря", field="status")
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            names = await repo.party_names(lease_id)
            current = await repo.current_for_lease(lease_id)
            version = await repo.latest_version(lease_id) + 1
        landlord_name = names.get(info.landlord_user_id)
        tenant_name = names.get(info.responsible_user_id)
        if not landlord_name or not tenant_name:
            raise InvalidInput("Потрібні ПІБ обох сторін для договору", field="full_name")
        if current is not None and current.status is ContractStatus.SIGNED:
            raise Conflict("Договір уже підписано", field="status")
        contract_id = uuid.uuid4()
        snapshot = self._snapshot(info, landlord_name, tenant_name, contract_id, version, now)
        try:
            pdf = await self._pdf.render(render_contract(snapshot))
        except PdfError as exc:
            log.error("PDF договору не згенеровано: %s", exc)
            raise ServiceUnavailable("Не вдалося сформувати PDF договору") from exc
        key = f"contracts/{lease_id}/{contract_id}.pdf"
        await self._storage.put(key, pdf)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if current is not None and current.status in LIVE_STATUSES:
                await self._void(repo, current, "superseded")
            await repo.create(
                NewContract(
                    lease_id=lease_id,
                    source=ContractSource.GENERATED,
                    template_code=TEMPLATE_CODE,
                    version=version,
                    pdf_storage_key=key,
                    pdf_sha256=sha256_hex(pdf),
                    status=ContractStatus.SENT,
                    data_snapshot=snapshot,
                    conditions_hash=info.conditions_hash,
                    created_by=info.landlord_user_id,
                )
            )
            await self._system(tx, lease_id, SystemCode.CONTRACT_READY, {"version": version})
            await self._notify(
                tx, info, [info.landlord_user_id, info.responsible_user_id], "contract_ready"
            )
        return await self._state(await self._leases.for_contract(telegram_uid, lease_id))

    def _snapshot(
        self,
        info: LeaseForContract,
        landlord_name: str,
        tenant_name: str,
        contract_id: uuid.UUID,
        version: int,
        now: datetime,
    ) -> dict[str, Any]:
        """Усе, з чого зібрано текст договору. Без реквізитів оплати й РНОКПП."""
        lease, prop = info.lease, info.property
        return {
            "number": f"{to_kyiv(now):%Y%m%d}-{str(contract_id)[:8].upper()}",
            "template_code": TEMPLATE_CODE,
            "version": version,
            "generated_on": to_kyiv(now).date().isoformat(),
            "city": prop.city,
            "street": prop.street,
            "house_number": prop.house_number,
            "apartment": prop.apartment,
            "kind": prop.kind.value,
            "unit_kind": lease.unit_kind.value,
            "unit_name": lease.unit_name,
            "area": str(prop.area) if prop.area and lease.unit_kind.value == "entire" else None,
            "rooms": prop.rooms if lease.unit_kind.value == "entire" else None,
            "landlord_name": landlord_name,
            "tenant_name": tenant_name,
            "start_date": lease.start_date.isoformat(),
            "end_date": lease.end_date.isoformat(),
            "months": months_between(lease.start_date, lease.end_date) + 1,
            "rent_amount": lease.rent_amount,
            "currency": lease.currency,
            "rent_due_day": lease.rent_due_day,
            "deposit_amount": lease.deposit_amount,
            "pay_method": lease.pay.method.value,
            "terms": lease.terms.model_dump(),
            "co_tenants": [],
        }

    # ------------------------------------------------------------- підписи

    async def upload_signature(
        self, telegram_uid: int, contract_id: uuid.UUID, filename: str, data: bytes
    ) -> ContractState:
        """CTR-SIGN: файл підпису з порталу Дії (.p7s / .asice) під саме цим PDF."""
        if not data or len(data) > self._config.signature_max_bytes:
            raise InvalidInput("Файл підпису — до 5 МБ", field="signature", reason="too_big")
        user = await self._users.require_active(telegram_uid)
        contract = await self._contract(contract_id)
        info = await self._leases.for_contract(telegram_uid, contract.lease_id)
        role = _party_role(info)
        if role is None:
            raise PermissionDenied("Договір підписують власник і відповідальний орендар")
        self._check_signable(contract, info)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            mine = next(
                (s for s in await repo.signatures(contract.id) if s.user_id == user.id), None
            )
            names = await repo.party_names(contract.lease_id)
        accepted = accepted_checks(self._config.require_moderator)
        if mine is not None and mine.check_status in accepted:
            raise Conflict("Твій підпис уже прийнято", field="signature")
        pdf = await self._storage.get(contract.pdf_storage_key)
        try:
            report = await self._verifier.inspect(data, pdf)
        except SignatureParseError as exc:
            raise InvalidInput(
                "Не можу прочитати файл підпису", field="signature", reason="unreadable"
            ) from exc
        if report.document_match == "mismatch":
            raise InvalidInput(
                "Цей підпис стоїть під іншим файлом", field="signature", reason="other_document"
            )
        signer = report.signer
        if signer is None or not signer.full_name:
            raise InvalidInput(
                "У підписі немає сертифіката підписувача", field="signature", reason="unreadable"
            )
        if signer.is_legal_entity:
            raise InvalidInput(
                "Потрібен особистий підпис, а не печатка організації",
                field="signature",
                reason="legal_entity",
            )
        tax_hash = (
            identifier_hash(signer.tax_id, self._config.hmac_secret) if signer.tax_id else None
        )
        if tax_hash is not None:
            await self._check_banned(user, tax_hash)
            if role is PartyRole.TENANT:
                await self._check_tenant_limit(tax_hash, contract.lease_id)
        expected = names.get(user.id)
        owner_doc = (
            await self._properties.verified_owner_name(info.property.id)
            if role is PartyRole.LANDLORD
            else None
        )
        name_ok = names_match(signer.full_name, expected) and (
            owner_doc is None or names_match(signer.full_name, owner_doc)
        )
        if not name_ok:
            check = SignatureCheck.MISMATCH
        elif report.document_match == "match":
            check = SignatureCheck.AUTO_OK
        else:
            check = SignatureCheck.NOT_CHECKED
        now = self._clock.now()
        ext = ".asice" if report.container == "asice" else ".p7s"
        key = f"contracts/{contract.lease_id}/sig-{uuid.uuid4()}{ext}"
        await self._storage.put(key, data)
        details = {
            "container": report.container,
            "document_match": report.document_match,
            "digest_algorithm": report.digest_algorithm,
            "name_match": name_ok,
            "owner_doc_checked": owner_doc is not None,
            "issuer": signer.issuer,
            "cert_valid_to": signer.valid_to.isoformat() if signer.valid_to else None,
            "crypto_verified": report.crypto_verified,
            "notes": report.notes,
        }
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            await repo.upsert_signature(
                NewSignature(
                    contract_id=contract.id,
                    user_id=user.id,
                    party_role=role,
                    signature_storage_key=key,
                    signature_filename=_safe_name(filename, ext),
                    signer_name=signer.full_name,
                    signer_tax_id_hash=tax_hash,
                    signed_at_claimed=report.signed_at,
                    check_status=check,
                    check_details=details,
                    uploaded_at=now,
                )
            )
            if contract.status is ContractStatus.SENT:
                await repo.update(
                    contract.id,
                    status=CONTRACT.transition(contract.status, ContractStatus.PARTIALLY_SIGNED),
                )
            await self._system(
                tx,
                contract.lease_id,
                SystemCode.CONTRACT_SIGNED_BY,
                {"name": user.display_name, "role": role.value},
            )
            others = [u for u in (info.landlord_user_id, info.responsible_user_id) if u != user.id]
            await self._notify(tx, info, others, "contract_signed_by", {"name": user.display_name})
            if check in accepted:
                await self._maybe_complete(tx, contract.id)
            else:
                await self._notify(tx, info, [user.id], "signature_review")
                await self._alert_staff(
                    tx,
                    user,
                    "signature_mismatch"
                    if check is SignatureCheck.MISMATCH
                    else "signature_review",
                    f"sig:{contract.id}:{user.id}:{now:%Y%m%d%H%M%S%f}",
                )
                if check is SignatureCheck.MISMATCH:
                    await self._moderation.add_risk_signal(
                        tx,
                        user.id,
                        RiskSignal.SIGNATURE_MISMATCH,
                        {"contract_id": str(contract.id)},
                    )
        if mine is not None and mine.signature_storage_key != key:
            await self._delete_file(mine.signature_storage_key)
        return await self.state(telegram_uid, contract.lease_id)

    async def _check_banned(self, user: UserOut, tax_hash: str) -> None:
        async with self._tx.begin() as tx:
            ban = await self._moderation.find_active_ban(tx, BanIdentifiers(identity_hash=tax_hash))
            if ban is None:
                return
            await self._moderation.add_risk_signal(tx, user.id, RiskSignal.BANNED_SIGNER, {})
            await self._alert_staff(
                tx, user, "banned_signer", f"banned_signer:{user.id}:{self._clock.now():%Y%m%d%H}"
            )
        raise Banned("Підписувач заблокований у Домовику")

    async def _check_tenant_limit(self, tax_hash: str, lease_id: uuid.UUID) -> None:
        """FR-LSE-8 з M4: ліміт оренд рахуємо за РНОКПП, а не лише за акаунтом."""
        async with self._tx.begin() as tx:
            others = await self._repo(tx).lease_ids_signed_by(tax_hash, PartyRole.TENANT, lease_id)
        if not others:
            return
        limit = self._leases.tenant_max_open_leases
        if len(await self._leases.open_lease_ids(others)) >= limit:
            raise LeaseLimitReached("Забагато одночасних оренд", limit=limit)

    async def _maybe_complete(self, tx: Tx, contract_id: uuid.UUID) -> bool:
        """FR-CTR-7: обидва підписи прийнято → договір підписано, оренда активна."""
        repo = self._repo(tx)
        contract = await repo.get(contract_id)
        if contract is None or contract.status not in LIVE_STATUSES:
            return False
        accepted = accepted_checks(self._config.require_moderator)
        roles = {
            s.party_role for s in await repo.signatures(contract_id) if s.check_status in accepted
        }
        if not all(r in roles for r in REQUIRED_ROLES):
            return False
        await repo.update(
            contract_id,
            status=CONTRACT.transition(contract.status, ContractStatus.SIGNED),
            signed_at=self._clock.now(),
        )
        await self._leases.activate(tx, contract.lease_id)
        return True

    # ------------------------------------------------- модератор (ADR 0010)

    async def review_queue(self, telegram_uid: int) -> list[SignatureCase]:
        await self._users.require_staff(telegram_uid)
        async with self._tx.begin() as tx:
            rows = await self._repo(tx).signatures_to_review(self._config.review_limit)
            return [await self._case(tx, s) for s in rows]

    async def review_case(self, telegram_uid: int, signature_id: uuid.UUID) -> SignatureCase:
        await self._users.require_staff(telegram_uid)
        async with self._tx.begin() as tx:
            return await self._case(tx, await self._signature(self._repo(tx), signature_id))

    async def review_signature(
        self, telegram_uid: int, signature_id: uuid.UUID, approve: bool, reason: str | None
    ) -> SignatureCase:
        """Модератор перевірив підпис (наприклад, на czo.gov.ua): прийняти або відхилити."""
        staff = await self._users.require_staff(telegram_uid)
        clean_reason = " ".join((reason or "").split())[:500] or None
        if not approve and not clean_reason:
            raise InvalidInput("Вкажи причину відхилення", field="reason")
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            sig = await self._signature(repo, signature_id)
            contract = await repo.get(sig.contract_id)
            if contract is None or contract.status not in LIVE_STATUSES:
                raise Conflict("Договір уже не чекає підписів", field="status")
            info = await self._leases.for_contract_by_id(tx, contract.lease_id)
            parties = {info.landlord_user_id, info.responsible_user_id}
            if staff.id in parties and not self._config.allow_self_review:
                raise PermissionDenied("Не можна перевіряти власний договір")
            status = SignatureCheck.MANUAL_OK if approve else SignatureCheck.INVALID
            await repo.review_signature(sig.id, status, staff.id, now, clean_reason)
            await self._moderation.audit(
                tx,
                staff.id,
                AuditAction.SIGNATURE_APPROVE if approve else AuditAction.SIGNATURE_REJECT,
                "contract_signature",
                str(sig.id),
                {"contract_id": str(contract.id)},
            )
            if approve:
                await self._maybe_complete(tx, contract.id)
            else:
                await self._notify(
                    tx, info, [sig.user_id], "signature_rejected", {"reason": clean_reason}
                )
            return await self._case(tx, await self._signature(repo, signature_id))

    async def send_review_files(self, telegram_uid: int, signature_id: uuid.UUID) -> int:
        """Модератору в чат: PDF договору й файл підпису — для перевірки на czo.gov.ua."""
        await self._users.require_staff(telegram_uid)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            sig = await self._signature(repo, signature_id)
            contract = await repo.get(sig.contract_id)
        if contract is None:
            raise NotFound("Договір не знайдено")
        files = [
            (_pdf_name(contract), await self._storage.get(contract.pdf_storage_key)),
            (sig.signature_filename, await self._storage.get(sig.signature_storage_key)),
        ]
        return await self._send(telegram_uid, files)

    # ---------------------------------------------------- договір поза Домовиком

    async def upload_external(
        self, telegram_uid: int, lease_id: uuid.UUID, filename: str, data: bytes
    ) -> ContractState:
        """CTR-EXT: власник завантажує вже підписаний договір (лише оренда за запрошенням)."""
        if not data or len(data) > self._config.external_max_bytes or not looks_like_pdf(data):
            raise InvalidInput("Потрібен PDF до 10 МБ", field="file")
        info = await self._leases.for_contract(telegram_uid, lease_id)
        if info.lease.my_role != "landlord":
            raise PermissionDenied("Договір завантажує власник")
        if info.origin is not LeaseOrigin.INVITE:
            raise Conflict("Лише для оренди за запрошенням", field="origin")
        if info.lease.status is not LeaseStatus.AWAITING_SIGNATURES or not info.responsible_user_id:
            raise Conflict("Спершу орендар має погодити умови", field="status")
        now = self._clock.now()
        contract_id = uuid.uuid4()
        key = f"contracts/{lease_id}/{contract_id}-external.pdf"
        await self._storage.put(key, data)
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            current = await repo.current_for_lease(lease_id)
            if current is not None and current.status is ContractStatus.SIGNED:
                raise Conflict("Договір уже підписано", field="status")
            if current is not None:
                await self._void(repo, current, "superseded")
            await repo.create(
                NewContract(
                    lease_id=lease_id,
                    source=ContractSource.UPLOADED_EXTERNAL,
                    template_code=None,
                    version=await repo.latest_version(lease_id) + 1,
                    pdf_storage_key=key,
                    pdf_sha256=sha256_hex(data),
                    status=ContractStatus.SENT,
                    data_snapshot={
                        "filename": _safe_name(filename, ".pdf"),
                        "uploaded_on": str(now),
                    },
                    conditions_hash=info.conditions_hash,
                    created_by=info.landlord_user_id,
                )
            )
            landlord = await self._users.get_by_id(info.landlord_user_id)
            name = landlord.display_name if landlord else ""
            await self._system(tx, lease_id, SystemCode.CONTRACT_EXTERNAL, {"name": name})
            await self._notify(
                tx, info, [info.responsible_user_id], "contract_external", {"name": name}
            )
        return await self.state(telegram_uid, lease_id)

    async def decide_external(
        self, telegram_uid: int, contract_id: uuid.UUID, accept: bool
    ) -> ContractState:
        """CTR-EXT: відповідальний орендар підтверджує, що це їхній чинний договір."""
        contract = await self._contract(contract_id)
        info = await self._leases.for_contract(telegram_uid, contract.lease_id)
        if info.lease.my_role != "responsible":
            raise PermissionDenied("Підтверджує відповідальний орендар")
        if contract.source is not ContractSource.UPLOADED_EXTERNAL:
            raise Conflict("Це не завантажений договір", field="source")
        self._check_live(contract, info)
        now = self._clock.now()
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            if accept:
                await repo.update(
                    contract.id,
                    status=CONTRACT.transition(contract.status, ContractStatus.SIGNED),
                    signed_at=now,
                    external_decided_at=now,
                )
                await self._leases.activate(tx, contract.lease_id)
            else:
                await self._void(repo, contract, "external_rejected", decided_at=now)
                await self._system(tx, contract.lease_id, SystemCode.CONTRACT_EXTERNAL_REJECTED, {})
                await self._notify(tx, info, [info.landlord_user_id], "contract_external_rejected")
        return await self.state(telegram_uid, contract.lease_id)

    # ------------------------------------------------------------- файли

    async def document(self, telegram_uid: int, contract_id: uuid.UUID) -> tuple[str, bytes]:
        """PDF договору — учасникам оренди."""
        contract = await self._contract(contract_id)
        await self._leases.for_contract(telegram_uid, contract.lease_id)
        return _pdf_name(contract), await self._storage.get(contract.pdf_storage_key)

    async def package(self, telegram_uid: int, contract_id: uuid.UUID) -> tuple[str, bytes]:
        """FR-CTR-7: PDF + файли підписів одним zip."""
        contract = await self._contract(contract_id)
        await self._leases.for_contract(telegram_uid, contract.lease_id)
        async with self._tx.begin() as tx:
            sigs = await self._repo(tx).signatures(contract.id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(_pdf_name(contract), await self._storage.get(contract.pdf_storage_key))
            for s in sigs:
                if s.check_status is SignatureCheck.INVALID:
                    continue
                prefix = "landlord" if s.party_role is PartyRole.LANDLORD else "tenant"
                ext = ".asice" if s.signature_filename.lower().endswith(".asice") else ".p7s"
                data = await self._storage.get(s.signature_storage_key)
                archive.writestr(f"{prefix}-signature{ext}", data)
        return f"dogovir-{contract.data_snapshot.get('number', contract.id)}.zip", buffer.getvalue()

    async def send_to_chat(
        self, telegram_uid: int, contract_id: uuid.UUID, what: Literal["pdf", "package"]
    ) -> int:
        """У Mini App на телефонах файли відкриваються ненадійно — шлемо в чат із ботом."""
        if what == "package":
            files = [await self.package(telegram_uid, contract_id)]
        else:
            files = [await self.document(telegram_uid, contract_id)]
        return await self._send(telegram_uid, files)

    # ------------------------------------------------------ фонові задачі

    async def remind_unsigned(self) -> int:
        """Нагадування тим, хто ще не підписав: через 24 і 72 год (BOT_FLOWS CTR-SIGN)."""
        hours = sorted(self._config.reminder_hours)
        if not hours:
            return 0
        now = self._clock.now()
        queued = 0
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            for contract in await repo.live_older_than(now - timedelta(hours=hours[0])):
                if contract.source is not ContractSource.GENERATED:
                    continue
                info = await self._leases.for_contract_by_id(tx, contract.lease_id)
                if info.lease.status is not LeaseStatus.AWAITING_SIGNATURES:
                    continue
                signed = {
                    s.user_id
                    for s in await repo.signatures(contract.id)
                    if s.check_status is not SignatureCheck.INVALID
                }
                waiting = [
                    u
                    for u in (info.landlord_user_id, info.responsible_user_id)
                    if u is not None and u not in signed
                ]
                age = now - contract.created_at
                for h in hours:
                    if age < timedelta(hours=h):
                        continue
                    for uid in waiting:
                        if await self._notify_one(tx, info, uid, "contract_reminder", {}, f"{h}h"):
                            queued += 1
        return queued

    # ------------------------------------------------------------ службове

    async def _state(self, info: LeaseForContract) -> ContractState:
        lease = info.lease
        async with self._tx.begin() as tx:
            repo = self._repo(tx)
            current = await repo.current_for_lease(lease.id)
            names = await repo.party_names(lease.id)
            sigs = await repo.signatures(current.id) if current else []
        owner_hint = None
        if lease.my_role == "landlord" and info.landlord_user_id not in names:
            owner_hint = await self._properties.verified_owner_name(info.property.id)
        stale = (
            current is not None
            and current.status in LIVE_STATUSES
            and current.conditions_hash != info.conditions_hash
        )
        accepted = accepted_checks(self._config.require_moderator)
        parties: list[PartyOut] = []
        for role, uid in (
            (PartyRole.LANDLORD, info.landlord_user_id),
            (PartyRole.TENANT, info.responsible_user_id),
        ):
            if uid is None:
                continue
            user = await self._users.get_by_id(uid)
            sig = next((s for s in sigs if s.user_id == uid), None)
            parties.append(
                PartyOut(
                    role=role.value,
                    user_id=uid,
                    display_name=user.display_name if user else "",
                    legal_name=names.get(uid),
                    is_me=uid == info.viewer_user_id,
                    signature=_signature_out(sig, accepted) if sig else None,
                )
            )
        my_role = lease.my_role
        awaiting = lease.status is LeaseStatus.AWAITING_SIGNATURES
        live = current is not None and current.status in LIVE_STATUSES and not stale
        my_party = next((p for p in parties if p.is_me), None)
        can_sign = (
            live
            and current is not None
            and current.source is ContractSource.GENERATED
            and my_party is not None
            and (my_party.signature is None or my_party.signature.state == "rejected")
        )
        signed_by_anyone = bool(sigs) and live
        return ContractState(
            lease_id=lease.id,
            lease_status=lease.status.value,
            my_role=my_role,
            contract=_contract_out(current) if current else None,
            parties=parties,
            stale=stale,
            needs_names=any(p.legal_name is None for p in parties) or len(parties) < 2,
            can_generate=my_role == "landlord"
            and awaiting
            and info.responsible_user_id is not None
            and not signed_by_anyone,
            can_upload_external=my_role == "landlord"
            and awaiting
            and info.origin is LeaseOrigin.INVITE
            and info.responsible_user_id is not None
            and not signed_by_anyone,
            can_sign=bool(can_sign),
            awaiting_my_confirmation=bool(
                live
                and current is not None
                and current.source is ContractSource.UPLOADED_EXTERNAL
                and my_role == "responsible"
            ),
            require_moderator=self._config.require_moderator,
            landlord_name_hint=owner_hint,
            diia_sign_url=self._config.diia_sign_url,
        )

    async def _case(self, tx: Tx, sig: SignatureRecord) -> SignatureCase:
        repo = self._repo(tx)
        contract = await repo.get(sig.contract_id)
        if contract is None:
            raise NotFound("Договір не знайдено")
        info = await self._leases.for_contract_by_id(tx, contract.lease_id)
        names = await repo.party_names(contract.lease_id)
        user = await self._users.get_by_id(sig.user_id)
        owner_doc = (
            await self._properties.verified_owner_name(info.property.id)
            if sig.party_role is PartyRole.LANDLORD
            else None
        )
        d = sig.check_details
        return SignatureCase(
            signature_id=sig.id,
            contract_id=contract.id,
            lease_id=contract.lease_id,
            street=info.property.street,
            house_number=info.property.house_number,
            apartment=info.property.apartment,
            party_role=sig.party_role.value,
            display_name=user.display_name if user else "",
            expected_name=names.get(sig.user_id),
            owner_name_in_doc=owner_doc,
            signer_name=sig.signer_name,
            check_status=sig.check_status,
            document_match=str(d.get("document_match") or "unknown"),
            digest_algorithm=d.get("digest_algorithm"),
            container=d.get("container"),
            issuer=d.get("issuer"),
            signed_at_claimed=sig.signed_at_claimed,
            uploaded_at=sig.uploaded_at,
            notes=list(d.get("notes") or []),
        )

    def _check_live(self, contract: ContractRecord, info: LeaseForContract) -> None:
        if contract.status not in LIVE_STATUSES:
            raise Conflict("Договір уже не чекає підписів", field="status", reason="not_live")
        if (
            info.lease.status is not LeaseStatus.AWAITING_SIGNATURES
            or contract.conditions_hash != info.conditions_hash
        ):
            raise Conflict(
                "Умови змінилися — потрібен новий договір", field="status", reason="stale"
            )

    def _check_signable(self, contract: ContractRecord, info: LeaseForContract) -> None:
        if contract.source is not ContractSource.GENERATED:
            raise Conflict("Цей договір підтверджують, а не підписують", field="source")
        self._check_live(contract, info)

    async def _contract(self, contract_id: uuid.UUID) -> ContractRecord:
        async with self._tx.begin() as tx:
            contract = await self._repo(tx).get(contract_id)
        if contract is None:
            raise NotFound("Договір не знайдено")
        return contract

    @staticmethod
    async def _signature(repo: ContractsRepository, signature_id: uuid.UUID) -> SignatureRecord:
        sig = await repo.signature(signature_id)
        if sig is None:
            raise NotFound("Підпис не знайдено")
        return sig

    @staticmethod
    async def _has_signatures(repo: ContractsRepository, contract: ContractRecord) -> bool:
        if contract.status not in LIVE_STATUSES:
            return contract.status is ContractStatus.SIGNED
        return any(
            s.check_status is not SignatureCheck.INVALID for s in await repo.signatures(contract.id)
        )

    async def _void(
        self,
        repo: ContractsRepository,
        contract: ContractRecord,
        reason: str,
        *,
        decided_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": CONTRACT.transition(contract.status, ContractStatus.VOID),
            "void_reason": reason,
        }
        if decided_at is not None:
            values["external_decided_at"] = decided_at
        await repo.update(contract.id, **values)

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
        params: dict[str, Any] | None = None,
    ) -> None:
        stamp = self._clock.now().strftime("%Y%m%d%H%M%S%f")
        for uid in user_ids:
            if uid is not None:
                await self._notify_one(tx, info, uid, event, params or {}, stamp)

    async def _notify_one(
        self,
        tx: Tx,
        info: LeaseForContract,
        user_id: uuid.UUID,
        event: str,
        params: dict[str, Any],
        stamp: str,
    ) -> bool:
        return await self._notifications.enqueue_for_lease(
            tx,
            user_id,
            info.lease.id,
            NotificationType.LEASE_UPDATE,
            {
                "lease_id": str(info.lease.id),
                "event": event,
                "street": info.lease.street,
                "house_number": info.lease.house_number,
                **params,
            },
            dedupe_key=f"contract:{info.lease.id}:{event}:{user_id}:{stamp}",
        )

    async def _alert_staff(self, tx: Tx, user: UserOut, reason: str, key: str) -> None:
        for staff_id in await self._users.staff_ids():
            await self._notifications.enqueue(
                tx,
                staff_id,
                NotificationType.MODERATION_ALERT,
                {
                    "reason": reason,
                    "user_name": user.display_name,
                    "telegram_uid": user.telegram_uid,
                },
                dedupe_key=f"moderation_alert:{key}:{staff_id}",
            )

    async def _send(self, telegram_uid: int, files: list[tuple[str, bytes]]) -> int:
        if self._notifier is None:
            raise ServiceUnavailable("Надсилання файлів не налаштовано")
        for name, data in files:
            try:
                await self._notifier.send_document(telegram_uid, name, data)
            except NotifierError as exc:
                raise ServiceUnavailable("Не вдалося надіслати файл у чат") from exc
        return len(files)

    async def _delete_file(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except Exception:  # файл прибере наступне чищення; операцію не валимо
            log.exception("Не вдалося видалити файл підпису")


def _party_role(info: LeaseForContract) -> PartyRole | None:
    if info.lease.my_role == "landlord":
        return PartyRole.LANDLORD
    if info.lease.my_role == "responsible":
        return PartyRole.TENANT
    return None


def _signature_state(sig: SignatureRecord, accepted: frozenset[SignatureCheck]) -> SignatureState:
    if sig.check_status in accepted:
        return "accepted"
    if sig.check_status is SignatureCheck.INVALID:
        return "rejected"
    return "review"


def _signature_out(sig: SignatureRecord, accepted: frozenset[SignatureCheck]) -> SignatureOut:
    return SignatureOut(
        id=sig.id,
        state=_signature_state(sig, accepted),
        check_status=sig.check_status,
        signer_name=sig.signer_name,
        uploaded_at=sig.uploaded_at,
        review_reason=sig.review_reason,
    )


def _contract_out(c: ContractRecord) -> ContractOut:
    return ContractOut(
        id=c.id,
        source=c.source,
        status=c.status,
        version=c.version,
        template_code=c.template_code,
        pdf_sha256=c.pdf_sha256,
        filename=_pdf_name(c),
        created_at=c.created_at,
        signed_at=c.signed_at,
    )


def _pdf_name(c: ContractRecord) -> str:
    if c.source is ContractSource.UPLOADED_EXTERNAL:
        return str(c.data_snapshot.get("filename") or "dogovir.pdf")
    return f"dogovir-{c.data_snapshot.get('number', c.id)}.pdf"


def _safe_name(filename: str, ext: str) -> str:
    base = "".join(ch for ch in filename if ch.isalnum() or ch in "._- ")[:80].strip(" .")
    if not base:
        base = "file"
    return base if base.lower().endswith(ext) else f"{base}{ext}"
