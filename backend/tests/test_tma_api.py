from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.dependencies import get_telegram_user
from app.llm import LLMProviderError
from app.main import app
from app.routers import tma as tma_router
from app.schemas import (
    GeneratePetAssetResponse,
    LocalChatResponse,
    LocalPetPushSnapshotResponse,
    LocalProactiveResponse,
    MemoryConsolidationResponse,
    MemoryExtractionResponse,
)
from app.services.ai_error_service import (
    ai_failure_http_exception,
    error_detail,
    generation_error_message,
    provider_error_details,
    public_error_detail,
)
from app.services.rate_limit_service import get_rate_limiter
from app.services.storage_health_service import StorageCapacityError
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_push_store import (
    TelegramPushRecordTooLargeError,
    TelegramPushStoreCapacityError,
)


@pytest.fixture(autouse=True)
def isolate_tma_runtime() -> None:
    """Keep FastAPI overrides and generation executors local to each test."""

    def stop_generation_service() -> None:
        service = tma_router.generation_job_service
        if service is None:
            return
        try:
            service.shutdown(wait=True)
        finally:
            tma_router.generation_job_service = None

    stop_generation_service()
    app.dependency_overrides.clear()
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        stop_generation_service()


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
    return TestClient(
        app,
        headers={"Idempotency-Key": f"test-request:{uuid.uuid4()}"},
    )


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

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": "pet-request-auth-0001"},
        json={"description": "маленький дракон"},
    )

    assert response.status_code == 401


def test_capabilities_come_from_backend_configuration(monkeypatch) -> None:
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            diagnostic_telegram_ids={42},
            interactive_travel_pilot_telegram_ids={42},
            allow_dev_tma_auth=False,
        ),
    )

    response = tma_client().get("/api/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "telegramUserId": 42,
        "debugMenu": True,
        "interactiveTravel": True,
    }


def test_paid_generation_requires_idempotency_key() -> None:
    app.dependency_overrides[get_telegram_user] = override_user
    client = TestClient(app)

    response = client.post("/api/generate-pet", json={"description": "маленький дракон"})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_generate_pet_uses_default_parallel_pipeline(monkeypatch) -> None:
    captured: dict[str, object] = {}
    now = datetime.now(UTC)

    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            openai_api_key="test-openai-key",
            kandinsky_api_key="test-kandinsky-key",
        ),
    )

    def fake_submit(description, user, request_key=None):
        captured.update(
            description=description,
            owner_id=user.telegram_id,
            request_key=request_key,
        )
        return {
            "jobId": "job-kandinsky",
            "status": "queued",
            "phase": "queued",
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr("app.routers.tma.submit_generation_job", fake_submit)
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        json={"description": "дракон", "imageProvider": "kandinsky"},
    )

    assert response.status_code == 202
    request_key = captured.pop("request_key")
    assert isinstance(request_key, str) and request_key.startswith("test-request:")
    assert captured == {
        "description": "дракон",
        "owner_id": 42,
    }
    app.dependency_overrides.clear()


@pytest.mark.parametrize("enabled", [False, True])
def test_pet_comparison_pipeline_is_explicit_opt_in(monkeypatch, enabled) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(pet_comparison_enabled=enabled),
    )

    def fake_service(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(shutdown=lambda *, wait=False: None)

    monkeypatch.setattr(tma_router, "GenerationJobService", fake_service)

    tma_router._generation_job_service()

    assert (captured["generate_comparison_images"] is not None) is enabled


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
            openai_api_key="test-openai-key",
            openrouter_api_key="test-openrouter-key",
            pet_comparison_enabled=False,
        ),
    )
    captured: dict[str, object] = {}
    notifications: list[int] = []
    monkeypatch.setattr(
        "app.routers.tma.send_generation_ready_notification",
        notifications.append,
    )

    def fake_generate_pet_image_asset_set(description: str, **kwargs):
        captured["description"] = description
        captured["kwargs"] = kwargs
        return SimpleNamespace(asset_set_id="asset-1", character_bible={"species": "dragon"})

    generated_response = {
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
        "videoUrl": "/static/generated/asset-1/teen-idle.mp4",
        "characterBible": {
            "species": "small dragon mascot",
            "main_colors": ["green", "yellow"],
        },
    }

    monkeypatch.setattr(
        "app.routers.tma.generate_pet_image_asset_set",
        fake_generate_pet_image_asset_set,
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_video_for_image_asset_set",
        lambda _image_set: SimpleNamespace(name="teen-idle.mp4"),
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_sad_scene_path",
        lambda _image_set, **_kwargs: SimpleNamespace(name="teen-sad.png"),
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_sad_video_for_image_asset_set",
        lambda _image_set, _sad_scene_path: SimpleNamespace(name="teen-sad.mp4"),
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_happy_scene_path",
        lambda _image_set, **_kwargs: SimpleNamespace(name="teen-happy.png"),
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_happy_video_for_image_asset_set",
        lambda _image_set, _happy_scene_path: SimpleNamespace(name="teen-happy.mp4"),
    )

    def fake_build_pet_asset_set_response(*args):
        _sad_video_path = args[3]
        _happy_video_path = args[5]
        return {
            **generated_response,
            "sadVideoUrl": ("/static/generated/asset-1/teen-sad.mp4" if _sad_video_path else None),
            "happyVideoUrl": (
                "/static/generated/asset-1/teen-happy.mp4" if _happy_video_path else None
            ),
        }

    monkeypatch.setattr(
        "app.routers.tma.build_pet_asset_set_response",
        fake_build_pet_asset_set_response,
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_kandinsky_pet_comparison_assets",
        lambda *_args, **_kwargs: pytest.fail("comparison must be opt-in"),
    )
    client = tma_client()

    response = client.post("/api/generate-pet", json={"description": "маленький дракон"})

    assert response.status_code == 202
    job = response.json()
    assert job["status"] in {"queued", "running", "succeeded"}
    payload = wait_for_generation_job(client, job["jobId"])
    assert payload["status"] == "succeeded"
    assert payload["phase"] == "completed"
    result = payload["result"]
    assert result is not None
    assert result["assetSetId"] == "asset-1"
    assert result["characterBible"]["species"] == "small dragon mascot"
    assert captured["description"] == "маленький дракон"
    assert captured["kwargs"] == {
        "image_provider": "openai",
        "asset_set_id": uuid.UUID(job["jobId"]),
    }
    assert notifications == [42]
    assert result["sadVideoUrl"] == "/static/generated/asset-1/teen-sad.mp4"
    assert result["happyVideoUrl"] == "/static/generated/asset-1/teen-happy.mp4"
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
        == "Создание питомца заняло больше времени, чем ожидалось. Попробуйте ещё раз."
    )


def test_generation_error_message_handles_image_postprocess_failure() -> None:
    assert (
        generation_error_message("IMAGE_POSTPROCESS_FAILED")
        == "Не получилось подготовить питомца. Попробуйте ещё раз."
    )


def test_storage_capacity_error_maps_to_retryable_503(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ai_error_service.log_ai_request_failure",
        lambda *_args, **_kwargs: None,
    )
    error = StorageCapacityError(media_kind="image", reason="LOW_DISK_SPACE")

    response_error = ai_failure_http_exception(
        "/api/travel/interactive/illustrate",
        "travel_failed",
        error.code,
        "Путешествие временно нельзя сохранить. Попробуйте позже.",
        error,
    )

    assert response_error.status_code == 503
    assert response_error.detail["code"] == "STORAGE_CAPACITY_LOW"
    assert response_error.headers == {"Retry-After": "300"}


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
                },
            },
            "history": [{"role": "user", "text": "Привет"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Я рядом.",
        "moodHint": "happy",
        "happinessDelta": 0,
    }
    assert captured["lore"] == {"home": {"favorite_spot": "мягкая звездная подушка"}}

    app.dependency_overrides.clear()


def test_chat_rejects_deep_character_bible_before_service(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    called = False

    def unexpected_chat(_payload):
        nonlocal called
        called = True
        raise AssertionError("invalid character bible reached the chat service")

    monkeypatch.setattr("app.routers.tma.chat_with_local_pet", unexpected_chat)
    nested_bible = '{"next":' * 500 + '"leaf"' + "}" * 500
    body = (
        '{"message":"x","pet":{"description":"pet","stage":"baby",'
        '"mood":"idle","stats":{"hunger":1,"happiness":1,"energy":1},'
        f'"characterBible":{nested_bible}}}'
    )
    client = tma_client()

    response = client.post(
        "/api/chat",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    assert called is False
    app.dependency_overrides.clear()


def test_debug_payload_requires_dev_mode_and_trusted_user(monkeypatch) -> None:
    class DebugPayload:
        includeDebug = True

        def model_copy(self, *, update):
            return SimpleNamespace(includeDebug=update["includeDebug"])

    payload = DebugPayload()
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=True,
            diagnostic_telegram_ids={62943754},
        ),
    )

    untrusted = override_user()
    diagnostic = SimpleNamespace(telegram_id=62943754)
    local_fallback = SimpleNamespace(telegram_id=0)

    assert tma_router._without_untrusted_debug(payload, untrusted).includeDebug is False
    assert tma_router._without_untrusted_debug(payload, diagnostic) is payload
    assert tma_router._without_untrusted_debug(payload, local_fallback) is payload


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
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 502
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.json()["detail"] == {
        "code": "CHAT_FAILED",
        "message": "Не получилось получить ответ. Отправьте сообщение ещё раз.",
    }
    assert any(
        "AI request failed" in record.message
        and '"endpoint": "/api/chat"' in record.message
        and '"code": "CHAT_FAILED"' in record.message
        for record in caplog.records
    )

    app.dependency_overrides.clear()


def test_chat_exposes_diagnostics_only_to_configured_user(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            diagnostic_telegram_ids={62943754},
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.chat_with_local_pet",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("provider exploded")),
    )
    app.dependency_overrides[get_telegram_user] = lambda: TelegramUserContext(
        telegram_id=62943754,
        username="sergey",
        first_name="Сергей",
        language_code="ru",
        auth_date=datetime.now(UTC),
    )
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={
            "message": "Как ты?",
            "pet": {
                "description": "маленький дракон",
                "stage": "baby",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 70, "energy": 60},
            },
            "history": [],
        },
    )

    assert response.status_code == 502
    assert response.json()["detail"]["message"] == (
        "Не получилось получить ответ. Отправьте сообщение ещё раз."
    )
    assert response.json()["detail"]["diagnostic"] == {
        "error": "chat_failed",
        "exceptionType": "RuntimeError",
        "exceptionMessage": "provider exploded",
    }

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


def test_provider_error_details_extracts_neutral_llm_error() -> None:
    exc = LLMProviderError(
        "GigaChat completion failed: quota exceeded",
        status_code=429,
    )

    assert provider_error_details(exc) == {
        "providerStatus": 429,
        "providerMessage": "GigaChat completion failed: quota exceeded",
    }


def test_public_error_detail_exposes_diagnostics_only_when_requested() -> None:
    detail = error_detail(
        "chat_failed",
        "CHAT_FAILED",
        "Не получилось получить ответ. Отправьте сообщение ещё раз.",
        RuntimeError("provider exploded"),
    )

    assert public_error_detail(detail) == {
        "code": "CHAT_FAILED",
        "message": "Не получилось получить ответ. Отправьте сообщение ещё раз.",
    }
    assert public_error_detail(detail, include_diagnostic=True)["diagnostic"] == {
        "error": "chat_failed",
        "exceptionType": "RuntimeError",
        "exceptionMessage": "provider exploded",
    }


def test_generate_pet_job_records_provider_failure(monkeypatch, caplog, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            openai_api_key="test-openai-key",
            openrouter_api_key="test-openrouter-key",
        ),
    )
    monkeypatch.setattr(
        "app.services.ai_error_service.AI_FAILURE_LOG_PATH",
        tmp_path / "ai-failures.jsonl",
    )
    caplog.set_level(logging.ERROR, logger="app.routers.tma")

    def fail_generate(_description: str, **_kwargs):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/images")
        response = httpx.Response(
            400,
            json={"error": {"message": "unsupported image model"}},
            headers={"x-request-id": "req-job"},
            request=request,
        )
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    monkeypatch.setattr("app.routers.tma.generate_pet_image_asset_set", fail_generate)
    client = tma_client()

    response = client.post("/api/generate-pet", json={"description": "дракон"})

    assert response.status_code == 202
    job = wait_for_generation_job(client, response.json()["jobId"])
    assert job["status"] == "failed"
    assert job["error"] == {
        "code": "OPENAI_BAD_REQUEST",
        "message": "Не получилось создать питомца. Попробуйте ещё раз.",
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


def test_extract_lite_facts_returns_extracted_patch(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    monkeypatch.setattr(
        "app.routers.tma.extract_lite_overlay_patch_from_reply",
        lambda _payload: ({"facts": [{"text": "Мир состоит из базальтовых гор."}]}, None),
    )

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
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "liteOverlayPatch": {
            "facts": [{"text": "Мир состоит из базальтовых гор."}],
        }
    }

    app.dependency_overrides.clear()


def test_memory_extract_endpoint_returns_operations(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    monkeypatch.setattr(
        "app.routers.tma.extract_user_memory_operations",
        lambda _payload: MemoryExtractionResponse(
            operations=[
                {
                    "type": "remember_user_fact",
                    "kind": "deadline",
                    "text": "У пользователя завтра экзамен.",
                }
            ]
        ),
    )

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
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["operations"][0]["kind"] == "deadline"

    app.dependency_overrides.clear()


def test_memory_consolidate_endpoint_returns_operations(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    monkeypatch.setattr(
        "app.routers.tma.consolidate_user_memory",
        lambda _payload: MemoryConsolidationResponse(
            operations=[{"type": "rewrite_summary", "content": "Короткая сводка."}]
        ),
    )

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
    assert response.json() == {
        "operations": [{"type": "rewrite_summary", "content": "Короткая сводка."}]
    }

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


def test_push_snapshot_endpoint_registers_pet(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fake_register_push_snapshot(user, payload):
        captured["telegram_id"] = user.telegram_id
        captured["pet_id"] = payload.petId
        captured["memory_count"] = len(payload.memoryContext.relevantMemories)
        return LocalPetPushSnapshotResponse(
            registered=True,
            telegramId=user.telegram_id,
            updatedAt="2026-07-07T12:00:00Z",
        )

    monkeypatch.setattr("app.routers.tma.register_push_snapshot", fake_register_push_snapshot)
    client = tma_client()

    response = client.post(
        "/api/push/snapshot",
        json={
            "petId": "pet-1",
            "createdAt": "2026-07-06T12:00:00Z",
            "updatedAt": "2026-07-07T12:00:00Z",
            "lastStatsTickAt": "2026-07-07T12:00:00Z",
            "timezone": "Europe/Moscow",
            "pet": {
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                },
            },
            "memoryContext": {
                "relevantMemories": [
                    {
                        "id": "m1",
                        "kind": "preference",
                        "text": "Пользователь любит короткие сообщения.",
                    }
                ]
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["telegramId"] == 42
    assert captured == {
        "telegram_id": 42,
        "pet_id": "pet-1",
        "memory_count": 1,
    }

    app.dependency_overrides.clear()


def test_delete_push_snapshot_is_authenticated_and_pet_scoped(monkeypatch) -> None:
    captured: dict[str, object] = {}
    rate_limit_buckets: list[str] = []

    def fake_unregister(telegram_id: int, pet_id: str) -> bool:
        captured.update({"telegramId": telegram_id, "petId": pet_id})
        return True

    monkeypatch.setattr(
        "app.routers.tma.check_rate_limit",
        lambda bucket, *_args, **_kwargs: rate_limit_buckets.append(bucket),
    )
    monkeypatch.setattr("app.routers.tma.unregister_push_snapshot", fake_unregister)
    client = tma_client()

    response = client.delete("/api/push/snapshot/pet-1")

    assert response.status_code == 200
    assert response.json() == {"unregistered": True, "petId": "pet-1"}
    assert captured == {"telegramId": 42, "petId": "pet-1"}
    assert rate_limit_buckets == ["push_snapshot_delete"]
    app.dependency_overrides.clear()


def test_delete_push_snapshot_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.dependencies.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            bot_token=None,
            telegram_init_data_max_age_seconds=86400,
        ),
    )
    app.dependency_overrides.clear()

    response = TestClient(app).delete("/api/push/snapshot/pet-1")

    assert response.status_code == 401


def test_delete_push_snapshot_normalizes_pet_id_and_rejects_blank(monkeypatch) -> None:
    captured_pet_ids: list[str] = []
    rate_limit_buckets: list[str] = []
    monkeypatch.setattr(
        "app.routers.tma.check_rate_limit",
        lambda bucket, *_args, **_kwargs: rate_limit_buckets.append(bucket),
    )
    monkeypatch.setattr(
        "app.routers.tma.unregister_push_snapshot",
        lambda _telegram_id, pet_id: captured_pet_ids.append(pet_id) or True,
    )
    client = tma_client()

    normalized = client.delete("/api/push/snapshot/%20pet-1%20")
    blank = client.delete("/api/push/snapshot/%20%20")

    assert normalized.status_code == 200
    assert normalized.json() == {"unregistered": True, "petId": "pet-1"}
    assert blank.status_code == 422
    assert captured_pet_ids == ["pet-1"]
    assert rate_limit_buckets == ["push_snapshot_delete"]
    app.dependency_overrides.clear()


def test_delete_push_snapshot_has_an_independent_rate_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
            push_snapshot_delete_rate_limit_per_hour=1,
        ),
    )
    unregister_calls = 0

    def unregister(_telegram_id: int, _pet_id: str) -> bool:
        nonlocal unregister_calls
        unregister_calls += 1
        return True

    monkeypatch.setattr("app.routers.tma.unregister_push_snapshot", unregister)
    client = tma_client()

    assert client.delete("/api/push/snapshot/pet-1").status_code == 200
    limited = client.delete("/api/push/snapshot/pet-2")

    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"
    assert unregister_calls == 1
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            TelegramPushRecordTooLargeError(actual_bytes=2_000, max_bytes=1_000),
            413,
            "PUSH_RECORD_TOO_LARGE",
        ),
        (
            TelegramPushStoreCapacityError("full"),
            507,
            "PUSH_STORE_CAPACITY",
        ),
    ],
)
def test_delete_push_snapshot_maps_capacity_errors_to_public_response(
    monkeypatch,
    error,
    expected_status,
    expected_code,
) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fail_unregister(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("app.routers.tma.unregister_push_snapshot", fail_unregister)
    response = tma_client().delete("/api/push/snapshot/pet-1")

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    app.dependency_overrides.clear()


def test_push_snapshot_rate_limit_deduplicates_modern_revision(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
            push_snapshot_rate_limit_per_hour=60,
            push_snapshot_attempt_rate_limit_per_hour=1_000,
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.register_push_snapshot",
        lambda user, _payload: LocalPetPushSnapshotResponse(
            registered=True,
            telegramId=user.telegram_id,
            updatedAt="2026-07-15T12:00:00Z",
        ),
    )
    client = tma_client()
    payload = {
        "petId": "pet-1",
        "snapshotWriterId": "writer-session-00000009",
        "snapshotRevision": 1,
        "pet": {
            "description": "маленький космический котенок",
            "stage": "baby",
            "mood": "idle",
            "stats": {"hunger": 80, "happiness": 70, "energy": 60},
        },
    }

    assert client.post("/api/push/snapshot", json=payload).status_code == 200
    assert client.post("/api/push/snapshot", json=payload).status_code == 200
    for revision in range(2, 61):
        assert (
            client.post(
                "/api/push/snapshot",
                json={**payload, "snapshotRevision": revision},
            ).status_code
            == 200
        )

    response = client.post(
        "/api/push/snapshot",
        json={**payload, "snapshotRevision": 61},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    app.dependency_overrides.clear()


def test_push_snapshot_attempt_limit_caps_replays_of_same_revision(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
            push_snapshot_rate_limit_per_hour=60,
            push_snapshot_attempt_rate_limit_per_hour=2,
        ),
    )
    calls = 0

    def register(user, _payload):
        nonlocal calls
        calls += 1
        return LocalPetPushSnapshotResponse(
            registered=True,
            telegramId=user.telegram_id,
            updatedAt="2026-07-15T12:00:00Z",
        )

    monkeypatch.setattr("app.routers.tma.register_push_snapshot", register)
    client = tma_client()
    payload = {
        "petId": "pet-1",
        "snapshotWriterId": "writer-session-00000010",
        "snapshotRevision": 1,
        "pet": {
            "description": "маленький космический котенок",
            "stage": "baby",
            "mood": "idle",
            "stats": {"hunger": 80, "happiness": 70, "energy": 60},
        },
    }

    assert client.post("/api/push/snapshot", json=payload).status_code == 200
    assert client.post("/api/push/snapshot", json=payload).status_code == 200
    response = client.post("/api/push/snapshot", json=payload)

    assert response.status_code == 429
    assert calls == 2
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            TelegramPushRecordTooLargeError(actual_bytes=2_000, max_bytes=1_000),
            413,
            "PUSH_RECORD_TOO_LARGE",
        ),
        (
            TelegramPushStoreCapacityError("full"),
            507,
            "PUSH_STORE_CAPACITY",
        ),
    ],
)
def test_push_snapshot_maps_capacity_errors_to_public_response(
    monkeypatch,
    error,
    expected_status,
    expected_code,
) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fail_register(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("app.routers.tma.register_push_snapshot", fail_register)
    client = tma_client()
    response = client.post(
        "/api/push/snapshot",
        json={
            "petId": "pet-1",
            "pet": {
                "description": "маленький космический котенок",
                "stage": "baby",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 70, "energy": 60},
            },
        },
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    app.dependency_overrides.clear()


def test_generation_rate_limit(monkeypatch, tmp_path) -> None:
    now = datetime.now(UTC)
    submissions = 0
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
            generation_rate_limit_per_day=3,
            openai_api_key="test-openai-key",
            openrouter_api_key="test-openrouter-key",
        ),
    )

    def fake_submit(_description, _user, request_key=None):
        nonlocal submissions
        assert request_key is not None
        submissions += 1
        return {
            "jobId": f"synthetic-job-{submissions}",
            "status": "queued",
            "phase": "queued",
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr("app.routers.tma.submit_generation_job", fake_submit)
    client = tma_client()

    for index in range(3):
        assert (
            client.post(
                "/api/generate-pet",
                headers={"Idempotency-Key": f"pet-request-quota-{index:04d}"},
                json={"description": "дракон"},
            ).status_code
            == 202
        )

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": "pet-request-quota-9999"},
        json={"description": "дракон"},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    assert response.json()["detail"]["retryAfterSeconds"] > 0
    assert submissions == 3

    app.dependency_overrides.clear()


@pytest.mark.parametrize("openai_api_key", [None, "   "])
def test_generate_pet_missing_credentials_does_not_consume_quota(
    monkeypatch,
    tmp_path,
    openai_api_key,
) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(store_path),
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            generation_rate_limit_per_day=1,
            openai_api_key=openai_api_key,
        ),
    )
    client = tma_client()

    first = client.post("/api/generate-pet", json={"description": "мышонок"})
    second = client.post("/api/generate-pet", json={"description": "мышонок"})

    assert first.status_code == 500
    assert second.status_code == 500
    assert not store_path.exists()
    app.dependency_overrides.clear()


def test_generate_pet_queue_rejection_refunds_quota(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(store_path),
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            generation_rate_limit_per_day=1,
            openai_api_key="test-openai-key",
        ),
    )

    def reject_submission(_description, _user, request_key=None):
        assert request_key is not None
        raise HTTPException(status_code=503, detail={"code": "GENERATION_QUEUE_FULL"})

    monkeypatch.setattr("app.routers.tma.submit_generation_job", reject_submission)
    client = tma_client()

    response = client.post("/api/generate-pet", json={"description": "мышонок"})

    assert response.status_code == 503
    with sqlite3.connect(store_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = 'generation'"
        ).fetchone()[0]
    assert count == 0
    app.dependency_overrides.clear()


def test_generate_pet_idempotency_key_reuses_quota_event(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    now = datetime.now(UTC)
    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(store_path),
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            generation_rate_limit_per_day=1,
            openai_api_key="test-openai-key",
        ),
    )

    def fake_submit(description, _user, request_key=None):
        captured.append((description, request_key))
        return {
            "jobId": "job-idempotent",
            "status": "queued",
            "phase": "queued",
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr("app.routers.tma.submit_generation_job", fake_submit)
    monkeypatch.setattr("app.routers.tma.find_generation_job_by_request_key", lambda *_args: None)
    client = tma_client()
    headers = {"Idempotency-Key": "pet-request-0001"}

    first = client.post(
        "/api/generate-pet",
        headers=headers,
        json={"description": "мышонок"},
    )
    replay = client.post(
        "/api/generate-pet",
        headers=headers,
        json={"description": "мышонок"},
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert first.json() == replay.json()
    assert captured == [
        ("мышонок", "pet-request-0001"),
        ("мышонок", "pet-request-0001"),
    ]
    with sqlite3.connect(store_path) as connection:
        rows = connection.execute(
            "SELECT request_key FROM rate_limit_events WHERE bucket = 'generation'"
        ).fetchall()
    assert rows == [("generate-pet:pet-request-0001",)]
    app.dependency_overrides.clear()


def test_generate_pet_replay_returns_job_before_credentials_or_quota(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime.now(UTC)
    existing = {
        "jobId": "job-existing",
        "status": "queued",
        "phase": "queued",
        "createdAt": now,
        "updatedAt": now,
    }
    monkeypatch.setattr(
        "app.routers.tma.find_generation_job_by_request_key",
        lambda request_key, user, *_payload: (
            existing
            if request_key == "pet-request-existing-0001" and user.telegram_id == 42
            else None
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.submit_generation_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("replay started a new generation job")
        ),
    )
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": "pet-request-existing-0001"},
        json={"description": "мышонок"},
    )

    assert response.status_code == 202
    assert response.json()["jobId"] == "job-existing"
    app.dependency_overrides.clear()


def test_generate_pet_rejects_idempotency_key_reused_for_another_payload(
    monkeypatch,
    tmp_path,
) -> None:
    class ConflictingService:
        def find_by_request_key(self, *_args):
            raise tma_router.GenerationIdempotencyConflictError("synthetic conflict")

    monkeypatch.setattr(tma_router, "_generation_job_service", lambda: ConflictingService())
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
        ),
    )
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": "pet-request-conflict-0001"},
        json={"description": "другой питомец"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_IDEMPOTENCY_CONFLICT"
    app.dependency_overrides.clear()


def test_generate_pet_rejects_another_active_job_for_same_owner(
    monkeypatch,
    tmp_path,
) -> None:
    reservation = object()
    refunds: list[object] = []

    class ActiveService:
        def find_by_request_key(self, *_args):
            return None

        def submit(self, *_args, **_kwargs):
            raise tma_router.GenerationOwnerActiveError(
                "job-already-running",
                f"  {'я' * 350}  ",
            )

    monkeypatch.setattr(tma_router, "_generation_job_service", lambda: ActiveService())
    monkeypatch.setattr(tma_router, "check_rate_limit", lambda *_args, **_kwargs: reservation)
    monkeypatch.setattr(tma_router, "refund_rate_limit", refunds.append)
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            openai_api_key="test-openai-key",
            enable_in_memory_rate_limit=False,
        ),
    )
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": "pet-request-owner-active-0001"},
        json={"description": "второй питомец"},
    )

    assert response.status_code == 409
    assert response.headers["retry-after"] == "15"
    assert response.json()["detail"]["code"] == "GENERATION_ALREADY_ACTIVE"
    assert response.json()["detail"]["activeJobId"] == "job-already-running"
    assert response.json()["detail"]["activeDescription"] == "я" * 300
    assert refunds == [reservation]
    app.dependency_overrides.clear()


def test_generate_pet_adopts_durable_alias_after_owner_active_conflict(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime.now(UTC)
    existing = {
        "jobId": "job-already-running",
        "status": "running",
        "phase": "generating_images",
        "createdAt": now,
        "updatedAt": now,
    }

    class AliasedActiveService:
        alias_bound = False
        lookup_calls = 0

        def find_by_request_key(self, *_args):
            self.lookup_calls += 1
            return existing if self.alias_bound else None

        def submit(self, *_args, **_kwargs):
            self.alias_bound = True
            raise tma_router.GenerationOwnerActiveError(
                "job-already-running",
                "первый питомец",
            )

    service = AliasedActiveService()
    reservation = object()
    refunds: list[object] = []
    monkeypatch.setattr(tma_router, "_generation_job_service", lambda: service)
    monkeypatch.setattr(tma_router, "check_rate_limit", lambda *_args, **_kwargs: reservation)
    monkeypatch.setattr(tma_router, "refund_rate_limit", refunds.append)
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            openai_api_key="test-openai-key",
        ),
    )
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": "pet-request-owner-alias-0001"},
        json={"description": "первый питомец"},
    )

    assert response.status_code == 202
    assert response.json()["jobId"] == "job-already-running"
    assert service.lookup_calls == 2
    assert refunds == [reservation]
    app.dependency_overrides.clear()


def test_idempotent_replay_failure_does_not_refund_original_quota_event(
    monkeypatch,
    tmp_path,
) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    now = datetime.now(UTC)
    calls = 0
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(store_path),
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            generation_rate_limit_per_day=1,
            openai_api_key="test-openai-key",
        ),
    )

    def fake_submit(_description, _user, request_key=None):
        nonlocal calls
        calls += 1
        assert request_key == "pet-request-0002"
        if calls == 2:
            raise HTTPException(status_code=503, detail={"code": "SYNTHETIC_REJECTION"})
        return {
            "jobId": "job-idempotent",
            "status": "queued",
            "phase": "queued",
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr("app.routers.tma.submit_generation_job", fake_submit)
    monkeypatch.setattr("app.routers.tma.find_generation_job_by_request_key", lambda *_args: None)
    client = tma_client()
    headers = {"Idempotency-Key": "pet-request-0002"}

    assert (
        client.post(
            "/api/generate-pet",
            headers=headers,
            json={"description": "мышонок"},
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/generate-pet",
            headers=headers,
            json={"description": "мышонок"},
        ).status_code
        == 503
    )

    with sqlite3.connect(store_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = 'generation'"
        ).fetchone()[0]
    assert count == 1
    app.dependency_overrides.clear()


def test_idempotent_admission_serializes_refund_and_concurrent_submit(
    monkeypatch,
    tmp_path,
) -> None:
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    refunds = 0
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            generation_job_store_path=str(tmp_path / "generation-jobs.sqlite3"),
            openai_api_key="test-openai-key",
        ),
    )
    monkeypatch.setattr("app.routers.tma.find_generation_job_by_request_key", lambda *_args: None)
    monkeypatch.setattr("app.routers.tma.check_rate_limit", lambda *_args, **_kwargs: object())

    def fake_refund(_reservation) -> None:
        nonlocal refunds
        refunds += 1

    def fake_submit(description, _user, request_key=None):
        assert request_key == "pet-request-race-0001"
        if description == "первый":
            first_entered.set()
            assert release_first.wait(timeout=2)
            raise HTTPException(status_code=503, detail={"code": "SYNTHETIC_REJECTION"})
        second_entered.set()
        return {
            "jobId": "job-second",
            "status": "queued",
            "phase": "queued",
            "createdAt": now,
            "updatedAt": now,
        }

    monkeypatch.setattr("app.routers.tma.refund_rate_limit", fake_refund)
    monkeypatch.setattr("app.routers.tma.submit_generation_job", fake_submit)
    client = tma_client()
    headers = {"Idempotency-Key": "pet-request-race-0001"}

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            client.post,
            "/api/generate-pet",
            headers=headers,
            json={"description": "первый"},
        )
        assert first_entered.wait(timeout=2)
        second = executor.submit(
            client.post,
            "/api/generate-pet",
            headers=headers,
            json={"description": "второй"},
        )
        assert not second_entered.wait(timeout=0.05)
        release_first.set()
        assert first.result(timeout=2).status_code == 503
        assert second.result(timeout=2).status_code == 202

    assert refunds == 1
    app.dependency_overrides.clear()


def test_chat_admission_rejects_per_user_and_global_before_sync_handler(monkeypatch) -> None:
    entered = Event()
    release = Event()
    calls = 0
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            http_llm_global_concurrency=1,
            http_llm_per_user_concurrency=1,
            http_admission_retry_after_seconds=7,
        ),
    )

    async def user_from_header(request: Request) -> TelegramUserContext:
        telegram_id = int(request.headers.get("x-test-user", "42"))
        return TelegramUserContext(
            telegram_id=telegram_id,
            username=f"user-{telegram_id}",
            first_name="Synthetic",
            language_code="ru",
            auth_date=datetime.now(UTC),
        )

    def blocking_chat(_payload) -> LocalChatResponse:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=3)
        return LocalChatResponse(reply="Я рядом.", moodHint="happy")

    monkeypatch.setattr(tma_router, "chat_with_local_pet", blocking_chat)
    app.dependency_overrides[get_telegram_user] = user_from_header
    client = TestClient(app)
    payload = {
        "message": "Как ты?",
        "pet": {
            "description": "маленький космический котенок",
            "stage": "baby",
            "mood": "idle",
            "stats": {"hunger": 80, "happiness": 70, "energy": 60},
        },
        "history": [],
    }

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            client.post,
            "/api/chat",
            headers={"x-test-user": "42"},
            json=payload,
        )
        assert entered.wait(timeout=2)
        try:
            same_user = client.post(
                "/api/chat",
                headers={"x-test-user": "42"},
                json=payload,
            )
            other_user = client.post(
                "/api/chat",
                headers={"x-test-user": "43"},
                json=payload,
            )
        finally:
            release.set()
        first_response = first.result(timeout=2)

    assert first_response.status_code == 200
    assert same_user.status_code == 429
    assert same_user.json()["detail"]["code"] == "REQUEST_ADMISSION_USER_LIMIT"
    assert same_user.headers["Retry-After"] == "7"
    assert other_user.status_code == 503
    assert other_user.json()["detail"]["code"] == "REQUEST_ADMISSION_GLOBAL_LIMIT"
    assert other_user.headers["Retry-After"] == "7"
    assert calls == 1

    after_release = client.post(
        "/api/chat",
        headers={"x-test-user": "42"},
        json=payload,
    )
    assert after_release.status_code == 200
    assert calls == 2


def test_chat_admission_releases_after_handler_exception(monkeypatch) -> None:
    calls = 0
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            http_llm_global_concurrency=1,
            http_llm_per_user_concurrency=1,
            http_admission_retry_after_seconds=5,
        ),
    )

    def fail_once(_payload) -> LocalChatResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPException(status_code=418, detail={"code": "SYNTHETIC_FAILURE"})
        return LocalChatResponse(reply="Снова работаю.", moodHint="happy")

    monkeypatch.setattr(tma_router, "chat_with_local_pet", fail_once)
    client = tma_client()
    payload = {
        "message": "Как ты?",
        "pet": {
            "description": "маленький космический котенок",
            "stage": "baby",
            "mood": "idle",
            "stats": {"hunger": 80, "happiness": 70, "energy": 60},
        },
        "history": [],
    }

    failed = client.post("/api/chat", json=payload)
    recovered = client.post("/api/chat", json=payload)

    assert failed.status_code == 418
    assert recovered.status_code == 200
    assert calls == 2


def test_llm_admission_dependency_releases_on_async_generator_cancel(monkeypatch) -> None:
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            http_llm_global_concurrency=1,
            http_llm_per_user_concurrency=1,
            http_admission_retry_after_seconds=5,
        ),
    )
    user = override_user()

    async def cancel_dependency() -> None:
        dependency = tma_router._llm_request_admission(user)
        await anext(dependency)
        with pytest.raises(HTTPException) as rejected:
            tma_router._acquire_public_request_admission(
                user,
                bucket="llm",
                global_setting="http_llm_global_concurrency",
                per_user_setting="http_llm_per_user_concurrency",
                default_global_limit=1,
                default_per_user_limit=1,
            )
        assert rejected.value.status_code == 429
        await dependency.aclose()

        replacement = tma_router._acquire_public_request_admission(
            user,
            bucket="llm",
            global_setting="http_llm_global_concurrency",
            per_user_setting="http_llm_per_user_concurrency",
            default_global_limit=1,
            default_per_user_limit=1,
        )
        replacement.release()

    asyncio.run(cancel_dependency())


def test_async_health_bypasses_saturated_fastapi_sync_threadpool(monkeypatch) -> None:
    entered = Event()
    release = Event()
    monkeypatch.setattr(
        tma_router,
        "get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=False,
            http_llm_global_concurrency=1,
            http_llm_per_user_concurrency=1,
            http_admission_retry_after_seconds=5,
        ),
    )
    monkeypatch.setattr(
        tma_router,
        "chat_with_local_pet",
        lambda _payload: (
            entered.set(),
            release.wait(timeout=3),
            LocalChatResponse(reply="Я рядом.", moodHint="happy"),
        )[-1],
    )
    monkeypatch.setattr(main_module, "scheduler_runtime_status", lambda: {})
    monkeypatch.setattr(main_module.tma, "generation_job_runtime_status", lambda: {"stuck": 0})
    monkeypatch.setattr(main_module, "llm_runtime_status", lambda: {"status": "ok"})
    monkeypatch.setattr(main_module, "media_runtime_status", lambda: {"status": "ok"})
    monkeypatch.setattr(
        main_module,
        "storage_runtime_status",
        lambda: {"status": "ok", "failedPaths": []},
    )
    monkeypatch.setattr(main_module, "notify_ops", lambda *_args, **_kwargs: None)

    isolated_app = FastAPI()
    isolated_app.include_router(tma_router.router)
    isolated_app.add_api_route("/health", main_module.health, methods=["GET"])
    isolated_app.dependency_overrides[get_telegram_user] = override_user
    payload = {
        "message": "Как ты?",
        "pet": {
            "description": "маленький космический котенок",
            "stage": "baby",
            "mood": "idle",
            "stats": {"hunger": 80, "happiness": 70, "energy": 60},
        },
        "history": [],
    }

    async def set_thread_tokens(value: int) -> int:
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = value
        return previous

    with TestClient(isolated_app) as client:
        assert client.portal is not None
        previous_tokens = client.portal.call(set_thread_tokens, 1)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                chat_future = executor.submit(client.post, "/api/chat", json=payload)
                assert entered.wait(timeout=2)
                health_future = executor.submit(client.get, "/health")
                try:
                    health_response = health_future.result(timeout=1)
                finally:
                    release.set()
                chat_response = chat_future.result(timeout=2)
        finally:
            client.portal.call(set_thread_tokens, previous_tokens)

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert chat_response.status_code == 200


@pytest.mark.parametrize(
    "idempotency_key",
    ["short", "contains space", "contains/slash", "x" * 97],
)
def test_generate_pet_rejects_invalid_idempotency_key(
    monkeypatch,
    idempotency_key,
) -> None:
    called = False

    def fake_submit(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid header reached generation admission")

    monkeypatch.setattr("app.routers.tma.submit_generation_job", fake_submit)
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        headers={"Idempotency-Key": idempotency_key},
        json={"description": "мышонок"},
    )

    assert response.status_code == 422
    assert called is False
    app.dependency_overrides.clear()


def test_chat_rate_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            enable_in_memory_rate_limit=True,
            rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
            chat_rate_limit_per_hour=30,
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.chat_with_local_pet",
        lambda payload: LocalChatResponse(reply="Я рядом.", moodHint="happy"),
    )
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


def test_interactive_travel_debug_reset_clears_durable_quota(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    settings = SimpleNamespace(
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(store_path),
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(tmp_path / "travel-owners.sqlite3"),
        diagnostic_telegram_ids={42},
    )
    monkeypatch.setattr(tma_router, "get_settings", lambda: settings)
    monkeypatch.setattr(tma_router, "reset_interactive_travel_generation", lambda _travel_id: None)
    limiter = get_rate_limiter(store_path)
    limiter.check("interactive_travel", 42, limit=1, window=timedelta(days=1))
    tma_router._interactive_travel_session_store().register_owner(
        "interactive-travel-test",
        override_user().telegram_id,
    )

    response = tma_router.interactive_travel_debug_reset("interactive-travel-test", override_user())

    assert response == {"reset": True}
    assert limiter.check(
        "interactive_travel",
        42,
        limit=1,
        window=timedelta(days=1),
    )
