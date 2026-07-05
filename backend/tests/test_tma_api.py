from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import get_telegram_user
from app.main import app
from app.routers.tma import generation_error_message
from app.schemas import GeneratePetAssetResponse, LocalChatResponse
from app.services.pet_memory.models import (
    DevelopmentPatch,
    MemoryCandidate,
    RelationshipPatch,
)
from app.services.pet_reply_engine.models import PetReplyResult
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
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
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

    assert response.status_code == 200
    payload = response.json()
    assert payload["assetSetId"] == "asset-1"
    assert payload["characterBible"]["species"] == "small dragon mascot"
    assert captured["description"] == "маленький дракон"
    assert captured["kwargs"] == {"use_template_presets": False}
    for stage in ("baby", "teen", "adult"):
        assert set(payload["images"][stage]) == {"idle", "happy", "hungry", "sad"}

    app.dependency_overrides.clear()


def test_generate_pet_passes_template_preset_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )
    captured: dict[str, object] = {}

    def fake_generate_pet_asset_set(description: str, **kwargs):
        captured["description"] = description
        captured["kwargs"] = kwargs
        return {
            "assetSetId": "asset-1",
            "generatedAt": datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
            "images": {
                stage: {
                    "idle": f"/static/generated/asset-1/{stage}-idle.png",
                    "happy": f"/static/generated/asset-1/{stage}-happy.png",
                    "hungry": f"/static/generated/asset-1/{stage}-hungry.png",
                    "sad": f"/static/generated/asset-1/{stage}-sad.png",
                }
                for stage in ("baby", "teen", "adult")
            },
            "characterBible": {"species": "дракон"},
        }

    monkeypatch.setattr("app.routers.tma.generate_pet_asset_set", fake_generate_pet_asset_set)
    client = tma_client()

    response = client.post(
        "/api/generate-pet",
        json={"description": "я хочу сделать дракона", "useTemplatePresets": True},
    )

    assert response.status_code == 200
    assert captured["description"] == "я хочу сделать дракона"
    assert captured["kwargs"] == {"use_template_presets": True}

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
        "loreMemoriesToSave": [],
    }
    assert captured["lore"] == {"home": {"favorite_spot": "мягкая звездная подушка"}}

    app.dependency_overrides.clear()


def test_chat_accepts_memory_and_returns_memory_patch(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fake_generate(_reply_input):
        return PetReplyResult(
            reply="Кап будит меня утром тихим звоном. хочешь, расскажу где мы прячемся?",
            mood_hint="happy",
            memory_candidates=(
                MemoryCandidate(
                    type="friend_fact",
                    text="У питомца есть друг Кап, маленькая капля росы.",
                    importance=0.8,
                    confidence=0.8,
                ),
            ),
            relationship_patch=RelationshipPatch(trustDelta=1, attachmentDelta=1),
            development_patch=DevelopmentPatch(curiosityDelta=1, confidenceDelta=1),
        )

    monkeypatch.setattr("app.services.chat_service.generate_pet_reply", fake_generate)
    client = tma_client()

    response = client.post(
        "/api/chat",
        json={
            "message": "кто твои друзья?",
            "pet": {
                "name": "Листик",
                "description": "серый челик с листом вместо лица",
                "characterBible": {"species": "leaf mascot"},
                "stage": "teen",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 70,
                    "energy": 60,
                    "cleanliness": 90,
                },
                "memory": {
                    "schemaVersion": 1,
                    "canon": [],
                    "relationship": {
                        "trust": 20,
                        "attachment": 20,
                        "familiarity": 0,
                        "sharedEvents": [],
                        "userFacts": [],
                        "boundaries": [],
                    },
                    "threads": [],
                    "reflections": [],
                    "activeGoals": [],
                    "development": {
                        "trust": 20,
                        "attachment": 20,
                        "curiosity": 45,
                        "confidence": 30,
                        "loneliness": 10,
                        "playfulness": 50,
                    },
                    "events": [],
                    "rejectedCandidates": [],
                },
            },
            "history": [{"role": "user", "text": "Привет"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"].startswith("Кап будит")
    assert payload["moodHint"] == "happy"
    assert payload["memoryPatch"]["canonUpserts"][0]["type"] == "friend_fact"
    assert payload["memoryPatch"]["relationshipPatch"]["trust"] == 21
    assert payload["memoryPatch"]["developmentPatch"]["curiosity"] == 46
    assert payload["loreMemoriesToSave"] == [
        "ЛОР: У питомца есть друг Кап, маленькая капля росы."
    ]

    app.dependency_overrides.clear()


def test_chat_returns_fallback_on_openai_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def raise_openai_error():
        raise RuntimeError("openai down")

    monkeypatch.setattr(
        "app.services.pet_reply_engine.reply_generator.get_openai_client",
        raise_openai_error,
    )
    client = tma_client()

    response = client.post(
        "/api/chat",
        json={
            "message": "Как ты?",
            "pet": {
                "name": "Пушок",
                "description": "маленький пушистый дракончик с шарфиком",
                "characterBible": {
                    "species": "soft dragon mascot",
                    "signature_features": ["warm scarf", "tiny horns"],
                    "materials": ["fluffy toy skin"],
                },
                "stage": "baby",
                "mood": "hungry",
                "stats": {
                    "hunger": 18,
                    "happiness": 70,
                    "energy": 25,
                    "cleanliness": 90,
                },
            },
            "history": [],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Я устал... спатки хочу...",
        "moodHint": "hungry",
        "loreMemoriesToSave": [],
    }

    app.dependency_overrides.clear()


def test_chat_wraps_unhandled_error_with_cors(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=False),
    )

    def fail_chat(_payload):
        raise RuntimeError("openai rejected chat request")

    monkeypatch.setattr("app.routers.tma.chat_with_local_pet", fail_chat)
    client = tma_client()

    response = client.post(
        "/api/chat",
        headers={"Origin": "http://localhost:3000"},
        json={
            "message": "расскажи о своем мире",
            "replyMode": "lite",
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


def test_generation_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=True),
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
        assert client.post("/api/generate-pet", json={"description": "дракон"}).status_code == 200

    response = client.post("/api/generate-pet", json={"description": "дракон"})

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"

    app.dependency_overrides.clear()


def test_chat_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=True),
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
