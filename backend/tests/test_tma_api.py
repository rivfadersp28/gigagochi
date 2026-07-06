from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import get_telegram_user
from app.main import app
from app.routers.tma import generation_error_message, provider_error_details
from app.schemas import (
    GeneratePetAssetResponse,
    GenerateTravelResponse,
    LocalChatResponse,
    LocalProactiveResponse,
    MemoryConsolidationResponse,
    MemoryExtractionResponse,
)
from app.services.rate_limit_service import rate_limiter
from app.services.telegram_auth_service import TelegramUserContext


def override_user() -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=42,
        username="serge",
        first_name="Serge",
        language_code="ru",
        auth_date=datetime.now(UTC),
    )


def tma_client() -> TestClient:
    app.dependency_overrides[get_telegram_user] = override_user
    return TestClient(app)


def wait_for_generation_job(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = client.get(f"/api/generate-pet/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("generation job did not finish")


def test_generate_pet_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.dependencies.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            bot_token=None,
            telegram_init_data_max_age_seconds=86400,
        ),
    )
    app.dependency_overrides.clear()
    client = TestClient(app)

    response = client.post("/api/generate-pet", json={"description": "маленький дракон"})

    assert response.status_code == 401


def test_chat_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.dependencies.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            bot_token=None,
            telegram_init_data_max_age_seconds=86400,
        ),
    )
    app.dependency_overrides.clear()
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={
            "message": "Как ты?",
            "pet": {
                "description": "маленький космический котенок",
                "stage": "baby",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 401


def test_generate_pet_response_contains_all_stages_and_moods(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            openai_api_key=None,
            openrouter_api_key="test-openrouter-key",
        ),
    )
    captured: dict[str, object] = {}

    def fake_generate_pet_asset_set(description: str, **kwargs):
        captured["description"] = description
        captured["kwargs"] = kwargs
        return {
            "assetSetId": "asset-1",
            "generatedAt": datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            "images": {
                "baby": {
                    "idle": "/static/generated/asset-1/baby-idle.png",
                    "happy": "/static/generated/asset-1/baby-happy.png",
                    "hungry": "/static/generated/asset-1/baby-hungry.png",
                    "sad": "/static/generated/asset-1/baby-sad.png",
                },
                "teen": {
                    "idle": "/static/generated/asset-1/teen-idle.png",
                    "happy": "/static/generated/asset-1/teen-happy.png",
                    "hungry": "/static/generated/asset-1/teen-hungry.png",
                    "sad": "/static/generated/asset-1/teen-sad.png",
                },
                "adult": {
                    "idle": "/static/generated/asset-1/adult-idle.png",
                    "happy": "/static/generated/asset-1/adult-happy.png",
                    "hungry": "/static/generated/asset-1/adult-hungry.png",
                    "sad": "/static/generated/asset-1/adult-sad.png",
                },
            },
            "spriteSheetUrl": "/static/generated/asset-1/sprite-sheet.png",
            "characterBible": {
                "species": "small dragon mascot",
                "main_colors": ["green", "yellow"],
            },
        }

    monkeypatch.setattr(
        "app.routers.tma.generate_pet_asset_set",
        fake_generate_pet_asset_set,
    )
    client = tma_client()

    response = client.post("/api/generate-pet", json={"description": "маленький дракон"})

    assert response.status_code == 202
    job = response.json()
    assert job["status"] in {"queued", "running", "succeeded"}
    payload = wait_for_generation_job(client, job["jobId"])
    assert payload["status"] == "succeeded"
    result = payload["result"]
    assert result is not None
    assert result["assetSetId"] == "asset-1"
    assert result["characterBible"]["species"] == "small dragon mascot"
    assert captured["description"] == "маленький дракон"
    assert captured["kwargs"] == {}
    for stage in ("baby", "teen", "adult"):
        assert set(result["images"][stage]) == {"idle", "happy", "hungry", "sad"}

    app.dependency_overrides.clear()


def test_generate_pet_asset_response_rejects_incomplete_image_set() -> None:
    with pytest.raises(ValidationError):
        GeneratePetAssetResponse.model_validate(
            {
                "assetSetId": "asset-1",
                "generatedAt": datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
                "images": {
                    "baby": {"happy": "/static/generated/asset-1/baby-happy.png"},
                    "teen": {},
                    "adult": {},
                },
            }
        )


def test_generation_error_message_handles_openai_timeout() -> None:
    assert (
        generation_error_message("OPENAI_TIMEOUT")
        == "Генерация заняла больше времени, чем ожидалось. Попробуйте еще раз."
    )


def test_generation_error_message_handles_image_postprocess_failure() -> None:
    assert (
        generation_error_message("IMAGE_POSTPROCESS_FAILED")
        == "Картинка сгенерировалась, но backend не смог подготовить ее для питомца."
    )


def test_chat_accepts_local_pet_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    captured: dict[str, object] = {}

    def fake_chat_with_local_pet(payload):
        captured["lore"] = payload.pet.characterBible["lore"]
        return LocalChatResponse(reply="Я рядом.", moodHint="happy")

    monkeypatch.setattr(
        "app.routers.tma.chat_with_local_pet",
        fake_chat_with_local_pet,
    )
    client = tma_client()

    response = client.post(
        "/api/chat",
        json={
            "message": "Как ты?",
            "pet": {
                "name": "Пушок",
                "description": "маленький космический котенок",
                "characterBible": {
                    "species": "cosmic kitten mascot",
                    "lore": {
                        "home": {
                            "favorite_spot": "мягкая звездная подушка",
                        }
                    },
                },
                "stage": "baby",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
            },
            "history": [{"role": "user", "text": "Привет"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Я рядом.",
        "moodHint": "happy",
    }
    assert captured["lore"] == {"home": {"favorite_spot": "мягкая звездная подушка"}}

    app.dependency_overrides.clear()


def test_travel_accepts_local_pet_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            openai_api_key=None,
            openrouter_api_key="test-openrouter-key",
        ),
    )
    captured: dict[str, object] = {}

    def fake_generate_travel(payload):
        captured["pet"] = payload.pet.model_dump()
        return GenerateTravelResponse(
            travelId="travel-1",
            generatedAt=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
            story={
                "title": "Лунная ярмарка",
                "summary": "Питомец нашел теплый огонек и вернулся довольным.",
                "scenes": [
                    {
                        "index": index,
                        "arc": arc,
                        "title": f"Сцена {index}",
                        "text": f"Короткая теплая сцена {index}.",
                        "visualBrief": f"Pet in scene {index}.",
                    }
                    for index, arc in [
                        (1, "beginning"),
                        (2, "exploration"),
                        (3, "discovery"),
                        (4, "discovery"),
                        (5, "final"),
                    ]
                ],
            },
            images=[
                {
                    "sceneIndex": 1,
                    "imageUrl": "/static/generated/travel-1/travel-scene-01.png",
                }
            ],
        )

    monkeypatch.setattr("app.routers.tma.generate_travel", fake_generate_travel)
    client = tma_client()

    response = client.post(
        "/api/travel",
        json={
            "pet": {
                "name": "Листик",
                "description": "маленький листолицый питомец",
                "characterBible": {
                    "identity": {"name": "Листик"},
                    "main_colors": ["green", "cream"],
                },
                "assetImages": {
                    "baby": {
                        "happy": "https://cdn.example.test/assets/baby-happy.png",
                        "idle": "https://cdn.example.test/assets/baby-idle.png",
                    }
                },
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 90,
                    "energy": 75,
                    "cleanliness": 85,
                },
            },
            "includeDebug": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["travelId"] == "travel-1"
    assert payload["story"]["title"] == "Лунная ярмарка"
    assert payload["story"]["scenes"][0]["index"] == 1
    assert payload["images"] == [
        {
            "sceneIndex": 1,
            "imageUrl": "/static/generated/travel-1/travel-scene-01.png",
        }
    ]
    assert captured["pet"]["characterBible"] == {
        "identity": {"name": "Листик"},
        "main_colors": ["green", "cream"],
    }
    assert captured["pet"]["assetImages"] == {
        "baby": {
            "happy": "https://cdn.example.test/assets/baby-happy.png",
            "idle": "https://cdn.example.test/assets/baby-idle.png",
        }
    }

    app.dependency_overrides.clear()


def test_chat_wraps_unhandled_error_with_cors(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    caplog.set_level(logging.ERROR, logger="app.routers.tma")

    def fail_chat(_payload):
        raise RuntimeError("openai rejected chat request")

    monkeypatch.setattr("app.routers.tma.chat_with_local_pet", fail_chat)
    client = tma_client()

    response = client.post(
        "/api/chat",
        headers={"Origin": "http://localhost:3000"},
        json={
            "message": "расскажи о своем мире",
            "pet": {
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 502
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.json()["detail"] == {
        "code": "CHAT_FAILED",
        "error": "chat_failed",
        "message": "Не удалось получить ответ питомца. Попробуйте еще раз.",
    }
    assert any(
        "AI request failed" in record.message
        and '"endpoint": "/api/chat"' in record.message
        and '"code": "CHAT_FAILED"' in record.message
        for record in caplog.records
    )

    app.dependency_overrides.clear()


def test_provider_error_details_extracts_provider_payload() -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/images")
    response = httpx.Response(
        400,
        json={"error": {"message": "unsupported image size"}},
        headers={"x-request-id": "req-test"},
        request=request,
    )
    exc = httpx.HTTPStatusError("bad request", request=request, response=response)

    assert provider_error_details(exc) == {
        "providerStatus": 400,
        "providerMessage": "unsupported image size",
        "requestId": "req-test",
    }


def test_provider_error_details_extracts_wrapped_provider_payload() -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        400,
        json={"error": {"message": "unsupported tool_choice"}},
        headers={"x-request-id": "req-chat"},
        request=request,
    )
    provider_exc = httpx.HTTPStatusError("bad request", request=request, response=response)

    try:
        raise RuntimeError("chat failed") from provider_exc
    except RuntimeError as exc:
        assert provider_error_details(exc) == {
            "providerStatus": 400,
            "providerMessage": "unsupported tool_choice",
            "requestId": "req-chat",
        }


def test_generate_pet_job_records_provider_failure(monkeypatch, caplog, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            openai_api_key=None,
            openrouter_api_key="test-openrouter-key",
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.AI_FAILURE_LOG_PATH",
        tmp_path / "ai-failures.jsonl",
    )
    caplog.set_level(logging.ERROR, logger="app.routers.tma")

    def fail_generate(_description: str):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/images")
        response = httpx.Response(
            400,
            json={"error": {"message": "unsupported image model"}},
            headers={"x-request-id": "req-job"},
            request=request,
        )
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    monkeypatch.setattr("app.routers.tma.generate_pet_asset_set", fail_generate)
    client = tma_client()

    response = client.post("/api/generate-pet", json={"description": "дракон"})

    assert response.status_code == 202
    job = wait_for_generation_job(client, response.json()["jobId"])
    assert job["status"] == "failed"
    assert job["error"] == {
        "code": "OPENAI_BAD_REQUEST",
        "error": "generation_failed",
        "message": "Не удалось создать питомца. Попробуйте еще раз.",
        "providerStatus": 400,
        "providerMessage": "unsupported image model",
        "requestId": "req-job",
    }
    assert any(
        "AI request failed" in record.message
        and '"endpoint": "/api/generate-pet"' in record.message
        and '"requestId": "req-job"' in record.message
        for record in caplog.records
    )
    failure_log = (tmp_path / "ai-failures.jsonl").read_text()
    assert '"requestId": "req-job"' in failure_log
    assert f'"jobId": "{response.json()["jobId"]}"' in failure_log

    app.dependency_overrides.clear()


def test_extract_lite_facts_returns_overlay_patch(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fake_extract(_payload):
        return (
            {
                "facts": [
                    {
                        "sphere": "world",
                        "kind": "world_fact",
                        "text": "Мир Громма состоит из базальтовых гор.",
                        "pathHint": "lite_overlay.spheres.world",
                        "source": "lite_post_reply_extractor",
                        "createdAt": "2026-07-05T00:00:00Z",
                    }
                ],
                "spheres": {
                    "world": {
                        "facts": [
                            {
                                "sphere": "world",
                                "kind": "world_fact",
                                "text": "Мир Громма состоит из базальтовых гор.",
                                "pathHint": "lite_overlay.spheres.world",
                                "source": "lite_post_reply_extractor",
                                "createdAt": "2026-07-05T00:00:00Z",
                            }
                        ]
                    }
                },
            },
            None,
        )

    monkeypatch.setattr("app.routers.tma.extract_lite_overlay_patch_from_reply", fake_extract)
    client = tma_client()

    response = client.post(
        "/api/chat/lite-facts",
        json={
            "message": "расскажи о своем мире",
            "reply": "Мой мир состоит из базальтовых гор.",
            "pet": {
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["liteOverlayPatch"]["facts"][0]["sphere"] == "world"
    assert payload["liteOverlayPatch"]["spheres"]["world"]["facts"][0]["kind"] == "world_fact"

    app.dependency_overrides.clear()


def test_memory_extract_endpoint_returns_operations(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fake_extract(_payload):
        return MemoryExtractionResponse(
            operations=[
                {
                    "type": "remember_user_fact",
                    "kind": "deadline",
                    "text": "У пользователя завтра экзамен.",
                    "normalizedKey": "exam",
                    "confidence": 0.9,
                    "importance": 0.9,
                    "dueAt": "2026-07-07T09:00:00+03:00",
                }
            ]
        )

    monkeypatch.setattr("app.routers.tma.extract_user_memory_operations", fake_extract)
    client = tma_client()

    response = client.post(
        "/api/chat/memory-extract",
        json={
            "message": "У меня завтра экзамен",
            "reply": "Я буду рядом.",
            "pet": {
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operations"][0]["type"] == "remember_user_fact"
    assert payload["operations"][0]["kind"] == "deadline"

    app.dependency_overrides.clear()


def test_memory_consolidate_endpoint_returns_operations(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fake_consolidate(_payload):
        return MemoryConsolidationResponse(
            operations=[
                {
                    "type": "rewrite_summary",
                    "content": "Пользователь готовится к экзамену.",
                }
            ]
        )

    monkeypatch.setattr("app.routers.tma.consolidate_user_memory", fake_consolidate)
    client = tma_client()

    response = client.post(
        "/api/chat/memory-consolidate",
        json={
            "pendingLearnings": [],
            "existingMemories": [],
            "summary": "",
            "userProfile": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["operations"][0]["type"] == "rewrite_summary"

    app.dependency_overrides.clear()


def test_proactive_endpoint_returns_reply(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fake_proactive(_payload):
        return LocalProactiveResponse(reply="Ну что, как экзамен?", faceHint="curious")

    monkeypatch.setattr("app.routers.tma.generate_proactive_pet_message", fake_proactive)
    client = tma_client()

    response = client.post(
        "/api/chat/proactive",
        json={
            "pet": {
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
            },
            "memoryContext": {
                "relevantMemories": [
                    {
                        "id": "m1",
                        "kind": "deadline",
                        "text": "У пользователя сегодня экзамен.",
                    }
                ],
                "proactiveCandidate": {
                    "memoryIds": ["m1"],
                    "reason": "у пользователя сегодня экзамен",
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Ну что, как экзамен?", "faceHint": "curious"}

    app.dependency_overrides.clear()


def test_generation_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            generation_rate_limit_per_day=3,
            openai_api_key=None,
            openrouter_api_key="test-openrouter-key",
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_asset_set",
        lambda description, **kwargs: {
            "assetSetId": "asset-1",
            "generatedAt": datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            "images": {
                "baby": {
                    "idle": "/static/generated/asset-1/baby-idle.png",
                    "happy": "/static/generated/asset-1/baby-happy.png",
                    "hungry": "/static/generated/asset-1/baby-hungry.png",
                    "sad": "/static/generated/asset-1/baby-sad.png",
                },
                "teen": {
                    "idle": "/static/generated/asset-1/teen-idle.png",
                    "happy": "/static/generated/asset-1/teen-happy.png",
                    "hungry": "/static/generated/asset-1/teen-hungry.png",
                    "sad": "/static/generated/asset-1/teen-sad.png",
                },
                "adult": {
                    "idle": "/static/generated/asset-1/adult-idle.png",
                    "happy": "/static/generated/asset-1/adult-happy.png",
                    "hungry": "/static/generated/asset-1/adult-hungry.png",
                    "sad": "/static/generated/asset-1/adult-sad.png",
                },
            },
            "spriteSheetUrl": "/static/generated/asset-1/sprite-sheet.png",
        },
    )
    with rate_limiter._lock:
        rate_limiter._events.clear()
    client = tma_client()

    for _ in range(3):
        assert client.post("/api/generate-pet", json={"description": "дракон"}).status_code == 202

    response = client.post("/api/generate-pet", json={"description": "дракон"})

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    assert response.json()["detail"]["retryAfterSeconds"] > 0

    app.dependency_overrides.clear()


def test_chat_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            chat_rate_limit_per_hour=30,
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.chat_with_local_pet",
        lambda payload: LocalChatResponse(reply="Я рядом.", moodHint="happy"),
    )
    with rate_limiter._lock:
        rate_limiter._events.clear()
    client = tma_client()
    payload = {
        "message": "Как ты?",
        "pet": {
            "description": "маленький космический котенок",
            "stage": "baby",
            "mood": "idle",
            "stats": {
                "hunger": 80,
                "happiness": 70,
                "energy": 60,
                "cleanliness": 90,
            },
        },
        "history": [],
    }

    for _ in range(30):
        assert client.post("/api/chat", json=payload).status_code == 200

    response = client.post("/api/chat", json=payload)

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"

    app.dependency_overrides.clear()
