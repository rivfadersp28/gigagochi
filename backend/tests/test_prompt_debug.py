from __future__ import annotations

import json

from app.services import prompt_debug


def test_prompt_debug_writes_prompt_log_with_generation_context(monkeypatch, tmp_path) -> None:
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
        json.loads(line)
        for line in response_log_path.read_text(encoding="utf-8").splitlines()
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
