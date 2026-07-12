from app.schemas import LocalChatRequest, LocalPetMemoryContext
from app.services.pet_reply_engine.lite_generator import (
    _history_messages,
    _memory_context_block,
)
from app.services.temporal_context import format_current_time, format_temporal_reference


def test_formats_absolute_and_relative_memory_time() -> None:
    assert (
        format_temporal_reference(
            "2026-07-08T10:00:00Z",
            now_iso="2026-07-10T12:00:00Z",
            timezone="Europe/Moscow",
        )
        == "08.07.2026 13:00 (позавчера)"
    )


def test_memory_context_preserves_episode_time_and_class() -> None:
    context = LocalPetMemoryContext.model_validate(
        {
            "relevantMemories": [
                {
                    "id": "event-1",
                    "kind": "event",
                    "memoryClass": "episode",
                    "text": "Питомец нашёл ключ.",
                    "recordedAt": "2026-07-08T10:00:00Z",
                    "occurredAt": "2026-07-08T10:00:00Z",
                }
            ]
        }
    )

    block = _memory_context_block(
        context,
        now_iso="2026-07-10T12:00:00Z",
        timezone="Europe/Moscow",
    )

    assert block is not None
    assert "id=event-1; event; class=episode" in block
    assert "произошло: 08.07.2026 13:00 (позавчера)" in block


def test_recent_chat_history_keeps_message_time() -> None:
    payload = LocalChatRequest.model_validate(
        {
            "message": "И что было дальше?",
            "nowIso": "2026-07-10T12:00:00Z",
            "timezone": "Europe/Moscow",
            "pet": {
                "description": "лесной зверёк",
                "stage": "baby",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 80, "energy": 80},
            },
            "history": [
                {
                    "role": "pet",
                    "text": "Я нашёл ключ.",
                    "createdAt": "2026-07-09T10:00:00Z",
                }
            ],
        }
    )

    assert _history_messages(payload) == [
        {"role": "assistant", "content": "[09.07.2026 13:00 (вчера)] Я нашёл ключ."}
    ]
    assert (
        format_current_time(
            payload.nowIso,
            timezone=payload.timezone,
        )
        == "Текущее локальное время: 10.07.2026 15:00 Europe/Moscow."
    )
