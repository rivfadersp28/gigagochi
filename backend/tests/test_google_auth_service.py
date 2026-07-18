from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from app.services.google_auth_service import (
    GoogleAuthService,
    GoogleCredentialRejectedError,
    GoogleRefreshRejectedError,
)
from app.services.google_auth_session_store import (
    GoogleAuthSessionStore,
    InvalidRefreshTokenError,
)

NOW_SECONDS = 1_800_000_000.0
CLIENT_ID = "android-web-client.apps.googleusercontent.com"
NONCE = "abcdefghijklmnopqrstuvwxyz0123456789_-nonce"


class FakeVerifier:
    def __init__(self, claims: Mapping[str, Any]) -> None:
        self.claims = claims
        self.requests: list[tuple[str, str]] = []

    def verify(self, id_token: str, *, audience: str) -> Mapping[str, Any]:
        self.requests.append((id_token, audience))
        return self.claims


class TokenSequence:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"opaque-secret-token-{self.index:04d}"


def valid_claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(NOW_SECONDS + 3_600),
        "sub": "immutable-google-subject",
        "nonce": NONCE,
        "email": "person@example.com",
        "email_verified": True,
        "name": "Person Name",
    }
    claims.update(overrides)
    return claims


def auth_service(
    tmp_path: Path,
    *,
    claims: Mapping[str, Any] | None = None,
) -> tuple[GoogleAuthService, GoogleAuthSessionStore, FakeVerifier]:
    verifier = FakeVerifier(claims or valid_claims())
    store = GoogleAuthSessionStore(
        tmp_path / "auth.sqlite3",
        token_factory=TokenSequence(),
    )
    service = GoogleAuthService(
        web_client_id=CLIENT_ID,
        store=store,
        verifier=verifier,
        access_ttl_seconds=900,
        refresh_ttl_seconds=30 * 24 * 60 * 60,
        clock_seconds=lambda: NOW_SECONDS,
    )
    return service, store, verifier


def test_google_login_issues_opaque_session_and_stores_no_plaintext_tokens(
    tmp_path: Path,
) -> None:
    service, store, verifier = auth_service(tmp_path)

    session = service.exchange_google_credential(id_token="signed-google-id-token", nonce=NONCE)

    assert verifier.requests == [("signed-google-id-token", CLIENT_ID)]
    assert session.expires_at_ms == int(NOW_SECONDS * 1_000) + 900_000
    identity = service.authenticate_access_token(session.access_token.reveal())
    assert identity is not None
    assert identity.provider_subject == "immutable-google-subject"
    assert identity.account_id.startswith("acct_")
    assert identity.email == "person@example.com"
    assert identity.display_name == "Person Name"

    stored_bytes = b"".join(
        path.read_bytes()
        for path in store.path.parent.glob(f"{store.path.name}*")
        if path.is_file()
    )
    for secret in (
        "signed-google-id-token",
        NONCE,
        session.access_token.reveal(),
        session.refresh_token.reveal(),
    ):
        assert secret.encode() not in stored_bytes


@pytest.mark.parametrize(
    "claims",
    [
        valid_claims(aud="wrong-client.apps.googleusercontent.com"),
        valid_claims(iss="https://attacker.example"),
        valid_claims(exp=int(NOW_SECONDS)),
        valid_claims(exp=float("nan")),
        valid_claims(nonce=None),
        valid_claims(nonce="wrong-nonce-value-abcdefghijklmnop"),
    ],
    ids=["audience", "issuer", "expired", "non-integer-exp", "missing-nonce", "wrong-nonce"],
)
def test_google_claim_validation_fails_closed(
    tmp_path: Path,
    claims: Mapping[str, Any],
) -> None:
    service, _, _ = auth_service(tmp_path, claims=claims)

    with pytest.raises(GoogleCredentialRejectedError):
        service.exchange_google_credential(id_token="signed-token", nonce=NONCE)


def test_duplicate_google_login_reuses_identity_key_not_email(tmp_path: Path) -> None:
    service, store, verifier = auth_service(tmp_path)
    first = service.exchange_google_credential(id_token="first-id-token", nonce=NONCE)
    verifier.claims = valid_claims(email="renamed@example.com", name="Renamed")
    second = service.exchange_google_credential(id_token="second-id-token", nonce=NONCE)

    with sqlite3.connect(store.path) as connection:
        user_count = connection.execute("SELECT COUNT(*) FROM google_auth_users").fetchone()[0]
        session_count = connection.execute(
            "SELECT COUNT(*) FROM google_auth_sessions"
        ).fetchone()[0]
        account_id, provider, subject, email = connection.execute(
            "SELECT account_id, provider, provider_subject, email FROM google_auth_users"
        ).fetchone()

    assert user_count == 1
    assert session_count == 2
    assert account_id.startswith("acct_")
    assert (provider, subject, email) == (
        "google",
        "immutable-google-subject",
        "renamed@example.com",
    )
    assert first.access_token.reveal() != second.access_token.reveal()


def test_guest_installation_stores_only_digest_and_no_personal_metadata(
    tmp_path: Path,
) -> None:
    service, store, verifier = auth_service(tmp_path)
    installation_id = "123e4567-e89b-42d3-a456-426614174000"

    identity, session = service.exchange_guest_installation(installation_id)

    assert verifier.requests == []
    assert identity.account_id.startswith("acct_")
    assert identity.provider_subject.startswith("guest:")
    assert identity.email is None
    assert identity.display_name is None
    assert service.authenticate_access_token(session.access_token.reveal()) == identity
    with sqlite3.connect(store.path) as connection:
        provider, subject, email, display_name = connection.execute(
            "SELECT provider, provider_subject, email, display_name "
            "FROM google_auth_users WHERE account_id = ?",
            (identity.account_id,),
        ).fetchone()
    assert provider == "google"
    assert subject == identity.provider_subject
    assert email is None
    assert display_name is None
    stored_bytes = b"".join(
        path.read_bytes()
        for path in store.path.parent.glob(f"{store.path.name}*")
        if path.is_file()
    )
    assert installation_id.encode() not in stored_bytes


def test_refresh_rotation_is_atomic_and_replay_is_rejected(tmp_path: Path) -> None:
    service, _, _ = auth_service(tmp_path)
    initial = service.exchange_google_credential(id_token="signed-token", nonce=NONCE)
    old_access = initial.access_token.reveal()
    old_refresh = initial.refresh_token.reveal()

    rotated = service.refresh(old_refresh)

    assert service.authenticate_access_token(old_access) is None
    assert service.authenticate_access_token(rotated.access_token.reveal()) is not None
    assert rotated.refresh_token.reveal() != old_refresh
    with pytest.raises(GoogleRefreshRejectedError):
        service.refresh(old_refresh)


def test_access_and_refresh_expiry_fail_closed(tmp_path: Path) -> None:
    token_sequence = TokenSequence()
    store = GoogleAuthSessionStore(tmp_path / "expiry.sqlite3", token_factory=token_sequence)
    _, session = store.issue_for_google_user(
        provider_subject="subject",
        email=None,
        display_name=None,
        now_ms=1_000,
        access_ttl_seconds=1,
        refresh_ttl_seconds=2,
    )

    assert store.identity_for_access_token(session.access_token.reveal(), now_ms=1_999) is not None
    assert store.identity_for_access_token(session.access_token.reveal(), now_ms=2_000) is None
    with pytest.raises(InvalidRefreshTokenError):
        store.rotate_refresh_token(
            session.refresh_token.reveal(),
            now_ms=3_000,
            access_ttl_seconds=1,
            refresh_ttl_seconds=2,
        )


def test_revoke_boundary_invalidates_access_and_refresh(tmp_path: Path) -> None:
    service, _, _ = auth_service(tmp_path)
    session = service.exchange_google_credential(id_token="signed-token", nonce=NONCE)

    assert service.revoke_refresh_token(session.refresh_token.reveal()) is True
    assert service.authenticate_access_token(session.access_token.reveal()) is None
    with pytest.raises(GoogleRefreshRejectedError):
        service.refresh(session.refresh_token.reveal())


def test_secret_representations_are_redacted(tmp_path: Path) -> None:
    service, _, _ = auth_service(tmp_path)
    session = service.exchange_google_credential(id_token="signed-token", nonce=NONCE)

    representation = repr(session)
    assert "opaque-secret-token" not in representation
    assert "<redacted>" in representation
