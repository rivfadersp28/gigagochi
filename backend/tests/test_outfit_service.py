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
        lambda *_args, **_kwargs: SimpleNamespace(content='{"item":"футболку Аргентины"}'),
    )

    item, prompt = outfit_service.simplify_outfit_request(
        "прикольненькая милая футболка аргентины",
        "старое описание больше не участвует",
    )

    assert item == "футболку Аргентины"
    assert prompt == "Одень персонажа в футболку Аргентины."


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
        "teen-sad.png",
        "teen-happy.png",
    ]
    assert [call[2] for call in calls] == [
        "pet_outfit/idle_image",
        "pet_outfit/sad_image",
        "pet_outfit/happy_image",
    ]
    assert all(call[3] == outfit_service.PET_SCENE_IMAGE_SIZE for call in calls)
    assert all("Одень персонажа в футболку Аргентины." in call[0] for call in calls)
    assert all("Сохрани абсолютно того же персонажа" in call[0] for call in calls)
    assert image_set.scene_path == output_dir / "teen-idle.png"
    assert outfit_service.generated_outfit_mood_path(image_set, "sad") == (
        output_dir / "teen-sad.png"
    )
    assert outfit_service.generated_outfit_mood_path(image_set, "happy") == (
        output_dir / "teen-happy.png"
    )
    metadata = json.loads((output_dir / ".generation.json").read_text(encoding="utf-8"))
    assert metadata["mode"] == "outfit_edit_v1"
    assert outfit_service.is_outfit_image_set(image_set)
