from __future__ import annotations

from types import SimpleNamespace

import httpx

from app import bot
from app.services import telegram_client, telegram_push_service
from app.services.story_delivery_format import TELEGRAM_PHOTO_CAPTION_LIMIT, format_story_caption

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


def test_story_command_sends_generated_image_as_photo(monkeypatch) -> None:
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
            "storyImage": {"bytes": b"png", "mimeType": "image/png"},
        },
    )

    def fake_send_photo(client, chat_id, photo, caption, reply_markup):
        sent["method"] = "photo"
        sent["chat_id"] = chat_id
        sent["photo"] = photo
        sent["caption"] = caption
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(bot, "send_photo", fake_send_photo)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: sent.setdefault("text", True))

    bot.handle_update(httpx.Client(), _story_update())

    assert sent["method"] == "photo"
    assert sent["chat_id"] == TEST_TELEGRAM_ID
    assert sent["photo"] == b"png"
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


def test_story_command_falls_back_to_message_without_image(monkeypatch) -> None:
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
            "storyImage": None,
        },
    )
    monkeypatch.setattr(bot, "send_photo", lambda *args, **kwargs: sent.setdefault("photo", True))

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
    assert "photo" not in sent


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


def test_story_caption_shows_recovery_as_plus() -> None:
    caption = format_story_caption(
        {
            "title": "Теплый привал",
            "storyText": "Питомец отдохнул и восстановил силы.",
            "statsDelta": {"energy": 18, "hunger": 0, "happiness": 7},
        }
    )

    assert caption.endswith("Влияние на параметры:\nздоровье: плюс 18\nнастроение: плюс 7")
