"""FastAPI-застосунок для Mini App. Тонкий: лише виклики сервісів модулів.

Без `from __future__ import annotations`: FastAPI має бачити локальні залежності (Auth).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
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
)
from domovyk.interfaces.api.telegram_auth import (
    InitDataError,
    TelegramAuth,
    validate_init_data,
)
from domovyk.modules.users.service import ConsentInfo, TenantProfile, UserOut

_AUTH_SCHEME = "tma "

# Порядок важливий: перший збіг isinstance визначає HTTP-статус.
_ERROR_STATUS: list[tuple[type[DomainError], int]] = [
    (NotFound, status.HTTP_404_NOT_FOUND),
    (NotRegistered, status.HTTP_403_FORBIDDEN),
    (PermissionDenied, status.HTTP_403_FORBIDDEN),
    (InvalidInput, 422),
    (Conflict, status.HTTP_409_CONFLICT),
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


def create_app(
    settings: Settings | None = None,
    clock: Clock = system_clock,
    services: Services | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.require("telegram_bot_token")
    bot_token = settings.telegram_bot_token.get_secret_value()
    max_age = timedelta(seconds=settings.init_data_max_age_seconds)

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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "ngrok-skip-browser-warning"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        code = next(
            (s for cls, s in _ERROR_STATUS if isinstance(exc, cls)),
            status.HTTP_400_BAD_REQUEST,
        )
        body: dict[str, object] = {"error": exc.code}
        if "field" in exc.details:
            body["field"] = exc.details["field"]
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

    return app
