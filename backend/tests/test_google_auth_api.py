from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_google_auth_service
from app.main import app
from app.services.google_auth_service import GoogleAuthService
from app.services.google_auth_session_store import GoogleAuthSessionStore
from tests.test_google_auth_service import (
    CLIENT_ID,
    NONCE,
    NOW_SECONDS,
    FakeVerifier,
    TokenSequence,
    valid_claims,
)

INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174000"
OTHER_INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174001"


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def configured_service(tmp_path: Path, *, client_id: str | None = CLIENT_ID) -> GoogleAuthService:
    return GoogleAuthService(
        web_client_id=client_id,
        store=GoogleAuthSessionStore(
            tmp_path / "api-auth.sqlite3",
            token_factory=TokenSequence(),
        ),
        verifier=FakeVerifier(valid_claims()),
        access_ttl_seconds=900,
        refresh_ttl_seconds=30 * 24 * 60 * 60,
        clock_seconds=lambda: NOW_SECONDS,
    )


def test_google_and_refresh_endpoints_match_android_contract(tmp_path: Path) -> None:
    service = configured_service(tmp_path)
    app.dependency_overrides[get_google_auth_service] = lambda: service
    client = TestClient(app)

    login = client.post(
        "/api/auth/google",
        json={"idToken": "signed-google-token", "nonce": NONCE},
    )

    assert login.status_code == 200
    assert set(login.json()) == {"accessToken", "refreshToken", "expiresAt"}
    assert login.json()["expiresAt"] == int(NOW_SECONDS * 1_000) + 900_000
    assert login.headers["cache-control"] == "no-store"
    identity = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
    )
    assert identity.status_code == 200
    assert set(identity.json()) == {"accountId"}
    assert identity.json()["accountId"].startswith("acct_")
    assert identity.headers["cache-control"] == "no-store"
    old_refresh = login.json()["refreshToken"]

    refresh = client.post("/api/auth/refresh", json={"refreshToken": old_refresh})
    assert refresh.status_code == 200
    assert refresh.json()["refreshToken"] != old_refresh

    replay = client.post("/api/auth/refresh", json={"refreshToken": old_refresh})
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "AUTH_INVALID"


def test_guest_endpoint_issues_session_and_returns_stable_account_id(tmp_path: Path) -> None:
    service = configured_service(tmp_path, client_id=None)
    app.dependency_overrides[get_google_auth_service] = lambda: service
    client = TestClient(app)

    first = client.post("/api/auth/guest", json={"installationId": INSTALLATION_ID})
    second = client.post("/api/auth/guest", json={"installationId": INSTALLATION_ID})
    other = client.post(
        "/api/auth/guest",
        json={"installationId": OTHER_INSTALLATION_ID},
    )

    assert first.status_code == 200
    assert set(first.json()) == {"accountId", "accessToken", "refreshToken", "expiresAt"}
    assert first.json()["accountId"].startswith("acct_")
    assert first.json()["accountId"] == second.json()["accountId"]
    assert first.json()["accountId"] != other.json()["accountId"]
    assert first.json()["accessToken"] != second.json()["accessToken"]
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["pragma"] == "no-cache"

    identity = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {first.json()['accessToken']}"},
    )
    assert identity.status_code == 200
    assert identity.json() == {"accountId": first.json()["accountId"]}
    assert service._verifier.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"installationId": None},
        {"installationId": 42},
        {"installationId": "not-a-uuid"},
        {"installationId": "123e4567-e89b-12d3-a456-426614174000"},
        {"installationId": INSTALLATION_ID.upper()},
        {"installationId": INSTALLATION_ID, "unexpected": True},
    ],
)
def test_guest_endpoint_returns_typed_bad_request_for_invalid_payload(
    tmp_path: Path,
    payload: object,
) -> None:
    app.dependency_overrides[get_google_auth_service] = lambda: configured_service(tmp_path)

    response = TestClient(app).post("/api/auth/guest", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AUTH_GUEST_INVALID"


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer", "Bearer invalid", "Bearer  invalid"],
)
def test_me_fails_closed_without_valid_bearer(
    tmp_path: Path,
    authorization: str | None,
) -> None:
    app.dependency_overrides[get_google_auth_service] = lambda: configured_service(tmp_path)
    headers = {"Authorization": authorization} if authorization is not None else {}

    response = TestClient(app).get("/api/auth/me", headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_INVALID"


def test_me_identity_is_stable_per_google_subject_and_isolated_between_subjects(
    tmp_path: Path,
) -> None:
    service = configured_service(tmp_path)
    app.dependency_overrides[get_google_auth_service] = lambda: service
    client = TestClient(app)

    first = client.post(
        "/api/auth/google",
        json={"idToken": "first", "nonce": NONCE},
    ).json()
    first_id = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {first['accessToken']}"},
    ).json()["accountId"]
    second = client.post(
        "/api/auth/google",
        json={"idToken": "second", "nonce": NONCE},
    ).json()
    second_id = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {second['accessToken']}"},
    ).json()["accountId"]
    assert second_id == first_id

    service._verifier.claims = valid_claims(sub="another-google-subject")
    other = client.post(
        "/api/auth/google",
        json={"idToken": "other", "nonce": NONCE},
    ).json()
    other_id = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {other['accessToken']}"},
    ).json()["accountId"]
    assert other_id != first_id


def test_auth_configuration_is_fail_closed(tmp_path: Path) -> None:
    app.dependency_overrides[get_google_auth_service] = lambda: configured_service(
        tmp_path,
        client_id=None,
    )

    response = TestClient(app).post(
        "/api/auth/google",
        json={"idToken": "signed-google-token", "nonce": NONCE},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"idToken": "token"},
        {"idToken": "token", "nonce": "too-short"},
        {"idToken": "token", "nonce": NONCE, "unexpected": "value"},
    ],
)
def test_google_endpoint_rejects_malformed_requests(
    tmp_path: Path,
    payload: dict[str, str],
) -> None:
    app.dependency_overrides[get_google_auth_service] = lambda: configured_service(tmp_path)

    response = TestClient(app).post("/api/auth/google", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_auth_router_is_additive_and_tma_route_remains_registered() -> None:
    paths = set(app.openapi()["paths"])

    # Android auth stays outside the frontend-owned generated OpenAPI contract.
    # The endpoint tests above prove both hidden routes are registered.
    assert "/api/auth/google" not in paths
    assert "/api/auth/guest" not in paths
    assert "/api/auth/refresh" not in paths
    assert "/api/auth/me" not in paths
    assert "/api/capabilities" in paths
    assert "/api/generate-pet" in paths
