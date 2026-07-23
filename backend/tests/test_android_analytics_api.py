from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_google_account_identity
from app.middleware import RequestBodyLimitMiddleware
from app.routers import android_analytics
from app.services.android_analytics_service import AndroidAnalyticsForwarder
from app.services.google_auth_session_store import GoogleUserIdentity


def _event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "eventId": str(uuid.uuid4()),
        "sessionId": str(uuid.uuid4()),
        "name": "app_opened",
        "occurredAtEpochMillis": int(time.time() * 1_000),
        "appVersion": "1.0.0",
        "buildNumber": 1,
        "environment": "production",
        "channel": "direct-apk",
        "properties": {"source": "launcher"},
    }
    value.update(overrides)
    return value


class RecordingForwarder:
    def __init__(self) -> None:
        self.accepted: list[tuple[str, list[dict[str, object]]]] = []

    def actor_id(self, account_id: str) -> str:
        return "a" * 64

    def accept(self, account_id: str, events: list[dict[str, object]]) -> None:
        self.accepted.append((account_id, events))


def _client(tmp_path: Path, monkeypatch: Any) -> tuple[TestClient, RecordingForwarder]:
    app = FastAPI()
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=1_048_576,
        path_max_body_bytes={"/api/android/analytics/events": 128 * 1_024},
    )
    app.include_router(android_analytics.router)
    forwarder = RecordingForwarder()
    identity = GoogleUserIdentity(1, "acct-test", "guest:test", None, None)
    app.dependency_overrides[get_google_account_identity] = lambda: identity
    app.dependency_overrides[android_analytics.get_android_analytics_forwarder] = (
        lambda: forwarder
    )
    monkeypatch.setattr(
        android_analytics,
        "get_settings",
        lambda: Settings(
            rate_limit_store_path=str(tmp_path / "rates.sqlite3"),
            android_analytics_batches_per_minute=100,
            android_analytics_burst_per_10_seconds=100,
        ),
    )
    return TestClient(app), forwarder


def test_android_analytics_accepts_only_strict_bounded_events(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, forwarder = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/android/analytics/events",
        json={"schemaVersion": 1, "events": [_event()]},
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 1}
    assert response.headers["cache-control"] == "no-store"
    assert len(forwarder.accepted) == 1
    assert forwarder.accepted[0][0] == "acct-test"

    invalid = _event(properties={"prompt": "private"})
    assert client.post(
        "/api/android/analytics/events",
        json={"schemaVersion": 1, "events": [invalid]},
    ).status_code == 422
    assert client.post(
        "/api/android/analytics/events",
        json={"schemaVersion": 1, "events": [_event(extra="forbidden")]},
    ).status_code == 422
    assert client.post(
        "/api/android/analytics/events",
        json={
            "schemaVersion": 1,
            "events": [
                _event(
                    occurredAtEpochMillis=int(
                        (time.time() - 9 * 24 * 60 * 60) * 1_000
                    )
                )
            ],
        },
    ).status_code == 422


def test_forwarder_deduplicates_and_prioritizes_deletion(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        gigagochi_stats_base_url="https://stats.example/p/gigagochi",
        gigagochi_stats_ingest_token="ingest-secret",
        gigagochi_stats_actor_secret="actor-secret-that-is-at-least-32-bytes",
        gigagochi_stats_outbox_path=str(tmp_path / "outbox.sqlite3"),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    forwarder = AndroidAnalyticsForwarder(settings, client=client)
    event = _event()
    forwarder.accept("acct-test", [event])
    forwarder.accept("acct-test", [event])
    assert forwarder.outbox.count() == (1, 0)

    actor_id = forwarder.request_deletion("acct-test")
    assert forwarder.outbox.count() == (0, 1)
    assert forwarder.flush_once() is True
    assert forwarder.outbox.count() == (0, 0)
    assert requests[0].url.path.endswith("/p/gigagochi/delete")
    assert requests[0].headers["x-ingest-token"] == "ingest-secret"
    assert json.loads(requests[0].content) == {"actorId": actor_id}
    forwarder.accept("acct-test", [_event()])
    assert forwarder.outbox.count() == (0, 0)
