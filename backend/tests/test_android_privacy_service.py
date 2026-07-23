from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_google_auth_service
from app.routers import android_privacy
from app.routers.android import get_android_feature_store
from app.routers.android_analytics import get_android_analytics_forwarder
from app.services import travel_video_prototype_service
from app.services.android_analytics_service import AndroidAnalyticsForwarder
from app.services.android_feature_store import AndroidFeatureStore
from app.services.android_privacy_service import AndroidPrivacyService
from app.services.feature_owner import FeatureOwner
from app.services.google_auth_service import GoogleAuthService
from app.services.google_auth_session_store import GoogleAuthSessionStore


class UnusedVerifier:
    def verify(self, _id_token: str, *, audience: str) -> dict[str, Any]:
        raise AssertionError(audience)


def test_privacy_delete_removes_auth_feature_state_and_analytics(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    monkeypatch.setattr(travel_video_prototype_service, "GENERATED_ROOT", generated_root)
    settings = Settings(
        auth_session_store_path=str(tmp_path / "auth.sqlite3"),
        android_feature_store_path=str(tmp_path / "android.sqlite3"),
        generation_job_store_path=str(tmp_path / "generation.sqlite3"),
        provider_task_receipt_store_path=str(tmp_path / "provider.sqlite3"),
        rate_limit_store_path=str(tmp_path / "rates.sqlite3"),
        storage_health_generated_assets_path=str(generated_root),
        gigagochi_stats_base_url="https://stats.example/p/gigagochi",
        gigagochi_stats_ingest_token="ingest-secret",
        gigagochi_stats_actor_secret="actor-secret-that-is-at-least-32-bytes",
        gigagochi_stats_outbox_path=str(tmp_path / "outbox.sqlite3"),
    )
    auth = GoogleAuthService(
        web_client_id=None,
        store=GoogleAuthSessionStore(settings.auth_session_store_path),
        verifier=UnusedVerifier(),
        access_ttl_seconds=900,
        refresh_ttl_seconds=30 * 24 * 60 * 60,
        clock_seconds=time.time,
    )
    identity, session = auth.exchange_guest_installation(str(uuid.uuid4()))
    owner = FeatureOwner.from_google(identity)
    feature_store = AndroidFeatureStore(settings.android_feature_store_path)
    request_key = str(uuid.uuid4())
    feature_store.begin_request(
        owner=owner,
        operation="chat",
        request_key=request_key,
        payload={"message": "private"},
    )
    feature_store.commit_response(
        owner=owner,
        operation="chat",
        request_key=request_key,
        payload={"message": "private"},
        response_json='{"reply":"private"}',
    )
    analytics = AndroidAnalyticsForwarder(settings)
    analytics.accept(
        identity.account_id,
        [
            {
                "eventId": str(uuid.uuid4()),
                "sessionId": str(uuid.uuid4()),
                "name": "app_opened",
                "occurredAtEpochMillis": int(time.time() * 1_000),
                "appVersion": "1",
                "buildNumber": 1,
                "environment": "production",
                "channel": "direct-apk",
                "properties": {},
            }
        ],
    )

    AndroidPrivacyService(
        settings,
        analytics=analytics,
        auth=auth,
        feature_store=feature_store,
    ).delete_account(
        identity.account_id,
        access_token=session.access_token.reveal(),
    )

    assert auth.authenticate_access_token(session.access_token.reveal()) is None
    assert analytics.outbox.count() == (0, 1)
    assert analytics.outbox.is_privacy_token(session.access_token.reveal())
    replay = feature_store.begin_request(
        owner=owner,
        operation="chat",
        request_key=request_key,
        payload={"message": "private"},
    )
    assert replay.created


def test_privacy_endpoint_replays_success_after_auth_rows_are_deleted(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    monkeypatch.setattr(travel_video_prototype_service, "GENERATED_ROOT", generated_root)
    settings = Settings(
        auth_session_store_path=str(tmp_path / "auth.sqlite3"),
        android_feature_store_path=str(tmp_path / "android.sqlite3"),
        generation_job_store_path=str(tmp_path / "generation.sqlite3"),
        provider_task_receipt_store_path=str(tmp_path / "provider.sqlite3"),
        rate_limit_store_path=str(tmp_path / "rates.sqlite3"),
        storage_health_generated_assets_path=str(generated_root),
        gigagochi_stats_base_url="https://stats.example/p/gigagochi",
        gigagochi_stats_ingest_token="ingest-secret",
        gigagochi_stats_actor_secret="actor-secret-that-is-at-least-32-bytes",
        gigagochi_stats_outbox_path=str(tmp_path / "outbox.sqlite3"),
    )
    auth = GoogleAuthService(
        web_client_id=None,
        store=GoogleAuthSessionStore(settings.auth_session_store_path),
        verifier=UnusedVerifier(),
        access_ttl_seconds=900,
        refresh_ttl_seconds=30 * 24 * 60 * 60,
    )
    _, session = auth.exchange_guest_installation(str(uuid.uuid4()))
    feature_store = AndroidFeatureStore(settings.android_feature_store_path)
    analytics = AndroidAnalyticsForwarder(settings)
    app = FastAPI()
    app.include_router(android_privacy.router)
    app.dependency_overrides[get_google_auth_service] = lambda: auth
    app.dependency_overrides[get_android_feature_store] = lambda: feature_store
    app.dependency_overrides[get_android_analytics_forwarder] = lambda: analytics
    monkeypatch.setattr(android_privacy, "get_settings", lambda: settings)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {session.access_token.reveal()}"}

    first = client.post("/api/android/privacy/delete", json={}, headers=headers)
    replay = client.post("/api/android/privacy/delete", json={}, headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == {"deleted": True}
