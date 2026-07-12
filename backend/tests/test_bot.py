from __future__ import annotations

from types import SimpleNamespace

import httpx

from app import bot
from app.services import telegram_client, telegram_push_service
from app.services.story_delivery_format import (
    TELEGRAM_PHOTO_CAPTION_LIMIT,
    format_full_story_message,
    format_story_caption,
)

TEST_TELEGRAM_ID = 62943754
STORY_IMPACT_TEXT = "Влияние на параметры:\nздоровье: минус 25"


def _story_update() -> dict:
    return {
        "message": {
            "chat": {"id": TEST_TELEGRAM_ID},
            "from": {"first_name": "Serge"},
            "text": "/story",
        }
    }


def _push_update() -> dict:
    return {
        "message": {
            "chat": {"id": TEST_TELEGRAM_ID},
            "from": {"first_name": "Serge"},
            "text": "/push",
        }
    }


def _full_story_update() -> dict:
    return {
        "message": {
            "chat": {"id": TEST_TELEGRAM_ID},
            "from": {"first_name": "Serge"},
            "text": "/full_story",
        }
    }


def test_push_command_generates_for_requesting_user(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_manual_push",
        lambda **kwargs: calls.append(kwargs) or {"sent": True},
    )

    bot.handle_update(httpx.Client(), _push_update())

    assert calls == [{"telegram_id": TEST_TELEGRAM_ID, "include_debug": False}]


def test_push_command_can_be_submitted_without_blocking_polling(monkeypatch) -> None:
    submitted: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    bot.handle_update(
        httpx.Client(),
        _push_update(),
        submit_push=lambda chat_id, keyboard: submitted.append((chat_id, keyboard)),
    )

    assert submitted[0][0] == TEST_TELEGRAM_ID
    assert submitted[0][1]["inline_keyboard"][0][0]["web_app"]["url"] == ("https://example.com/app")


def test_story_command_sends_generated_video(monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_story_for_telegram_user",
        lambda **kwargs: {
            "story": {
                "title": "След под кроной",
                "storyText": "Олег нашел теплый знак под древним дубом.",
                "statImpact": {
                    "applies": True,
                    "isNegativeOutcome": True,
                    "stat": "energy",
                    "amount": 25,
                    "reason": "Олег поцарапал лапу.",
                },
                "statsDelta": {"energy": -25, "hunger": 0, "happiness": 0},
            },
            "storyVideo": {"bytes": b"mp4", "mimeType": "video/mp4"},
        },
    )

    def fake_send_video(client, chat_id, video, caption, reply_markup):
        sent["method"] = "video"
        sent["chat_id"] = chat_id
        sent["video"] = video
        sent["caption"] = caption
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(bot, "send_video", fake_send_video)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: sent.setdefault("text", True))

    bot.handle_update(httpx.Client(), _story_update())

    assert sent["method"] == "video"
    assert sent["chat_id"] == TEST_TELEGRAM_ID
    assert sent["video"] == b"mp4"
    assert sent["caption"] == (
        f"След под кроной\n\nОлег нашел теплый знак под древним дубом.\n\n{STORY_IMPACT_TEXT}"
    )
    assert "text" not in sent


def test_send_photo_uses_detected_jpeg_mime(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        telegram_client,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    class FakeClient:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return SimpleNamespace(is_success=True)

    telegram_client.send_photo(
        FakeClient(),
        123,
        b"\xff\xd8\xff\xe0jpeg-bytes",
        "caption",
        {"inline_keyboard": []},
    )

    assert captured["files"]["photo"] == (
        "story.jpg",
        b"\xff\xd8\xff\xe0jpeg-bytes",
        "image/jpeg",
    )


def test_send_video_uses_mp4_and_streaming(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        telegram_client,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    class FakeClient:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return SimpleNamespace(is_success=True)

    telegram_client.send_video(
        FakeClient(),
        123,
        b"mp4-bytes",
        "caption",
        {"inline_keyboard": []},
    )

    assert captured["url"].endswith("/sendVideo")
    assert captured["files"]["video"] == (
        "story.mp4",
        b"mp4-bytes",
        "video/mp4",
    )
    assert captured["data"]["supports_streaming"] == "true"


def test_story_command_can_be_submitted_without_blocking_polling(monkeypatch) -> None:
    submitted: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda chat_id, keyboard: submitted.append((chat_id, keyboard)),
    )

    assert submitted[0][0] == TEST_TELEGRAM_ID
    assert submitted[0][1]["inline_keyboard"][0][0]["web_app"]["url"] == ("https://example.com/app")


def test_full_story_command_can_be_submitted_without_blocking_polling(monkeypatch) -> None:
    submitted: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    bot.handle_update(
        httpx.Client(),
        _full_story_update(),
        submit_full_story=lambda chat_id, keyboard: submitted.append((chat_id, keyboard)),
    )

    assert submitted[0][0] == TEST_TELEGRAM_ID


def test_full_story_command_generates_for_requesting_user(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_full_story_for_telegram_user",
        lambda client, **kwargs: calls.append(kwargs) or {"generated": True},
    )

    bot.handle_update(httpx.Client(), _full_story_update())

    assert calls[0]["telegram_id"] == TEST_TELEGRAM_ID


def test_story_command_falls_back_to_message_without_video(monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_story_for_telegram_user",
        lambda **kwargs: {
            "story": {
                "title": "След под кроной",
                "storyText": "Олег нашел теплый знак под древним дубом.",
                "statImpact": {
                    "applies": False,
                    "isNegativeOutcome": False,
                    "stat": "none",
                    "amount": 0,
                    "reason": "Последствий нет.",
                },
                "statsDelta": {"energy": 0, "hunger": 0, "happiness": 0},
            },
            "storyVideo": None,
        },
    )
    monkeypatch.setattr(bot, "send_video", lambda *args, **kwargs: sent.setdefault("video", True))

    def fake_send_message(client, chat_id, text, reply_markup):
        sent["method"] = "message"
        sent["chat_id"] = chat_id
        sent["text"] = text
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(bot, "send_message", fake_send_message)

    bot.handle_update(httpx.Client(), _story_update())

    assert sent["method"] == "message"
    assert sent["chat_id"] == TEST_TELEGRAM_ID
    assert sent["text"] == (
        "След под кроной\n\n"
        "Олег нашел теплый знак под древним дубом.\n\n"
        "Влияние на параметры:\n"
        "без изменений"
    )
    assert "video" not in sent


def test_story_caption_preserves_stat_debug_tail() -> None:
    caption = format_story_caption(
        {
            "title": "Длинная история",
            "storyText": "Очень длинный текст. " * 200,
            "statImpact": {
                "applies": True,
                "isNegativeOutcome": True,
                "stat": "hunger",
                "amount": 25,
                "reason": "Питомец потерял еду.",
            },
            "statsDelta": {"energy": 0, "hunger": -25, "happiness": 0},
        }
    )

    assert len(caption) <= TELEGRAM_PHOTO_CAPTION_LIMIT
    assert caption.endswith("Влияние на параметры:\nголод: минус 25")


def test_full_story_message_formats_all_four_parts() -> None:
    message = format_full_story_message(
        {
            "overallTitle": "Большой путь",
            "parts": [
                {
                    "title": f"Этап {index}",
                    "storyText": f"Событие {index}.",
                    "statsDelta": {"energy": -index, "hunger": 0, "happiness": index},
                }
                for index in range(1, 5)
            ],
        }
    )

    assert "Большой путь" in message
    assert "Часть 4. Этап 4" in message
    assert "здоровье: минус 4" in message
    assert "настроение: плюс 4" in message


def test_story_caption_shows_recovery_as_plus() -> None:
    caption = format_story_caption(
        {
            "title": "Теплый привал",
            "storyText": "Питомец отдохнул и восстановил силы.",
            "statsDelta": {"energy": 18, "hunger": 0, "happiness": 7},
        }
    )

    assert caption.endswith("Влияние на параметры:\nздоровье: плюс 18\nнастроение: плюс 7")
