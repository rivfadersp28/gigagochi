from types import SimpleNamespace

from app.services import telegram_push_service


def test_safe_push_error_redacts_bot_token_and_bounds_persisted_text(monkeypatch) -> None:
    token = "123456:super-secret-token"
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(bot_token=token),
    )
    error = RuntimeError(
        f"GET https://api.telegram.org/bot{token}/sendMessage\n"
        + "x" * (telegram_push_service.MAX_PERSISTED_ERROR_CHARS + 100)
    )

    message = telegram_push_service._safe_error_message(error)

    assert token not in message
    assert "<redacted>" in message
    assert "\n" not in message
    assert len(message) == telegram_push_service.MAX_PERSISTED_ERROR_CHARS


def test_bulk_push_error_uses_redacted_message(monkeypatch) -> None:
    token = "123456:super-secret-token"
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(bot_token=token),
    )

    payload = telegram_push_service._bulk_error(
        {"telegramId": 42, "petId": "pet-1"},
        RuntimeError(f"request URL contains /bot{token}/sendMessage"),
    )

    assert token not in payload["message"]
    assert payload["message"] == "request URL contains /bot<redacted>/sendMessage"
