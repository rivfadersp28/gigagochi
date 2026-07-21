from __future__ import annotations

import httpx

from app.services import ai_error_service, image_service, openrouter_billing_alert_service


def test_http_402_sends_safe_deduplicated_ops_alert(monkeypatch) -> None:
    alerts: list[tuple[str, str, dict[str, object]]] = []
    settings = type(
        "Settings",
        (),
        {
            "openrouter_billing_alerts_enabled": True,
            "openrouter_billing_alert_telegram_ids": {62943754},
            "openrouter_billing_alert_dedup_seconds": 3600,
        },
    )()
    monkeypatch.setattr(openrouter_billing_alert_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        openrouter_billing_alert_service,
        "notify_telegram_alert",
        lambda key, text, **kwargs: alerts.append((key, text, kwargs)),
    )

    notified = openrouter_billing_alert_service.notify_openrouter_credits_exhausted(
        status_code=402,
        provider_message={"error": {"message": "raw provider detail"}},
        source="video",
    )

    assert notified is True
    assert alerts[0][0] == "openrouter:credits-exhausted"
    assert "Сергей (@rivfader)" in alerts[0][1]
    assert "https://openrouter.ai/settings/credits" in alerts[0][1]
    assert "raw provider detail" not in alerts[0][1]
    assert alerts[0][2] == {
        "enabled": True,
        "telegram_ids": {62943754},
        "dedup_seconds": 3600,
    }


def test_credit_message_without_status_is_detected_but_rate_limit_is_not() -> None:
    assert openrouter_billing_alert_service.openrouter_credits_exhausted(
        status_code=None,
        provider_message="Insufficient credits. Add more credits to continue.",
    )
    assert not openrouter_billing_alert_service.openrouter_credits_exhausted(
        status_code=429,
        provider_message="Rate limit exceeded",
    )


def test_video_http_error_routes_through_billing_alert(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        image_service,
        "notify_openrouter_credits_exhausted",
        lambda **kwargs: calls.append(kwargs),
    )
    response = httpx.Response(
        402,
        request=httpx.Request("POST", "https://openrouter.test/videos"),
        json={"error": {"message": "Insufficient credits"}},
    )

    error = image_service._openrouter_video_error(response)

    assert error.status_code == 402
    assert calls == [
        {
            "status_code": 402,
            "provider_message": {"error": {"message": "Insufficient credits"}},
            "source": "video",
        }
    ]


def test_text_provider_402_routes_through_billing_alert(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []
    settings = type(
        "Settings",
        (),
        {
            "ai_provider": "openrouter",
            "ai_log_max_bytes": 10_000,
            "ai_log_backup_count": 1,
        },
    )()
    monkeypatch.setattr(ai_error_service, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_error_service, "AI_FAILURE_LOG_PATH", tmp_path / "failures.jsonl")
    monkeypatch.setattr(ai_error_service, "notify_ops", lambda *_args: None)
    monkeypatch.setattr(
        ai_error_service,
        "notify_openrouter_credits_exhausted",
        lambda **kwargs: calls.append(kwargs),
    )

    ai_error_service.log_ai_request_failure(
        "/api/chat",
        {
            "code": "LLM_STATUS_402",
            "providerStatus": 402,
            "providerMessage": "Insufficient credits",
        },
        RuntimeError("provider failed"),
    )

    assert calls == [
        {
            "status_code": 402,
            "provider_message": "Insufficient credits",
            "source": "/api/chat",
        }
    ]
