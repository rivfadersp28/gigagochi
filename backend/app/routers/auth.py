from __future__ import annotations

import hashlib
import ipaddress
import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.config import get_settings
from app.dependencies import get_google_account_identity, get_google_auth_service
from app.services.google_auth_service import (
    GoogleAuthNotConfiguredError,
    GoogleAuthService,
    GoogleCredentialRejectedError,
    GoogleRefreshRejectedError,
    GuestInstallationRejectedError,
)
from app.services.google_auth_session_store import GoogleUserIdentity, IssuedAuthSession
from app.services.rate_limit_service import RateLimitExceeded, get_rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["android-auth"])
AuthService = Annotated[GoogleAuthService, Depends(get_google_auth_service)]
AccountIdentity = Annotated[GoogleUserIdentity, Depends(get_google_account_identity)]


class GoogleAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_token: SecretStr = Field(alias="idToken", min_length=1, max_length=16 * 1_024)
    nonce: str = Field(min_length=22, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class RefreshSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: SecretStr = Field(alias="refreshToken", min_length=1, max_length=1_024)


class GuestAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    installation_id: str = Field(alias="installationId", min_length=36, max_length=36)


class AuthSessionResponse(BaseModel):
    accessToken: str
    refreshToken: str
    expiresAt: int


class AccountIdentityResponse(BaseModel):
    accountId: str


class GuestAuthSessionResponse(AuthSessionResponse):
    accountId: str


def _response(session: IssuedAuthSession, response: Response) -> AuthSessionResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return AuthSessionResponse(
        accessToken=session.access_token.reveal(),
        refreshToken=session.refresh_token.reveal(),
        expiresAt=session.expires_at_ms,
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTH_INVALID", "message": "Не удалось подтвердить сессию."},
    )


def _invalid_guest_request() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "AUTH_GUEST_INVALID",
            "message": "Не удалось создать локальную сессию.",
        },
    )


def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    candidate = forwarded or (request.client.host if request.client else "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def _rate_limit(
    request: Request,
    bucket: str,
    key: str,
    limit: int,
    window: timedelta,
) -> None:
    try:
        get_rate_limiter(get_settings().rate_limit_store_path).check_fixed_window(
            bucket,
            key,
            limit,
            window,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Слишком много запросов."},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None


@router.post("/guest", response_model=GuestAuthSessionResponse, include_in_schema=False)
def create_guest_session(
    request: Request,
    response: Response,
    service: AuthService,
    raw_payload: Annotated[object, Body()],
) -> GuestAuthSessionResponse:
    try:
        payload = GuestAuthRequest.model_validate(raw_payload)
        settings = get_settings()
        _rate_limit(
            request,
            "auth-guest-ip",
            _request_ip(request),
            settings.auth_guest_ip_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        _rate_limit(
            request,
            "auth-guest-installation",
            hashlib.sha256(payload.installation_id.encode("utf-8")).hexdigest(),
            settings.auth_guest_installation_rate_limit_per_hour,
            timedelta(hours=1),
        )
        identity, session = service.exchange_guest_installation(payload.installation_id)
    except (ValidationError, GuestInstallationRejectedError) as exc:
        raise _invalid_guest_request() from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("guest_auth_exchange_failed exception=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_UNAVAILABLE", "message": "Сервис временно недоступен."},
        ) from exc

    session_response = _response(session, response)
    return GuestAuthSessionResponse(
        accountId=identity.account_id,
        accessToken=session_response.accessToken,
        refreshToken=session_response.refreshToken,
        expiresAt=session_response.expiresAt,
    )


@router.post("/google", response_model=AuthSessionResponse, include_in_schema=False)
def exchange_google_credential(
    payload: GoogleAuthRequest,
    request: Request,
    response: Response,
    service: AuthService,
) -> AuthSessionResponse:
    try:
        settings = get_settings()
        _rate_limit(
            request,
            "auth-google-ip",
            _request_ip(request),
            settings.auth_google_ip_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        session = service.exchange_google_credential(
            id_token=payload.id_token.get_secret_value(),
            nonce=payload.nonce,
        )
    except GoogleAuthNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Вход временно недоступен."},
        ) from exc
    except GoogleCredentialRejectedError as exc:
        raise _unauthorized() from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("google_auth_exchange_failed exception=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_UNAVAILABLE", "message": "Вход временно недоступен."},
        ) from exc
    return _response(session, response)


@router.post("/refresh", response_model=AuthSessionResponse, include_in_schema=False)
def refresh_session(
    payload: RefreshSessionRequest,
    request: Request,
    response: Response,
    service: AuthService,
) -> AuthSessionResponse:
    try:
        settings = get_settings()
        _rate_limit(
            request,
            "auth-refresh-ip",
            _request_ip(request),
            settings.auth_refresh_ip_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        session = service.refresh(payload.refresh_token.get_secret_value())
    except GoogleRefreshRejectedError as exc:
        raise _unauthorized() from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("google_auth_refresh_failed exception=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_UNAVAILABLE", "message": "Вход временно недоступен."},
        ) from exc
    return _response(session, response)


@router.get("/me", response_model=AccountIdentityResponse, include_in_schema=False)
def current_account(
    response: Response,
    identity: AccountIdentity,
) -> AccountIdentityResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return AccountIdentityResponse(accountId=identity.account_id)
