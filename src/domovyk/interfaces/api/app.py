"""FastAPI-застосунок для Mini App. Тонкий: лише виклики сервісів модулів.

Без `from __future__ import annotations`: FastAPI має бачити локальні залежності (Auth).
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import domovyk
from domovyk.config import Settings, get_settings
from domovyk.container import Container, Services
from domovyk.core.clock import Clock, system_clock
from domovyk.core.errors import (
    Conflict,
    DomainError,
    InvalidInput,
    NotFound,
    NotRegistered,
    PermissionDenied,
    ServiceUnavailable,
)
from domovyk.domain.contracts import EXTERNAL_PDF_MAX_BYTES, SIGNATURE_MAX_BYTES
from domovyk.domain.leases import MemberRole
from domovyk.domain.payments import RECEIPT_MAX_BYTES
from domovyk.interfaces.api.telegram_auth import (
    InitDataError,
    TelegramAuth,
    validate_init_data,
)
from domovyk.modules.contracts.service import (
    ContractState,
    ExternalDecisionIn,
    LegalNameIn,
    ReviewIn,
    SignatureCase,
)
from domovyk.modules.leases.service import (
    InviteOut,
    InvitePreview,
    LeaseConditions,
    LeaseCreate,
    LeaseNotificationPrefs,
    LeaseOut,
    LeaseSummary,
    MessageOut,
    MessagesPage,
    ProposalIn,
    RespondIn,
    TerminationIn,
)
from domovyk.modules.payments.service import (
    CashIn,
    DecisionIn,
    PayInfo,
    PaymentsOverview,
)
from domovyk.modules.properties.service import (
    AddressCandidate,
    CityOut,
    OwnershipOut,
    PhotoOut,
    PropertyCreate,
    PropertyDetails,
    PropertyOut,
    PropertyUpdate,
    QueueItem,
    RoomIn,
    VerificationCase,
)
from domovyk.modules.users.service import AppMode, ConsentInfo, TenantProfile, UserOut

log = logging.getLogger(__name__)

PHOTO_MAX_BYTES = 15 * 1024 * 1024  # як PropertiesConfig.max_photo_bytes
_AUTH_SCHEME = "tma "

# Порядок важливий: перший збіг isinstance визначає HTTP-статус.
_ERROR_STATUS: list[tuple[type[DomainError], int]] = [
    (NotFound, status.HTTP_404_NOT_FOUND),
    (NotRegistered, status.HTTP_403_FORBIDDEN),
    (PermissionDenied, status.HTTP_403_FORBIDDEN),
    (InvalidInput, 422),
    (Conflict, status.HTTP_409_CONFLICT),
    (ServiceUnavailable, status.HTTP_503_SERVICE_UNAVAILABLE),
]


class HealthOut(BaseModel):
    status: str
    version: str
    env: str


class TelegramUserOut(BaseModel):
    id: int
    first_name: str
    username: str | None


class MeOut(BaseModel):
    telegram: TelegramUserOut
    start_param: str | None
    user: UserOut | None  # None — ще не зареєстрований (онбординг у Mini App)
    consent: ConsentInfo


class RegisterIn(BaseModel):
    display_name: str
    terms_version: str
    privacy_version: str


class NameIn(BaseModel):
    display_name: str


class ModeIn(BaseModel):
    mode: AppMode


class ApproveIn(BaseModel):
    owner_name: str


class RejectIn(BaseModel):
    reason: str


class ModerationSummaryOut(BaseModel):
    ownership_pending: int
    signatures_pending: int = 0


class SendFilesIn(BaseModel):
    what: Literal["pdf", "package"] = "pdf"


class SentOut(BaseModel):
    sent: int


class LeaseCreatedOut(BaseModel):
    lease: LeaseOut
    invite: InviteOut


class InviteIn(BaseModel):
    role: MemberRole


class TextIn(BaseModel):
    text: str


class MessageIn(BaseModel):
    body: str


class ReadIn(BaseModel):
    message_id: int


def create_app(
    settings: Settings | None = None,
    clock: Clock = system_clock,
    services: Services | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.require("telegram_bot_token")
    bot_token = settings.telegram_bot_token.get_secret_value()
    max_age = timedelta(seconds=settings.init_data_max_age_seconds)
    upload_limit = settings.ownership_doc_max_mb * 1024 * 1024

    container: Container | None = None
    if services is None:
        container = Container(settings, clock)
        services = container.services
    svc = services

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if container is not None:
            await container.close()

    app = FastAPI(
        title="Домовик API",
        version=domovyk.__version__,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _unexpected_errors(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Несподівана помилка → JSON 500 з CORS-заголовками.

        Без цього браузер не бачить відповіді (CORS додається зовнішнім шаром), і Mini App
        показує «немає зв'язку», хоча сервер працює. Зареєстровано до CORS — тобто всередині.
        """
        try:
            return await call_next(request)
        except Exception:
            log.exception("Необроблена помилка API: %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"error": "internal"})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "ngrok-skip-browser-warning"],
        expose_headers=["X-Contract-Draft"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        code = next(
            (s for cls, s in _ERROR_STATUS if isinstance(exc, cls)),
            status.HTTP_400_BAD_REQUEST,
        )
        body: dict[str, object] = {"error": exc.code}
        for key in ("field", "reason"):
            if key in exc.details:
                body[key] = exc.details[key]
        return JSONResponse(status_code=code, content=body)

    def current_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> TelegramAuth:
        if not authorization or not authorization.startswith(_AUTH_SCHEME):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing")
        try:
            return validate_init_data(
                authorization.removeprefix(_AUTH_SCHEME),
                bot_token=bot_token,
                max_age=max_age,
                clock=clock,
            )
        except InitDataError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.reason) from exc

    Auth = Annotated[TelegramAuth, Depends(current_auth)]

    @app.get("/health")
    async def health() -> HealthOut:
        return HealthOut(status="ok", version=domovyk.__version__, env=settings.app_env)

    @app.get("/api/v1/me")
    async def me(auth: Auth) -> MeOut:
        return MeOut(
            telegram=TelegramUserOut(
                id=auth.user.id, first_name=auth.user.first_name, username=auth.user.username
            ),
            start_param=auth.start_param,
            user=await svc.users.find_by_telegram(auth.user.id),
            consent=svc.users.consent_info(),
        )

    @app.post("/api/v1/register", status_code=status.HTTP_201_CREATED)
    async def register(auth: Auth, body: RegisterIn) -> UserOut:
        return await svc.users.register(
            auth.user.id,
            body.display_name,
            terms_version=body.terms_version,
            privacy_version=body.privacy_version,
        )

    @app.patch("/api/v1/me")
    async def update_me(auth: Auth, body: NameIn) -> UserOut:
        return await svc.users.update_display_name(auth.user.id, body.display_name)

    @app.put("/api/v1/me/mode")
    async def set_mode(auth: Auth, body: ModeIn) -> UserOut:
        return await svc.users.set_mode(auth.user.id, body.mode)

    @app.delete("/api/v1/me", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_me(auth: Auth) -> Response:
        await svc.users.delete_account(auth.user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/me/tenant-profile")
    async def get_tenant_profile(auth: Auth) -> TenantProfile:
        return await svc.users.get_tenant_profile(auth.user.id)

    @app.put("/api/v1/me/tenant-profile")
    async def put_tenant_profile(auth: Auth, body: TenantProfile) -> TenantProfile:
        return await svc.users.update_tenant_profile(auth.user.id, body)

    # ------------------------------------------------ об'єкти (M2, FR-PRP-1..3)

    @app.get("/api/v1/cities")
    async def list_cities(auth: Auth) -> list[CityOut]:
        return await svc.properties.cities()

    @app.get("/api/v1/addresses")
    async def search_addresses(
        auth: Auth,
        q: Annotated[str, Query(max_length=200)],
        city: Annotated[str | None, Query(max_length=32)] = None,
    ) -> list[AddressCandidate]:
        return await svc.properties.search_address(auth.user.id, q, city)

    @app.get("/api/v1/properties")
    async def list_properties(auth: Auth) -> list[PropertyOut]:
        return await svc.properties.list_mine(auth.user.id)

    @app.post("/api/v1/properties", status_code=status.HTTP_201_CREATED)
    async def create_property(auth: Auth, body: PropertyCreate) -> PropertyOut:
        return await svc.properties.create_property(auth.user.id, body)

    @app.get("/api/v1/properties/{property_id}")
    async def get_property(auth: Auth, property_id: uuid.UUID) -> PropertyOut:
        return await svc.properties.get_mine(auth.user.id, property_id)

    @app.put("/api/v1/properties/{property_id}")
    async def update_property(
        auth: Auth, property_id: uuid.UUID, body: PropertyUpdate
    ) -> PropertyOut:
        return await svc.properties.update_details(auth.user.id, property_id, body)

    @app.delete("/api/v1/properties/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_property(auth: Auth, property_id: uuid.UUID) -> Response:
        await svc.properties.delete_property(auth.user.id, property_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.put("/api/v1/properties/{property_id}/details")
    async def update_property_profile(
        auth: Auth, property_id: uuid.UUID, body: PropertyDetails
    ) -> PropertyOut:
        return await svc.properties.update_profile(auth.user.id, property_id, body)

    @app.post("/api/v1/properties/{property_id}/rooms", status_code=status.HTTP_201_CREATED)
    async def add_room(auth: Auth, property_id: uuid.UUID, body: RoomIn) -> PropertyOut:
        return await svc.properties.add_room(auth.user.id, property_id, body)

    @app.delete("/api/v1/properties/{property_id}/rooms/{unit_id}")
    async def remove_room(auth: Auth, property_id: uuid.UUID, unit_id: uuid.UUID) -> PropertyOut:
        return await svc.properties.remove_room(auth.user.id, property_id, unit_id)

    # ------------------------------------------------- фото об'єкта (FR-PRP-10)

    @app.get("/api/v1/properties/{property_id}/photos")
    async def property_photos(auth: Auth, property_id: uuid.UUID) -> list[PhotoOut]:
        return await svc.properties.photos(auth.user.id, property_id)

    @app.post("/api/v1/properties/{property_id}/photos", status_code=status.HTTP_201_CREATED)
    async def add_property_photo(
        auth: Auth, property_id: uuid.UUID, file: Annotated[UploadFile, File()]
    ) -> list[PhotoOut]:
        data = await file.read(PHOTO_MAX_BYTES + 1)  # завелике відхилить сервіс
        return await svc.properties.add_photo(auth.user.id, property_id, data)

    @app.get("/api/v1/properties/{property_id}/photos/{photo_id}")
    async def property_photo(auth: Auth, property_id: uuid.UUID, photo_id: uuid.UUID) -> Response:
        return _photo(await svc.properties.photo_file(auth.user.id, property_id, photo_id))

    @app.delete("/api/v1/properties/{property_id}/photos/{photo_id}")
    async def delete_property_photo(
        auth: Auth, property_id: uuid.UUID, photo_id: uuid.UUID
    ) -> list[PhotoOut]:
        return await svc.properties.delete_photo(auth.user.id, property_id, photo_id)

    @app.post("/api/v1/properties/{property_id}/photos/{photo_id}/cover")
    async def property_photo_cover(
        auth: Auth, property_id: uuid.UUID, photo_id: uuid.UUID
    ) -> list[PhotoOut]:
        return await svc.properties.make_cover(auth.user.id, property_id, photo_id)

    # ------------------------------------------ власність (FR-PRP-4, FR-PRP-5)

    @app.get("/api/v1/properties/{property_id}/ownership")
    async def get_ownership(auth: Auth, property_id: uuid.UUID) -> OwnershipOut:
        return await svc.properties.ownership(auth.user.id, property_id)

    @app.post(
        "/api/v1/properties/{property_id}/ownership/documents",
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_ownership_document(
        auth: Auth, property_id: uuid.UUID, file: Annotated[UploadFile, File()]
    ) -> OwnershipOut:
        # Читаємо не більше ліміту + 1 байт: завеликий файл відхилить сервіс.
        data = await file.read(upload_limit + 1)
        return await svc.properties.upload_document(
            auth.user.id, property_id, file.filename or "document.pdf", data
        )

    @app.delete("/api/v1/properties/{property_id}/ownership/documents/{document_id}")
    async def remove_ownership_document(
        auth: Auth, property_id: uuid.UUID, document_id: uuid.UUID
    ) -> OwnershipOut:
        return await svc.properties.remove_document(auth.user.id, property_id, document_id)

    @app.post("/api/v1/properties/{property_id}/ownership/submit")
    async def submit_ownership(auth: Auth, property_id: uuid.UUID) -> OwnershipOut:
        return await svc.properties.submit_ownership(auth.user.id, property_id)

    @app.post("/api/v1/properties/{property_id}/ownership/dev-verify")
    async def dev_verify_ownership(auth: Auth, property_id: uuid.UUID) -> OwnershipOut:
        return await svc.properties.dev_verify(auth.user.id, property_id)

    # ---------------------------------------------- оренди (M3, FR-LSE-1..5)

    @app.get("/api/v1/leases")
    async def list_leases(auth: Auth) -> list[LeaseSummary]:
        return await svc.leases.list_mine(auth.user.id)

    @app.post("/api/v1/leases", status_code=status.HTTP_201_CREATED)
    async def create_lease(auth: Auth, body: LeaseCreate) -> LeaseCreatedOut:
        lease, invite = await svc.leases.create_lease(auth.user.id, body)
        return LeaseCreatedOut(lease=lease, invite=invite)

    @app.get("/api/v1/leases/{lease_id}")
    async def get_lease(auth: Auth, lease_id: uuid.UUID) -> LeaseOut:
        return await svc.leases.get(auth.user.id, lease_id)

    @app.get("/api/v1/leases/{lease_id}/photos")
    async def lease_photos(auth: Auth, lease_id: uuid.UUID) -> list[PhotoOut]:
        return await svc.leases.photos(auth.user.id, lease_id)

    @app.get("/api/v1/leases/{lease_id}/photos/{photo_id}")
    async def lease_photo(auth: Auth, lease_id: uuid.UUID, photo_id: uuid.UUID) -> Response:
        return _photo(await svc.leases.photo(auth.user.id, lease_id, photo_id))

    @app.put("/api/v1/leases/{lease_id}/conditions")
    async def update_lease_conditions(
        auth: Auth, lease_id: uuid.UUID, body: LeaseConditions
    ) -> LeaseOut:
        return await svc.leases.update_conditions(auth.user.id, lease_id, body)

    @app.post("/api/v1/leases/{lease_id}/accept-terms")
    async def accept_lease_terms(auth: Auth, lease_id: uuid.UUID) -> LeaseOut:
        return await svc.leases.accept_terms(auth.user.id, lease_id)

    @app.post("/api/v1/leases/{lease_id}/termination")
    async def request_termination(auth: Auth, lease_id: uuid.UUID, body: TerminationIn) -> LeaseOut:
        return await svc.leases.request_termination(auth.user.id, lease_id, body)

    @app.delete("/api/v1/leases/{lease_id}/termination")
    async def withdraw_termination(auth: Auth, lease_id: uuid.UUID) -> LeaseOut:
        return await svc.leases.withdraw_termination(auth.user.id, lease_id)

    @app.post("/api/v1/leases/{lease_id}/proposal")
    async def propose_changes(auth: Auth, lease_id: uuid.UUID, body: ProposalIn) -> LeaseOut:
        return await svc.leases.propose_changes(auth.user.id, lease_id, body)

    @app.post("/api/v1/leases/{lease_id}/proposal/respond")
    async def respond_proposal(auth: Auth, lease_id: uuid.UUID, body: RespondIn) -> LeaseOut:
        return await svc.leases.respond_proposal(auth.user.id, lease_id, body.accept)

    @app.post("/api/v1/leases/{lease_id}/request-changes")
    async def request_lease_changes(auth: Auth, lease_id: uuid.UUID, body: TextIn) -> LeaseOut:
        return await svc.leases.request_changes(auth.user.id, lease_id, body.text)

    @app.post("/api/v1/leases/{lease_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
    async def leave_lease(auth: Auth, lease_id: uuid.UUID) -> Response:
        await svc.leases.decline(auth.user.id, lease_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/api/v1/leases/{lease_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def hide_lease(auth: Auth, lease_id: uuid.UUID) -> Response:
        await svc.leases.hide(auth.user.id, lease_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/leases/{lease_id}/cancel")
    async def cancel_lease(auth: Auth, lease_id: uuid.UUID) -> LeaseOut:
        return await svc.leases.cancel(auth.user.id, lease_id)

    @app.post("/api/v1/leases/{lease_id}/invites", status_code=status.HTTP_201_CREATED)
    async def new_lease_invite(auth: Auth, lease_id: uuid.UUID, body: InviteIn) -> InviteOut:
        return await svc.leases.new_invite(auth.user.id, lease_id, body.role)

    @app.delete("/api/v1/leases/{lease_id}/invites/{role}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_lease_invite(auth: Auth, lease_id: uuid.UUID, role: MemberRole) -> Response:
        await svc.leases.revoke_invite(auth.user.id, lease_id, role)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/invites/{token}")
    async def preview_invite(auth: Auth, token: str) -> InvitePreview:
        return await svc.leases.preview_invite(auth.user.id, token)

    @app.post("/api/v1/invites/{token}/accept")
    async def accept_invite(auth: Auth, token: str) -> LeaseOut:
        return await svc.leases.accept_invite(auth.user.id, token)

    # ------------------------------------------------- чат оренди (FR-CHT-1)

    @app.get("/api/v1/leases/{lease_id}/messages")
    async def lease_messages(
        auth: Auth,
        lease_id: uuid.UUID,
        after_id: Annotated[int, Query(ge=0)] = 0,
        before_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> MessagesPage:
        return await svc.leases.messages(
            auth.user.id, lease_id, after_id=after_id, before_id=before_id
        )

    @app.post("/api/v1/leases/{lease_id}/messages", status_code=status.HTTP_201_CREATED)
    async def post_lease_message(auth: Auth, lease_id: uuid.UUID, body: MessageIn) -> MessageOut:
        return await svc.leases.post_message(auth.user.id, lease_id, body.body)

    @app.post("/api/v1/leases/{lease_id}/read", status_code=status.HTTP_204_NO_CONTENT)
    async def mark_lease_read(auth: Auth, lease_id: uuid.UUID, body: ReadIn) -> Response:
        await svc.leases.mark_read(auth.user.id, lease_id, body.message_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/leases/{lease_id}/notifications")
    async def lease_notification_prefs(auth: Auth, lease_id: uuid.UUID) -> LeaseNotificationPrefs:
        return await svc.leases.notification_prefs(auth.user.id, lease_id)

    @app.put("/api/v1/leases/{lease_id}/notifications")
    async def set_lease_notification_prefs(
        auth: Auth, lease_id: uuid.UUID, body: LeaseNotificationPrefs
    ) -> LeaseNotificationPrefs:
        return await svc.leases.set_notification_prefs(auth.user.id, lease_id, body)

    # ------------------------------------------------- модерація (MOD-OWN)

    @app.get("/api/v1/moderation/summary")
    async def moderation_summary(auth: Auth) -> ModerationSummaryOut:
        return ModerationSummaryOut(
            ownership_pending=await svc.properties.pending_count(auth.user.id),
            signatures_pending=await svc.contracts.review_count(auth.user.id),
        )

    @app.get("/api/v1/moderation/ownership")
    async def ownership_queue(auth: Auth) -> list[QueueItem]:
        return await svc.properties.verification_queue(auth.user.id)

    @app.get("/api/v1/moderation/ownership/{verification_id}")
    async def ownership_case(auth: Auth, verification_id: uuid.UUID) -> VerificationCase:
        return await svc.properties.verification_case(auth.user.id, verification_id)

    @app.post("/api/v1/moderation/ownership/{verification_id}/send-documents")
    async def send_ownership_documents(auth: Auth, verification_id: uuid.UUID) -> SentOut:
        sent = await svc.properties.send_documents_to_chat(auth.user.id, verification_id)
        return SentOut(sent=sent)

    @app.post("/api/v1/moderation/ownership/{verification_id}/approve")
    async def approve_ownership(
        auth: Auth, verification_id: uuid.UUID, body: ApproveIn
    ) -> VerificationCase:
        return await svc.properties.approve(auth.user.id, verification_id, body.owner_name)

    @app.post("/api/v1/moderation/ownership/{verification_id}/reject")
    async def reject_ownership(
        auth: Auth, verification_id: uuid.UUID, body: RejectIn
    ) -> VerificationCase:
        return await svc.properties.reject(auth.user.id, verification_id, body.reason)

    # ------------------------------------------------ модерація підписів (ADR 0010)

    @app.get("/api/v1/moderation/signatures")
    async def signature_queue(auth: Auth) -> list[SignatureCase]:
        return await svc.contracts.review_queue(auth.user.id)

    @app.get("/api/v1/moderation/signatures/{signature_id}")
    async def signature_case(auth: Auth, signature_id: uuid.UUID) -> SignatureCase:
        return await svc.contracts.review_case(auth.user.id, signature_id)

    @app.post("/api/v1/moderation/signatures/{signature_id}/send-files")
    async def send_signature_files(auth: Auth, signature_id: uuid.UUID) -> SentOut:
        return SentOut(sent=await svc.contracts.send_review_files(auth.user.id, signature_id))

    @app.post("/api/v1/moderation/signatures/{signature_id}/review")
    async def review_signature(
        auth: Auth, signature_id: uuid.UUID, body: ReviewIn
    ) -> SignatureCase:
        return await svc.contracts.review_signature(
            auth.user.id, signature_id, body.approve, body.reason
        )

    # ------------------------------------------------------ договори (M4, FR-CTR)

    # ------------------------------------------------------- оплати (M5, FR-PAY)

    @app.get("/api/v1/leases/{lease_id}/payments")
    async def payments_overview(auth: Auth, lease_id: uuid.UUID) -> PaymentsOverview:
        return await svc.payments.overview(auth.user.id, lease_id)

    @app.get("/api/v1/charges/{charge_id}/pay-info")
    async def pay_info(auth: Auth, charge_id: uuid.UUID) -> PayInfo:
        return await svc.payments.pay_info(auth.user.id, charge_id)

    @app.post("/api/v1/charges/{charge_id}/cash")
    async def report_cash(auth: Auth, charge_id: uuid.UUID, body: CashIn) -> PaymentsOverview:
        return await svc.payments.report_cash(auth.user.id, charge_id, body)

    @app.post("/api/v1/charges/{charge_id}/receipt")
    async def report_receipt(
        auth: Auth,
        charge_id: uuid.UUID,
        file: Annotated[UploadFile, File()],
        amount: Annotated[Decimal, Form(gt=0)],
        note: Annotated[str | None, Form(max_length=300)] = None,
    ) -> PaymentsOverview:
        data = await file.read(RECEIPT_MAX_BYTES + 1)
        return await svc.payments.report_receipt(
            auth.user.id, charge_id, amount, file.filename or "kvytantsiia", data, note
        )

    @app.post("/api/v1/charges/{charge_id}/received")
    async def mark_received(auth: Auth, charge_id: uuid.UUID, body: CashIn) -> PaymentsOverview:
        return await svc.payments.mark_received(auth.user.id, charge_id, body)

    @app.post("/api/v1/payments/{payment_id}/decision")
    async def decide_payment(
        auth: Auth, payment_id: uuid.UUID, body: DecisionIn
    ) -> PaymentsOverview:
        return await svc.payments.decide(auth.user.id, payment_id, body)

    @app.get("/api/v1/payments/{payment_id}/receipt")
    async def payment_receipt(auth: Auth, payment_id: uuid.UUID) -> Response:
        name, data, mime = await svc.payments.receipt(auth.user.id, payment_id)
        response = _file(data, name, mime)
        response.headers["Content-Disposition"] = response.headers["Content-Disposition"].replace(
            "attachment", "inline", 1
        )
        return response

    @app.post("/api/v1/payments/{payment_id}/send")
    async def send_receipt(auth: Auth, payment_id: uuid.UUID) -> SentOut:
        return SentOut(sent=await svc.payments.send_receipt_to_chat(auth.user.id, payment_id))

    @app.get("/api/v1/leases/{lease_id}/contract")
    async def contract_state(auth: Auth, lease_id: uuid.UUID) -> ContractState:
        return await svc.contracts.state(auth.user.id, lease_id)

    @app.put("/api/v1/leases/{lease_id}/contract/name")
    async def contract_name(auth: Auth, lease_id: uuid.UUID, body: LegalNameIn) -> ContractState:
        return await svc.contracts.set_legal_name(auth.user.id, lease_id, body.full_name)

    @app.get("/api/v1/leases/{lease_id}/contract/preview")
    async def contract_preview(auth: Auth, lease_id: uuid.UUID) -> Response:
        """PDF для перегляду в застосунку (FR-LSE-10): договір або чернетка з умов."""
        name, data, draft = await svc.contracts.preview(auth.user.id, lease_id)
        response = _file(data, name, "application/pdf")
        response.headers["Content-Disposition"] = response.headers["Content-Disposition"].replace(
            "attachment", "inline", 1
        )
        response.headers["X-Contract-Draft"] = "1" if draft else "0"
        return response

    @app.post("/api/v1/leases/{lease_id}/contract/generate")
    async def contract_generate(auth: Auth, lease_id: uuid.UUID) -> ContractState:
        return await svc.contracts.generate(auth.user.id, lease_id)

    @app.post("/api/v1/leases/{lease_id}/contract/external")
    async def contract_external(
        auth: Auth, lease_id: uuid.UUID, file: Annotated[UploadFile, File()]
    ) -> ContractState:
        data = await file.read(EXTERNAL_PDF_MAX_BYTES + 1)
        return await svc.contracts.upload_external(
            auth.user.id, lease_id, file.filename or "dogovir.pdf", data
        )

    @app.post("/api/v1/contracts/{contract_id}/signature")
    async def contract_signature(
        auth: Auth, contract_id: uuid.UUID, file: Annotated[UploadFile, File()]
    ) -> ContractState:
        data = await file.read(SIGNATURE_MAX_BYTES + 1)
        return await svc.contracts.upload_signature(
            auth.user.id, contract_id, file.filename or "signature.p7s", data
        )

    @app.post("/api/v1/contracts/{contract_id}/external-decision")
    async def contract_external_decision(
        auth: Auth, contract_id: uuid.UUID, body: ExternalDecisionIn
    ) -> ContractState:
        return await svc.contracts.decide_external(auth.user.id, contract_id, body.accept)

    @app.post("/api/v1/contracts/{contract_id}/send")
    async def contract_send(auth: Auth, contract_id: uuid.UUID, body: SendFilesIn) -> SentOut:
        return SentOut(sent=await svc.contracts.send_to_chat(auth.user.id, contract_id, body.what))

    @app.get("/api/v1/contracts/{contract_id}/pdf")
    async def contract_pdf(auth: Auth, contract_id: uuid.UUID) -> Response:
        name, data = await svc.contracts.document(auth.user.id, contract_id)
        return _file(data, name, "application/pdf")

    @app.get("/api/v1/contracts/{contract_id}/package")
    async def contract_package(auth: Auth, contract_id: uuid.UUID) -> Response:
        name, data = await svc.contracts.package(auth.user.id, contract_id)
        return _file(data, name, "application/zip")

    return app


def _photo(data: bytes) -> Response:
    """Фото за ID не змінюється: браузер може тримати його в кеші (лише свому)."""
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


def _file(data: bytes, filename: str, media_type: str) -> Response:
    """Файл для завантаження (сайт, Desktop). Ім'я — ASCII-безпечне + UTF-8 за RFC 5987."""
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode() or "file"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )
