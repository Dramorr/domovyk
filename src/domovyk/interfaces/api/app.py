"""FastAPI-застосунок для Mini App. Тонкий: лише виклики сервісів модулів.

Без `from __future__ import annotations`: FastAPI має бачити локальні залежності (Auth).
"""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import domovyk
from domovyk.config import Settings, get_settings
from domovyk.core.clock import Clock, system_clock
from domovyk.core.errors import Conflict, DomainError, InvalidInput, NotFound, PermissionDenied
from domovyk.interfaces.api.telegram_auth import (
    InitDataError,
    TelegramAuth,
    validate_init_data,
)

_AUTH_SCHEME = "tma "

_ERROR_STATUS: dict[type[DomainError], int] = {
    NotFound: status.HTTP_404_NOT_FOUND,
    PermissionDenied: status.HTTP_403_FORBIDDEN,
    InvalidInput: 422,
    Conflict: status.HTTP_409_CONFLICT,
}


class HealthOut(BaseModel):
    status: str
    version: str
    env: str


class MeOut(BaseModel):
    telegram_id: int
    first_name: str
    last_name: str | None
    username: str | None
    start_param: str | None


def create_app(settings: Settings | None = None, clock: Clock = system_clock) -> FastAPI:
    settings = settings or get_settings()
    settings.require("telegram_bot_token")
    bot_token = settings.telegram_bot_token.get_secret_value()
    max_age = timedelta(seconds=settings.init_data_max_age_seconds)

    app = FastAPI(
        title="Домовик API",
        version=domovyk.__version__,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
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
            (s for cls, s in _ERROR_STATUS.items() if isinstance(exc, cls)),
            status.HTTP_400_BAD_REQUEST,
        )
        return JSONResponse(status_code=code, content={"error": exc.code})

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
        # M0: дані лише з initData. З M1 — профіль з modules/users/service.py.
        return MeOut(
            telegram_id=auth.user.id,
            first_name=auth.user.first_name,
            last_name=auth.user.last_name,
            username=auth.user.username,
            start_param=auth.start_param,
        )

    return app
