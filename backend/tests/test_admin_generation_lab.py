from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.dependencies import get_telegram_user
from app.main import app

LOCAL_HEADERS = {
    "host": "localhost:8000",
    "origin": "http://localhost:3000",
}


def enabled_settings(token: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        enable_admin_generation_lab=True,
        admin_generation_lab_token=token,
    )


def sample_character_bible() -> dict:
    return {
        "species": "soft dragon mascot",
        "personality": "curious and warm",
        "main_colors": ["green", "cream"],
        "signature_features": ["soft wings", "tiny horns"],
        "materials": ["plush scales"],
        "lore": {
            "world": {"name": "Теплая полка"},
            "home": {"favorite_spot": "рядом с лампой"},
            "origin": {"birthplace": "коробка с тканями"},
            "relationships": {"attitude_to_user": "доверяет постепенно"},
            "inner_life": {"dreams": ["летать над столом"]},
            "voice": {"favorite_phrases": ["шур"]},
            "growth_arc": {
                "baby": "учится махать крыльями",
                "teen": "становится смелее",
                "adult": "бережет свой уголок",
            },
        },
    }


def admin_client(monkeypatch, *, token: str | None = None) -> TestClient:
    monkeypatch.setattr(
        "app.routers.admin_generation_lab.get_settings",
        lambda: enabled_settings(token),
    )
    return TestClient(app)


def test_admin_endpoint_returns_404_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.admin_generation_lab.get_settings",
        lambda: SimpleNamespace(
            enable_admin_generation_lab=False,
            admin_generation_lab_token=None,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={"description": "маленький дракон", "mode": "profile_only"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ADMIN_GENERATION_LAB_DISABLED"


def test_admin_endpoint_returns_403_for_non_localhost(monkeypatch) -> None:
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers={
            "host": "example.com",
            "origin": "http://localhost:3000",
        },
        json={"description": "маленький дракон", "mode": "profile_only"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_GENERATION_LAB_FORBIDDEN"


def test_admin_endpoint_requires_token_when_configured(monkeypatch) -> None:
    client = admin_client(monkeypatch, token="secret")

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={"description": "маленький дракон", "mode": "profile_only"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_GENERATION_LAB_FORBIDDEN"


def test_admin_profile_only_does_not_require_telegram_auth_or_image_generation(monkeypatch) -> None:
    app.dependency_overrides[get_telegram_user] = lambda: (_ for _ in ()).throw(
        AssertionError("Telegram auth must not be used")
    )
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.create_character_bible",
        lambda description: sample_character_bible(),
    )
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.generate_pet_asset_set",
        lambda description: (_ for _ in ()).throw(
            AssertionError("profile_only must not generate images")
        ),
    )
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={
            "description": "маленький дракон",
            "mode": "profile_only",
            "slotId": "slot-1",
            "includeDebugPrompts": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["slotId"] == "slot-1"
    assert payload["status"] == "ready"
    assert payload["mode"] == "profile_only"
    assert payload["characterBible"]["species"] == "soft dragon mascot"
    assert payload["images"] is None
    assert payload["debug"]["characterBiblePrompt"]
    assert payload["debug"]["spriteSheetPrompt"] is None

    app.dependency_overrides.clear()


def test_admin_full_assets_is_temporarily_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.generate_pet_asset_set",
        lambda description: (_ for _ in ()).throw(
            AssertionError("full_assets must not generate images in admin")
        ),
    )
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={
            "description": "маленький дракон",
            "mode": "full_assets",
            "includeDebugPrompts": True,
        },
    )

    assert response.status_code == 400
    payload = response.json()["detail"]
    assert payload["code"] == "ADMIN_FULL_ASSETS_DISABLED"
    assert payload["status"] == "failed"
    assert payload["description"] == "маленький дракон"


def test_admin_self_intro_benchmark_failure_does_not_fail_slot(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.create_character_bible",
        lambda description: sample_character_bible(),
    )

    def raise_reply_error(reply_input):
        raise RuntimeError("reply failed")

    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.generate_pet_reply",
        raise_reply_error,
    )
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={
            "description": "маленький дракон",
            "mode": "profile_only",
            "includeDebugPrompts": True,
            "includeSelfIntroBenchmark": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["benchmark"]["question"] == "расскажи о себе"
    assert payload["benchmark"]["usedFallback"] is True
    assert payload["benchmark"]["validationFlags"] == ["benchmark_error:RuntimeError"]
    assert payload["benchmark"]["qualityPassed"] is False
    assert "used_fallback" in payload["benchmark"]["qualityFlags"]
    assert payload["debug"]["selfIntroBenchmarkMessages"]


def test_admin_self_intro_benchmark_returns_generated_reply(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.create_character_bible",
        lambda description: sample_character_bible(),
    )
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.generate_pet_reply",
        lambda reply_input: SimpleNamespace(
            reply="Я мягкий дракончик, живу у теплой лампы.",
            mood_hint="happy",
            used_fallback=False,
            validation_flags=("voice_ok",),
        ),
    )
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={
            "description": "маленький дракон",
            "mode": "profile_only",
            "includeSelfIntroBenchmark": True,
        },
    )

    assert response.status_code == 200
    benchmark = response.json()["benchmark"]
    assert benchmark["reply"] == "Я мягкий дракончик, живу у теплой лампы."
    assert benchmark["moodHint"] == "happy"
    assert benchmark["usedFallback"] is False
    assert benchmark["validationFlags"] == ["voice_ok"]
    assert benchmark["qualityScore"] is not None
    assert isinstance(benchmark["qualityFlags"], list)


def test_admin_conversation_benchmark_runs_multiple_turns_with_history(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.create_character_bible",
        lambda description: sample_character_bible(),
    )
    calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def fake_reply(reply_input):
        calls.append(
            (
                reply_input.user_text,
                tuple((item.role, item.text) for item in reply_input.recent_messages),
            )
        )
        return SimpleNamespace(
            reply=f"ответ: {reply_input.user_text}",
            mood_hint="idle",
            used_fallback=False,
            validation_flags=(),
        )

    monkeypatch.setattr(
        "app.services.admin_generation_lab_service.generate_pet_reply",
        fake_reply,
    )
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={
            "description": "маленький дракон",
            "mode": "profile_only",
            "includeSelfIntroBenchmark": True,
            "includeConversationBenchmark": True,
        },
    )

    assert response.status_code == 200
    turns = response.json()["benchmark"]["turns"]
    assert len(turns) == 12
    assert turns[1]["question"] == "что ты любишь?"
    assert turns[-1]["question"] == "что у тебя за привычка?"
    assert calls[2][0] == "почему?"
    assert ("user", "что ты любишь?") in calls[2][1]
    assert ("pet", "ответ: что ты любишь?") in calls[2][1]


def test_admin_generation_error_response_contains_slot_code_and_duration(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.admin_generation_lab.admin_service.generate_admin_profile_only",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = admin_client(monkeypatch)

    response = client.post(
        "/admin/generation-lab/generate-one",
        headers=LOCAL_HEADERS,
        json={
            "description": "маленький дракон",
            "mode": "profile_only",
            "slotId": "slot-err",
        },
    )

    assert response.status_code == 502
    payload = response.json()["detail"]
    assert payload["slotId"] == "slot-err"
    assert payload["description"] == "маленький дракон"
    assert payload["status"] == "failed"
    assert payload["code"] == "GENERATION_FAILED"
    assert isinstance(payload["durationMs"], int)
