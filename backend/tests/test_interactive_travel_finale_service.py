from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.schemas import InteractiveTravelState
from app.services import interactive_travel_finale_service as finale_service


def _travel():
    return SimpleNamespace(
        travelId="interactive-travel-finale-test",
        parts=[
            SimpleNamespace(
                backgroundVideoUrl="/static/generated/interactive-travel-finale-test/part-1.mp4"
            )
        ],
    )


def _completed_travel() -> InteractiveTravelState:
    result = {
        "text": "Я справился с испытанием.",
        "adviceAssessment": "helpful",
        "reaction": "Продолжаю путь.",
        "reactionTone": "determined",
        "consequence": "Путь стал свободен.",
        "outcomeValence": "positive",
        "statImpacts": [],
    }
    return InteractiveTravelState.model_validate(
        {
            "travelId": "interactive-travel-finale-patch",
            "generatedAt": "2026-07-15T10:00:00Z",
            "destination": "облачный город",
            "overallTitle": "Путь к башне",
            "arcPlan": {"goal": "добраться до башни"},
            "parts": [
                {
                    "partNumber": part_number,
                    "title": f"Часть {part_number}",
                    "storyText": "Я иду к башне.",
                    "transition": (
                        None
                        if part_number == 1
                        else {"elapsedHours": 1, "summary": "Прошёл один час."}
                    ),
                    "challenge": "Найти безопасную дорогу.",
                    "actionSuggestions": [],
                    "answer": "Осмотреться.",
                    "result": result,
                }
                for part_number in range(1, 4)
            ],
            "completed": True,
            "outcomeValence": "positive",
        }
    )


def test_late_media_patches_saved_finale_without_lost_concurrent_update(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(finale_service, "GENERATED_ROOT", tmp_path)
    travel = _completed_travel()
    finale_service.save_interactive_travel_finale(travel, telegram_id=42, username="serge")
    image_url = (
        "/static/generated/interactive-travel-finale-patch/interactive-travel-part-03.png?v=1"
    )
    video_url = (
        "/static/generated/interactive-travel-finale-patch/interactive-travel-part-03.mp4?v=2"
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        image_patch = executor.submit(
            finale_service.patch_interactive_travel_finale_media,
            travel.travelId,
            part_number=3,
            image_url=image_url,
        )
        video_patch = executor.submit(
            finale_service.patch_interactive_travel_finale_media,
            travel.travelId,
            part_number=3,
            video_url=video_url,
        )
        assert image_patch.result(timeout=3) is True
        assert video_patch.result(timeout=3) is True

    payload = finale_service.read_interactive_travel_finale(travel.travelId)
    assert payload["owner"]["telegramId"] == 42
    assert payload["travel"]["parts"][2]["backgroundImageUrl"] == image_url
    assert payload["travel"]["parts"][2]["backgroundVideoUrl"] == video_url
    assert payload["mediaUpdatedAt"].endswith("Z")

    finale_service.save_interactive_travel_finale(travel, telegram_id=42, username="serge")
    resaved = finale_service.read_interactive_travel_finale(travel.travelId)
    assert resaved["travel"]["parts"][2]["backgroundImageUrl"] == image_url
    assert resaved["travel"]["parts"][2]["backgroundVideoUrl"] == video_url


def test_finale_patch_rejects_symlink_without_touching_target(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(finale_service, "GENERATED_ROOT", tmp_path)
    travel = _completed_travel()
    output_dir = tmp_path / travel.travelId
    output_dir.mkdir()
    outside = tmp_path / "outside-finale.json"
    original = json.dumps({"outside": True}).encode()
    outside.write_bytes(original)
    (output_dir / finale_service.FINALE_FILENAME).symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        finale_service.patch_interactive_travel_finale_media(
            travel.travelId,
            part_number=3,
            image_url="/static/generated/synthetic.png",
        )

    assert outside.read_bytes() == original


def test_finale_video_is_single_flight_and_replays_persisted_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(finale_service, "GENERATED_ROOT", tmp_path)
    provider_calls: list[int] = []

    @contextmanager
    def fake_generate(*_args, **_kwargs):
        provider_calls.append(1)
        time.sleep(0.05)
        yield b"provider-video"

    monkeypatch.setattr(finale_service, "reserve_video_from_image_bytes", fake_generate)
    monkeypatch.setattr(
        finale_service,
        "strip_generated_video_auxiliary_streams",
        lambda content: b"normalized-" + content,
    )

    def generate():
        return finale_service.generate_interactive_travel_finale_video(
            _travel(),
            prompt="A coherent journey",
            reference_base_url="https://media.example.test",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: generate(), range(2)))

    assert provider_calls == [1]
    assert results[0]["id"] == results[1]["id"]
    video_path = (
        tmp_path
        / "interactive-travel-finale-test"
        / "finale-attempts"
        / (f"{results[0]['id']}.mp4")
    )
    assert video_path.read_bytes() == b"normalized-provider-video"

    metadata_path = video_path.with_suffix(".json")
    metadata_path.unlink()
    recovered = generate()

    assert provider_calls == [1]
    assert recovered["id"] == results[0]["id"]
    assert metadata_path.is_file()
