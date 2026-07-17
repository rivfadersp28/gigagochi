from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.android_feature_store import (
    AndroidFeatureIdempotencyConflictError,
    AndroidFeatureStore,
)
from app.services.feature_owner import FeatureOwner, TelegramNotificationTarget
from app.services.google_auth_session_store import GoogleUserIdentity


def google_owner(account_id: str) -> FeatureOwner:
    return FeatureOwner.from_google(
        GoogleUserIdentity(1, account_id, "provider-subject", "hidden@example.com", "Hidden")
    )


def test_google_storage_key_and_repr_do_not_contain_canonical_account_id() -> None:
    account_id = "acct_raw_canonical_secretish_identifier"
    owner = google_owner(account_id)

    assert account_id not in str(owner.storage_key)
    assert account_id not in repr(owner)
    assert owner.storage_key.startswith("google:")

    with pytest.raises(ValueError):
        FeatureOwner("google", owner.storage_key, TelegramNotificationTarget(42))
    with pytest.raises(ValueError):
        FeatureOwner("google", "google:raw-account-id")
    with pytest.raises(ValueError):
        FeatureOwner("unknown", 42)  # type: ignore[arg-type]


def test_concurrent_duplicate_has_one_executor_and_restart_stays_in_progress(tmp_path) -> None:
    path = tmp_path / "android.sqlite3"
    AndroidFeatureStore(path)
    owner = google_owner("account-a")
    payload = {"message": "Привет"}

    def begin() -> str:
        return AndroidFeatureStore(path).begin_request(
            owner=owner,
            operation="chat",
            request_key="11111111-1111-4111-8111-111111111111",
            payload=payload,
        ).state

    with ThreadPoolExecutor(max_workers=8) as executor:
        states = list(executor.map(lambda _: begin(), range(8)))

    assert states.count("created") == 1
    assert states.count("in_progress") == 7
    restarted = AndroidFeatureStore(path).begin_request(
        owner=owner,
        operation="chat",
        request_key="11111111-1111-4111-8111-111111111111",
        payload=payload,
    )
    assert restarted.state == "in_progress"
    assert restarted.response_json is None


def test_commit_requires_same_payload_and_replay_returns_exact_response(tmp_path) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    owner = google_owner("account-a")
    key = "22222222-2222-4222-8222-222222222222"
    payload = {"request": "шляпа"}
    assert store.begin_request(
        owner=owner,
        operation="outfit-simplify",
        request_key=key,
        payload=payload,
    ).created

    with pytest.raises(RuntimeError):
        store.commit_response(
            owner=owner,
            operation="outfit-simplify",
            request_key=key,
            payload={"request": "другое"},
            response_json=json.dumps({"item": "шляпа"}),
        )

    store.commit_response(
        owner=owner,
        operation="outfit-simplify",
        request_key=key,
        payload=payload,
        response_json=json.dumps({"item": "шляпа"}),
    )
    replay = store.begin_request(
        owner=owner,
        operation="outfit-simplify",
        request_key=key,
        payload=payload,
    )
    assert replay.state == "completed"
    assert json.loads(replay.response_json or "{}") == {"item": "шляпа"}

    with pytest.raises(AndroidFeatureIdempotencyConflictError):
        store.begin_request(
            owner=owner,
            operation="outfit-simplify",
            request_key=key,
            payload={"request": "плащ"},
        )
