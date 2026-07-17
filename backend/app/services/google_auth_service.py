from __future__ import annotations

import hmac
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from app.services.google_auth_session_store import (
    GoogleAuthSessionStore,
    GoogleUserIdentity,
    InvalidRefreshTokenError,
    IssuedAuthSession,
)

GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,256}$")
MAX_ID_TOKEN_LENGTH = 16 * 1_024
MAX_SUBJECT_LENGTH = 255


class GoogleTokenVerifier(Protocol):
    def verify(self, id_token: str, *, audience: str) -> Mapping[str, Any]: ...


class OfficialGoogleTokenVerifier:
    """Signature/audience/issuer/expiry verification via Google's maintained library."""

    def __init__(self, request: GoogleAuthRequest | None = None) -> None:
        self._request = request or GoogleAuthRequest()

    def verify(self, id_token: str, *, audience: str) -> Mapping[str, Any]:
        return google_id_token.verify_oauth2_token(
            id_token,
            self._request,
            audience=audience,
        )


class GoogleAuthError(RuntimeError):
    pass


class GoogleAuthNotConfiguredError(GoogleAuthError):
    pass


class GoogleCredentialRejectedError(GoogleAuthError):
    pass


class GoogleRefreshRejectedError(GoogleAuthError):
    pass


class GoogleAuthService:
    def __init__(
        self,
        *,
        web_client_id: str | None,
        store: GoogleAuthSessionStore,
        verifier: GoogleTokenVerifier,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
        clock_seconds: Callable[[], float] = time.time,
    ) -> None:
        self._web_client_id = (web_client_id or "").strip()
        self._store = store
        self._verifier = verifier
        self._access_ttl_seconds = access_ttl_seconds
        self._refresh_ttl_seconds = refresh_ttl_seconds
        self._clock_seconds = clock_seconds

    def exchange_google_credential(
        self,
        *,
        id_token: str,
        nonce: str,
    ) -> IssuedAuthSession:
        client_id = self._web_client_id
        if not client_id:
            raise GoogleAuthNotConfiguredError("google auth is not configured")
        if not id_token or len(id_token) > MAX_ID_TOKEN_LENGTH:
            raise GoogleCredentialRejectedError("google credential was rejected")
        if NONCE_PATTERN.fullmatch(nonce) is None:
            raise GoogleCredentialRejectedError("google credential was rejected")

        try:
            claims = self._verifier.verify(id_token, audience=client_id)
        except Exception as exc:
            raise GoogleCredentialRejectedError("google credential was rejected") from exc

        identity = self._validate_claims(claims, expected_audience=client_id, nonce=nonce)
        _, session = self._store.issue_for_google_user(
            provider_subject=identity.provider_subject,
            email=identity.email,
            display_name=identity.display_name,
            now_ms=self._now_ms(),
            access_ttl_seconds=self._access_ttl_seconds,
            refresh_ttl_seconds=self._refresh_ttl_seconds,
        )
        return session

    def refresh(self, refresh_token: str) -> IssuedAuthSession:
        if not refresh_token or len(refresh_token) > 1_024:
            raise GoogleRefreshRejectedError("refresh token was rejected")
        try:
            return self._store.rotate_refresh_token(
                refresh_token,
                now_ms=self._now_ms(),
                access_ttl_seconds=self._access_ttl_seconds,
                refresh_ttl_seconds=self._refresh_ttl_seconds,
            )
        except InvalidRefreshTokenError as exc:
            raise GoogleRefreshRejectedError("refresh token was rejected") from exc

    def authenticate_access_token(self, access_token: str) -> GoogleUserIdentity | None:
        if not access_token or len(access_token) > 1_024:
            return None
        return self._store.identity_for_access_token(access_token, now_ms=self._now_ms())

    def revoke_refresh_token(self, refresh_token: str) -> bool:
        if not refresh_token or len(refresh_token) > 1_024:
            return False
        return self._store.revoke_refresh_token(refresh_token, now_ms=self._now_ms())

    def _validate_claims(
        self,
        claims: Mapping[str, Any],
        *,
        expected_audience: str,
        nonce: str,
    ) -> GoogleUserIdentity:
        issuer = claims.get("iss")
        audience = claims.get("aud")
        expires_at = claims.get("exp")
        subject = claims.get("sub")
        token_nonce = claims.get("nonce")

        if not isinstance(issuer, str) or issuer not in GOOGLE_ISSUERS:
            raise GoogleCredentialRejectedError("google credential was rejected")
        if not isinstance(audience, str) or not hmac.compare_digest(
            audience,
            expected_audience,
        ):
            raise GoogleCredentialRejectedError("google credential was rejected")
        if isinstance(expires_at, bool) or not isinstance(expires_at, int):
            raise GoogleCredentialRejectedError("google credential was rejected")
        if expires_at <= self._clock_seconds():
            raise GoogleCredentialRejectedError("google credential was rejected")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > MAX_SUBJECT_LENGTH
            or not subject.isascii()
        ):
            raise GoogleCredentialRejectedError("google credential was rejected")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
            raise GoogleCredentialRejectedError("google credential was rejected")

        email_value = claims.get("email")
        email = (
            email_value.strip()
            if claims.get("email_verified") is True
            and isinstance(email_value, str)
            and 0 < len(email_value.strip()) <= 320
            else None
        )
        name_value = claims.get("name")
        display_name = (
            name_value.strip()
            if isinstance(name_value, str) and 0 < len(name_value.strip()) <= 255
            else None
        )
        return GoogleUserIdentity(
            user_id=0,
            account_id="",
            provider_subject=subject,
            email=email,
            display_name=display_name,
        )

    def _now_ms(self) -> int:
        return int(self._clock_seconds() * 1_000)
