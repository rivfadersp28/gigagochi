from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.dependencies import get_google_account_identity, get_google_auth_service
from app.services.google_auth_service import (
    GoogleAuthNotConfiguredError,
    GoogleAuthService,
    GoogleCredentialRejectedError,
    GoogleRefreshRejectedError,
)
from app.services.google_auth_session_store import GoogleUserIdentity, IssuedAuthSession

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


class AuthSessionResponse(BaseModel):
    accessToken: str
    refreshToken: str
    expiresAt: int


class AccountIdentityResponse(BaseModel):
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


@router.post("/google", response_model=AuthSessionResponse, include_in_schema=False)
def exchange_google_credential(
    payload: GoogleAuthRequest,
    response: Response,
    service: AuthService,
) -> AuthSessionResponse:
    try:
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
    except Exception as exc:
        logger.exception("google_auth_exchange_failed exception=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_UNAVAILABLE", "message": "Вход временно недоступен."},
        ) from exc
    return _response(session, response)


@router.post("/refresh", response_model=AuthSessionResponse, include_in_schema=False)
def refresh_session(
    payload: RefreshSessionRequest,
    response: Response,
    service: AuthService,
) -> AuthSessionResponse:
    try:
        session = service.refresh(payload.refresh_token.get_secret_value())
    except GoogleRefreshRejectedError as exc:
        raise _unauthorized() from exc
    except Exception as exc:
        logger.exception("google_auth_refresh_failed exception=%s", type(exc).__name__)
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
