from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.dependencies import get_telegram_user
from app.main import app
from app.schemas import LocalChatResponse
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
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_asset_set",
        lambda description: {
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
        },
    )
    client = tma_client()

    response = client.post("/api/generate-pet", json={"description": "маленький дракон"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["assetSetId"] == "asset-1"
    assert payload["characterBible"]["species"] == "small dragon mascot"
    for stage in ("baby", "teen", "adult"):
        assert set(payload["images"][stage]) == {"idle", "happy", "hungry", "sad"}

    app.dependency_overrides.clear()


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
        "reply": "фыр... ням)",
        "moodHint": "hungry",
        "loreMemoriesToSave": [],
    }

    app.dependency_overrides.clear()


def test_generation_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(enable_in_memory_rate_limit=True),
    )
    monkeypatch.setattr(
        "app.routers.tma.generate_pet_asset_set",
        lambda description: {
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
