from types import SimpleNamespace

from app.services import generation_notification_service


def test_sends_generation_ready_notification(monkeypatch) -> None:
    sent: dict[str, object] = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(generation_notification_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        generation_notification_service,
        "get_settings",
        lambda: SimpleNamespace(bot_token="token", webapp_url="https://example.test/app"),
    )
    monkeypatch.setattr(
        generation_notification_service,
        "send_message",
        lambda client, chat_id, text, reply_markup: sent.update(
            client=client,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        ),
    )

    generation_notification_service.send_generation_ready_notification(42)

    assert sent["chat_id"] == 42
    assert sent["text"] == "Ваш друг родился, скорее познакомьтесь с ним"
    assert sent["reply_markup"] == {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть питомца",
                    "web_app": {"url": "https://example.test/app"},
                }
            ]
        ]
    }


def test_skips_notification_without_telegram_config(monkeypatch) -> None:
    monkeypatch.setattr(
        generation_notification_service,
        "get_settings",
        lambda: SimpleNamespace(bot_token=None, webapp_url=None),
    )
    monkeypatch.setattr(
        generation_notification_service,
        "send_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    generation_notification_service.send_generation_ready_notification(42)


def test_sends_outfit_ready_notification(monkeypatch) -> None:
    sent: dict[str, object] = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(generation_notification_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        generation_notification_service,
        "get_settings",
        lambda: SimpleNamespace(bot_token="token", webapp_url="https://example.test/app"),
    )
    monkeypatch.setattr(
        generation_notification_service,
        "send_message",
        lambda _client, _chat_id, text, _reply_markup: sent.update(text=text),
    )

    generation_notification_service.send_outfit_ready_notification(42)

    assert sent["text"] == "Ваш персонаж переоделся. Скорее посмотрите на него в обновках!"


def test_sends_travel_ready_video(monkeypatch) -> None:
    sent: dict[str, object] = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(generation_notification_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        generation_notification_service,
        "get_settings",
        lambda: SimpleNamespace(bot_token="token", webapp_url="https://example.test/app"),
    )
    monkeypatch.setattr(
        generation_notification_service,
        "send_video",
        lambda _client, chat_id, video, caption, _reply_markup: sent.update(
            chat_id=chat_id,
            video=video,
            caption=caption,
        ),
    )

    generation_notification_service.send_travel_ready_video(42, b"travel-video")

    assert sent == {
        "chat_id": 42,
        "video": b"travel-video",
        "caption": "Я вернулся из путешествия! Смотри, как всё прошло.",
    }
