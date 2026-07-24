from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.llm import LLMResponse, LLMUsage
from app.services import prompt_debug


@pytest.fixture(autouse=True)
def isolated_prompt_log_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prompt_debug,
        "get_settings",
        lambda: SimpleNamespace(
            ai_log_max_bytes=10 * 1024 * 1024,
            ai_log_backup_count=3,
            allow_dev_tma_auth=True,
        ),
    )


def test_prompt_debug_writes_prompt_log_with_generation_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_PROMPT_LOG_FULL", "true")
    log_path = tmp_path / "ai-prompts.jsonl"
    monkeypatch.setattr(prompt_debug, "AI_PROMPT_LOG_PATH", log_path)

    token = prompt_debug.set_prompt_log_context(
        {
            "jobId": "job-1",
            "endpoint": "/api/generate-pet",
        }
    )
    try:
        prompt_debug.log_image_generation_prompt(
            "pet_creation/image",
            {
                "model": "gpt-image-2",
                "prompt": "Create one standalone electric dragon sprite.",
                "size": "1024x1024",
                "quality": "medium",
                "n": 1,
                "output_format": "png",
            },
        )
        context = prompt_debug.current_ai_log_context()
    finally:
        prompt_debug.reset_prompt_log_context(token)

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["event"] == "ai_prompt"
    assert payload["promptType"] == "image_generation"
    assert payload["jobId"] == "job-1"
    assert payload["endpoint"] == "/api/generate-pet"
    assert payload["label"] == "pet_creation/image"
    assert payload["prompt"] == "Create one standalone electric dragon sprite."
    assert context["jobId"] == "job-1"
    assert context["lastPrompt"] == {
        "timestamp": payload["timestamp"],
        "promptType": "image_generation",
        "label": "pet_creation/image",
        "model": "gpt-image-2",
    }


def test_prompt_debug_writes_response_log_with_generation_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_PROMPT_LOG_FULL", "true")
    prompt_log_path = tmp_path / "ai-prompts.jsonl"
    response_log_path = tmp_path / "ai-responses.jsonl"
    monkeypatch.setattr(prompt_debug, "AI_PROMPT_LOG_PATH", prompt_log_path)
    monkeypatch.setattr(prompt_debug, "AI_RESPONSE_LOG_PATH", response_log_path)

    token = prompt_debug.set_prompt_log_context(
        {
            "jobId": "job-2",
            "endpoint": "/api/generate-pet",
        }
    )
    try:
        prompt_debug.log_chat_completion_prompt(
            "pet_creation/character_bible",
            {
                "model": "openai/gpt-5.5",
                "messages": [{"role": "user", "content": "электрический дракон"}],
            },
        )
        prompt_debug.log_chat_completion_response(
            "pet_creation/character_bible",
            {
                "id": "gen-text-1",
                "model": "openai/gpt-5.5",
                "choices": [{"finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        prompt_debug.log_image_generation_response(
            "pet_creation/image",
            {"model": "openai/gpt-image-2"},
            {
                "id": "gen-img-1",
                "created": 1783331818,
                "usage": {"prompt_tokens": 100, "completion_tokens": 200, "cost": 0.05},
                "data": [{"b64_json": "..."}],
            },
            headers={"x-request-id": "req-image-1", "authorization": "must-not-log"},
        )
    finally:
        prompt_debug.reset_prompt_log_context(token)

    responses = [
        json.loads(line) for line in response_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert responses[0]["event"] == "ai_response"
    assert responses[0]["promptType"] == "chat_completion"
    assert responses[0]["jobId"] == "job-2"
    assert responses[0]["lastPrompt"]["label"] == "pet_creation/character_bible"
    assert responses[0]["providerGenerationId"] == "gen-text-1"
    assert responses[0]["finishReason"] == "stop"
    assert responses[0]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert responses[1]["promptType"] == "image_generation"
    assert responses[1]["providerGenerationId"] == "gen-img-1"
    assert responses[1]["headers"] == {"x-request-id": "req-image-1"}


def test_prompt_debug_logs_neutral_response_without_raw_payload(monkeypatch, tmp_path) -> None:
    response_log_path = tmp_path / "ai-responses.jsonl"
    monkeypatch.setattr(prompt_debug, "AI_RESPONSE_LOG_PATH", response_log_path)

    prompt_debug.log_chat_completion_response(
        "custom/provider",
        LLMResponse(
            content="готово",
            model="custom-model",
            finish_reason="stop",
            usage=LLMUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        ),
    )

    payload = json.loads(response_log_path.read_text(encoding="utf-8"))
    assert payload["model"] == "custom-model"
    assert payload["finishReason"] == "stop"
    assert payload["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


def test_pet_reply_log_redacts_final_reply_and_keeps_context(monkeypatch, tmp_path) -> None:
    reply_log_path = tmp_path / "pet-replies.jsonl"
    monkeypatch.delenv("AI_PROMPT_LOG_FULL", raising=False)
    monkeypatch.setattr(prompt_debug, "PET_REPLY_LOG_PATH", reply_log_path)
    token = prompt_debug.set_prompt_log_context(
        {"requestKey": "chat-17", "endpoint": "/api/android/chat"}
    )
    try:
        prompt_debug.log_pet_reply(
            "chat",
            "Это длинная, но полностью законченная реплика без обрезки.",
        )
    finally:
        prompt_debug.reset_prompt_log_context(token)

    payload = json.loads(reply_log_path.read_text(encoding="utf-8"))
    assert payload["event"] == "pet_reply"
    assert payload["surface"] == "chat"
    assert payload["requestKey"] == "chat-17"
    assert "reply" not in payload
    assert payload["replyChars"] == len(
        "Это длинная, но полностью законченная реплика без обрезки."
    )
    assert len(payload["replyHash"]) == 64
    assert "полностью" not in reply_log_path.read_text(encoding="utf-8")


def test_prompt_debug_redacts_prompt_content_by_default(monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "ai-prompts.jsonl"
    monkeypatch.delenv("AI_PROMPT_LOG_FULL", raising=False)
    monkeypatch.setattr(prompt_debug, "AI_PROMPT_LOG_PATH", log_path)

    snapshot = prompt_debug.log_chat_completion_prompt(
        "pet_reply/lite",
        {
            "model": "gpt-5.5",
            "messages": [{"role": "user", "content": "Меня зовут Секрет"}],
        },
    )

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert snapshot["messages"][0]["content"] == "Меня зовут Секрет"
    assert "messages" not in payload
    assert "Секрет" not in log_path.read_text(encoding="utf-8")
    assert payload["promptContentChars"] == len("Меня зовут Секрет")


def test_full_prompt_logging_stays_redacted_outside_dev_mode(monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "ai-prompts.jsonl"
    monkeypatch.setenv("AI_PROMPT_LOG_FULL", "true")
    monkeypatch.setattr(prompt_debug, "AI_PROMPT_LOG_PATH", log_path)
    monkeypatch.setattr(
        prompt_debug,
        "get_settings",
        lambda: SimpleNamespace(
            ai_log_max_bytes=10 * 1024 * 1024,
            ai_log_backup_count=3,
            allow_dev_tma_auth=False,
        ),
    )

    prompt_debug.log_chat_completion_prompt(
        "pet_reply/lite",
        {
            "model": "gpt-5.5",
            "messages": [{"role": "user", "content": "production secret"}],
        },
    )

    raw_log = log_path.read_text(encoding="utf-8")
    assert "production secret" not in raw_log
    assert "messages" not in json.loads(raw_log)


def test_video_prompt_debug_redacts_prompt_and_references_by_default(
    monkeypatch,
    tmp_path,
) -> None:
    log_path = tmp_path / "ai-prompts.jsonl"
    monkeypatch.delenv("AI_PROMPT_LOG_FULL", raising=False)
    monkeypatch.setattr(prompt_debug, "AI_PROMPT_LOG_PATH", log_path)

    prompt_debug.log_video_generation_prompt(
        "travel/video",
        {
            "model": "video-model",
            "prompt": "Секретный сюжет пользователя",
            "duration": 8,
            "input_references": [{"url": "https://private.example/user-image.png"}],
        },
    )

    raw_log = log_path.read_text(encoding="utf-8")
    payload = json.loads(raw_log)
    assert payload["promptType"] == "video_generation"
    assert payload["promptChars"] == len("Секретный сюжет пользователя")
    assert payload["inputReferenceCount"] == 1
    assert "Секретный" not in raw_log
    assert "private.example" not in raw_log
