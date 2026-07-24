from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from app.services import outfit_service


def _png_bytes(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 48), color).save(output, format="PNG")
    return output.getvalue()


def test_simplified_outfit_prompt_uses_accusative_item(monkeypatch) -> None:
    monkeypatch.setattr(
        outfit_service,
        "get_settings",
        lambda: SimpleNamespace(
            openai_chat_model="test-model",
            openai_chat_timeout_seconds=10,
        ),
    )
    monkeypatch.setattr(outfit_service, "resolve_llm_model", lambda *_args: "test-model")
    monkeypatch.setattr(
        outfit_service,
        "complete_chat",
        lambda *_args, **_kwargs: SimpleNamespace(
            content='{"item":"футболку Аргентины","displayItem":"футболка Аргентины"}'
        ),
    )

    item, display_item, prompt = outfit_service.simplify_outfit_request(
        "прикольненькая милая футболка аргентины",
        "старое описание больше не участвует",
    )

    assert item == "футболку Аргентины"
    assert display_item == "футболка Аргентины"
    assert prompt == "Добавь персонажу футболку Аргентины."


def test_simplified_outfit_prompt_supports_makeup(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        outfit_service,
        "get_settings",
        lambda: SimpleNamespace(
            openai_chat_model="test-model",
            openai_chat_timeout_seconds=10,
        ),
    )
    monkeypatch.setattr(outfit_service, "resolve_llm_model", lambda *_args: "test-model")

    def fake_complete_chat(_operation, payload):
        captured.update(payload)
        return SimpleNamespace(
            content=(
                '{"item":"чёрно-белый блэк-метал корпспейнт",'
                '"displayItem":"чёрно-белый блэк-метал корпспейнт"}'
            )
        )

    monkeypatch.setattr(outfit_service, "complete_chat", fake_complete_chat)

    item, display_item, prompt = outfit_service.simplify_outfit_request(
        "black metal corpse paint",
        "старое описание больше не участвует",
    )

    messages = captured["messages"]
    assert messages[1] == {"role": "user", "content": "black metal corpse paint"}
    assert "макияж" in messages[0]["content"]
    assert "стиль" in messages[0]["content"]
    assert item == "чёрно-белый блэк-метал корпспейнт"
    assert display_item == "чёрно-белый блэк-метал корпспейнт"
    assert prompt == "Добавь персонажу чёрно-белый блэк-метал корпспейнт."


def test_outfit_generation_edits_each_mood_reference(monkeypatch, tmp_path) -> None:
    generated_root = tmp_path / "generated"
    source_dir = generated_root / "source-assets"
    output_dir = generated_root / "output-assets"
    source_dir.mkdir(parents=True)
    source_bytes = _png_bytes("navy")
    for mood in ("idle", "sad", "happy"):
        (source_dir / f"teen-{mood}.png").write_bytes(source_bytes)

    calls: list[tuple[str, str, str, str | None]] = []

    @contextmanager
    def fake_reserve(prompt, source_path, *, label, size=None, provider=None):
        calls.append((prompt, source_path.name, label, size))
        yield _png_bytes("red")

    monkeypatch.setattr(outfit_service, "GENERATED_ROOT", generated_root)
    monkeypatch.setattr(outfit_service, "generated_dir_for", lambda _asset_id: output_dir)
    monkeypatch.setattr(outfit_service, "reserve_image_edit_bytes", fake_reserve)

    encoded = outfit_service.encode_outfit_generation_description(
        "Одень персонажа в футболку Аргентины.",
        idle_image_url="/static/generated/source-assets/teen-idle.png?v=1",
        sad_image_url="/static/generated/source-assets/teen-sad.png?v=1",
        happy_image_url="/static/generated/source-assets/teen-happy.png?v=1",
    )
    image_set = outfit_service.generate_outfit_image_asset_set(
        encoded,
        image_provider="openai",
        asset_set_id=uuid.uuid4(),
    )

    assert [call[1] for call in calls] == [
        "teen-idle.png",
        "teen-idle.png",
        "teen-idle.png",
    ]
    assert [call[2] for call in calls] == [
        "pet_outfit/idle_image",
        "pet_outfit/sad_image",
        "pet_outfit/happy_image",
    ]
    assert all(call[3] == outfit_service.PET_SCENE_IMAGE_SIZE for call in calls)
    assert "Одень персонажа в футболку Аргентины." in calls[0][0]
    assert all(
        "Не генерируй и не переосмысляй изменённую внешность заново" in call[0]
        for call in calls[1:]
    )
    assert "Сохрани абсолютно того же персонажа" in calls[0][0]
    assert all("Сохрани того же персонажа" in call[0] for call in calls[1:])
    assert image_set.scene_path == output_dir / "teen-idle.png"
    assert outfit_service.generated_outfit_mood_path(image_set, "sad") == (
        output_dir / "teen-sad.png"
    )
    assert outfit_service.generated_outfit_mood_path(image_set, "happy") == (
        output_dir / "teen-happy.png"
    )
    metadata = json.loads((output_dir / ".generation.json").read_text(encoding="utf-8"))
    assert metadata["mode"] == "outfit_edit_v1"
    assert metadata["outfitPipeline"] == "idle-derived-moods-v1"
    assert outfit_service.is_outfit_image_set(image_set)


def test_outfit_generation_accepts_test_pet_reference(monkeypatch, tmp_path) -> None:
    test_pet_root = tmp_path / "test-pet"
    test_pet_root.mkdir()
    source_path = test_pet_root / "openai-normal.png"
    source_path.write_bytes(_png_bytes("navy"))

    monkeypatch.setattr(outfit_service, "TEST_PET_ROOT", test_pet_root)

    resolved = outfit_service._generated_reference_path("/test-pet/openai-normal.png?v=fixture")

    assert resolved == source_path


def test_outfit_generation_repairs_rejected_prompt_twice(monkeypatch, tmp_path) -> None:
    generated_root = tmp_path / "generated"
    source_dir = generated_root / "source-assets"
    output_dir = generated_root / "output-assets"
    source_dir.mkdir(parents=True)
    (source_dir / "teen-idle.png").write_bytes(_png_bytes("navy"))

    prompts: list[str] = []

    @contextmanager
    def fake_reserve(prompt, _source_path, **_kwargs):
        prompts.append(prompt)
        if len(prompts) <= 2:
            raise RuntimeError("blocked")
        yield _png_bytes("red")

    monkeypatch.setattr(outfit_service, "GENERATED_ROOT", generated_root)
    monkeypatch.setattr(outfit_service, "generated_dir_for", lambda _asset_id: output_dir)
    monkeypatch.setattr(outfit_service, "reserve_image_edit_bytes", fake_reserve)
    monkeypatch.setattr(
        outfit_service,
        "generation_error_code",
        lambda exc: "IMAGE_PROMPT_REJECTED" if str(exc) == "blocked" else "GENERATION_FAILED",
    )
    monkeypatch.setattr(
        outfit_service,
        "_repair_outfit_prompt",
        lambda _original, _rejected, attempt: f"Одень персонажа в безопасный плащ {attempt}.",
    )

    encoded = outfit_service.encode_outfit_generation_description(
        "Одень персонажа в исходный плащ.",
        idle_image_url="/static/generated/source-assets/teen-idle.png",
        sad_image_url="/static/generated/source-assets/teen-idle.png",
        happy_image_url="/static/generated/source-assets/teen-idle.png",
    )
    outfit_service.generate_outfit_image_asset_set(
        encoded,
        image_provider="openai",
        asset_set_id=uuid.uuid4(),
    )

    assert len(prompts) == 5
    assert "исходный плащ" in prompts[0]
    assert "безопасный плащ 1" in prompts[1]
    assert "безопасный плащ 2" in prompts[2]


def test_outfit_generation_marks_two_failed_repairs_as_exhausted(monkeypatch, tmp_path) -> None:
    generated_root = tmp_path / "generated"
    source_dir = generated_root / "source-assets"
    output_dir = generated_root / "output-assets"
    source_dir.mkdir(parents=True)
    (source_dir / "teen-idle.png").write_bytes(_png_bytes("navy"))

    @contextmanager
    def fake_reserve(_prompt, _source_path, **_kwargs):
        raise RuntimeError("blocked")
        yield b""  # pragma: no cover

    monkeypatch.setattr(outfit_service, "GENERATED_ROOT", generated_root)
    monkeypatch.setattr(outfit_service, "generated_dir_for", lambda _asset_id: output_dir)
    monkeypatch.setattr(outfit_service, "reserve_image_edit_bytes", fake_reserve)
    monkeypatch.setattr(
        outfit_service,
        "generation_error_code",
        lambda _exc: "IMAGE_PROMPT_REJECTED",
    )
    monkeypatch.setattr(
        outfit_service,
        "_repair_outfit_prompt",
        lambda _original, _rejected, attempt: f"Одень персонажа в безопасный плащ {attempt}.",
    )

    encoded = outfit_service.encode_outfit_generation_description(
        "Одень персонажа в исходный плащ.",
        idle_image_url="/static/generated/source-assets/teen-idle.png",
        sad_image_url="/static/generated/source-assets/teen-idle.png",
        happy_image_url="/static/generated/source-assets/teen-idle.png",
    )

    try:
        outfit_service.generate_outfit_image_asset_set(
            encoded,
            image_provider="openai",
            asset_set_id=uuid.uuid4(),
        )
    except outfit_service.PromptRepairExhausted as exc:
        assert exc.code == "OUTFIT_PROMPT_REPAIR_EXHAUSTED"
    else:
        raise AssertionError("PromptRepairExhausted was not raised")


def _prepare_outfit_output_dir(monkeypatch, tmp_path) -> tuple[SimpleNamespace, list[str]]:
    output_dir = tmp_path / "output-assets"
    output_dir.mkdir(parents=True)
    (output_dir / "teen-idle.png").write_bytes(_png_bytes("navy"))
    (output_dir / "teen-happy.png").write_bytes(_png_bytes("green"))

    regenerated: list[str] = []

    @contextmanager
    def fake_reserve(_prompt, source_path, *, label, size=None, provider=None):
        regenerated.append(label)
        assert source_path.name == "teen-idle.png"
        yield _png_bytes("red")

    monkeypatch.setattr(outfit_service, "generated_dir_for", lambda _asset_id: output_dir)
    monkeypatch.setattr(outfit_service, "reserve_image_edit_bytes", fake_reserve)
    return SimpleNamespace(asset_set_id=uuid.uuid4()), regenerated


def test_outfit_mood_video_retry_regenerates_static_and_succeeds(monkeypatch, tmp_path) -> None:
    image_set, regenerated = _prepare_outfit_output_dir(monkeypatch, tmp_path)
    attempts: list[str] = []

    def flaky_video(_image_set, scene_path):
        attempts.append(scene_path.name)
        if len(attempts) < 2:
            raise RuntimeError("InputImageSensitiveContentDetected")
        return scene_path.with_suffix(".mp4")

    result = outfit_service.generate_outfit_mood_video_with_retry(
        image_set,
        "happy",
        flaky_video,
    )

    assert result.name == "teen-happy.mp4"
    assert len(attempts) == 2
    assert regenerated == ["pet_outfit/happy_image_retry"]


def test_outfit_mood_video_retry_raises_after_exhausting_attempts(monkeypatch, tmp_path) -> None:
    image_set, regenerated = _prepare_outfit_output_dir(monkeypatch, tmp_path)
    attempts: list[str] = []

    def always_blocked(_image_set, scene_path):
        attempts.append(scene_path.name)
        raise RuntimeError("InputImageSensitiveContentDetected")

    try:
        outfit_service.generate_outfit_mood_video_with_retry(
            image_set,
            "happy",
            always_blocked,
        )
    except RuntimeError as exc:
        assert "InputImageSensitiveContentDetected" in str(exc)
    else:
        raise AssertionError("expected the exhausted retry to raise")

    assert len(attempts) == outfit_service.OUTFIT_VIDEO_RETRY_ATTEMPTS
    assert regenerated == ["pet_outfit/happy_image_retry"] * (
        outfit_service.OUTFIT_VIDEO_RETRY_ATTEMPTS - 1
    )
