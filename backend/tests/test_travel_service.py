from __future__ import annotations

import uuid
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from app.schemas import GenerateTravelRequest, TravelStory
from app.services import travel_service


def travel_payload() -> GenerateTravelRequest:
    return GenerateTravelRequest.model_validate(
        {
            "pet": {
                "name": "Листик",
                "description": "маленький листолицый питомец",
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 90,
                    "energy": 75,
                    "cleanliness": 85,
                },
                "characterBible": {
                    "identity": {"name": "Листик"},
                    "main_colors": ["moss green", "cream"],
                    "signature_features": ["leaf-shaped face", "tiny sprout tail"],
                    "baby_design": "small leaf-faced pet with rounded cream cheeks",
                },
                "assetImages": {
                    "baby": {
                        "idle": "https://cdn.example.test/assets/baby-idle.png",
                        "happy": "https://cdn.example.test/assets/baby-happy.png",
                    },
                    "teen": {
                        "happy": "https://cdn.example.test/assets/teen-happy.png",
                    },
                },
            }
        }
    )


def travel_story() -> TravelStory:
    return TravelStory.model_validate(
        {
            "title": "Лунная ярмарка",
            "summary": "Листик нашел огонек и вернулся вдохновленным.",
            "scenes": [
                {
                    "index": index,
                    "arc": arc,
                    "title": f"Сцена {index}",
                    "text": f"Короткая теплая сцена {index}.",
                    "visualBrief": f"The pet explores scene {index} with warm wonder.",
                }
                for index, arc in [
                    (1, "beginning"),
                    (2, "exploration"),
                    (3, "discovery"),
                    (4, "discovery"),
                    (5, "reward"),
                    (6, "reward"),
                    (7, "final"),
                ]
            ],
        }
    )


def sample_png_bytes() -> bytes:
    image = Image.new("RGB", (900, 1200), (94, 131, 87))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_travel_image_prompt_includes_pet_asset_references() -> None:
    prompt = travel_service.build_travel_scene_image_prompt(
        travel_payload(),
        travel_story(),
        0,
    )

    assert "PET REFERENCE ASSETS:" in prompt
    assert "PRIMARY CURRENT SPRITE baby/happy" in prompt
    assert "https://cdn.example.test/assets/baby-happy.png" in prompt
    assert "reference sprite baby/idle" in prompt
    assert "preserve species, silhouette, body proportions" in prompt
    assert "face placement, colors" in prompt
    assert "ASPECT RATIO:" in prompt
    assert "OUTPUT SIZE:" in prompt
    assert "644x1080" in prompt
    assert prompt.index("PRIMARY CURRENT SPRITE baby/happy") < prompt.index(
        "reference sprite baby/idle"
    )


def test_asset_input_references_use_public_urls(monkeypatch) -> None:
    payload = GenerateTravelRequest.model_validate(
        {
            "pet": {
                "description": "маленький листолицый питомец",
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 90,
                    "energy": 75,
                    "cleanliness": 85,
                },
                "assetImages": {
                    "baby": {
                        "happy": "/static/generated/asset-1/baby-happy.png",
                        "idle": "http://127.0.0.1:8000/static/generated/asset-1/baby-idle.png",
                    },
                    "teen": {
                        "happy": "https://cdn.example.test/assets/teen-happy.png",
                    },
                },
            }
        }
    )
    monkeypatch.setattr(
        travel_service,
        "get_settings",
        lambda: SimpleNamespace(
            backend_public_url="https://api.example.test",
            webapp_url=None,
        ),
    )

    references = travel_service._asset_input_references(payload)

    assert references == [
        {
            "type": "image_url",
            "image_url": {
                "url": "https://api.example.test/static/generated/asset-1/baby-happy.png"
            },
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.test/assets/teen-happy.png"},
        },
    ]


def test_generate_scene_images_generates_every_story_scene(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_generate_image_bytes(
        prompt: str,
        *,
        label: str,
        size: str | None = None,
        input_references: list[dict[str, object]] | None = None,
    ) -> bytes:
        calls.append(
            {
                "prompt": prompt,
                "label": label,
                "size": size,
                "inputReferences": input_references or [],
            }
        )
        return sample_png_bytes()

    monkeypatch.setattr(travel_service, "generate_image_bytes", fake_generate_image_bytes)
    monkeypatch.setattr(
        travel_service,
        "generated_dir_for",
        lambda travel_id: tmp_path / "generated" / str(travel_id),
    )

    travel_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    images = travel_service._generate_scene_images(travel_id, travel_payload(), travel_story())

    assert [image.sceneIndex for image in images] == [1, 2, 3, 4, 5, 6, 7]
    assert [call["label"] for call in calls] == [
        "travel/scene_01_image",
        "travel/scene_02_image",
        "travel/scene_03_image",
        "travel/scene_04_image",
        "travel/scene_05_image",
        "travel/scene_06_image",
        "travel/scene_07_image",
    ]
    assert [call["size"] for call in calls] == ["644x1080"] * 7
    assert "SCENE STORY:\nКороткая теплая сцена 7." in calls[-1]["prompt"]
    assert calls[0]["inputReferences"] == [
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.test/assets/baby-happy.png"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.test/assets/baby-idle.png"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.test/assets/teen-happy.png"},
        },
    ]

    for scene_index, image in enumerate(images, start=1):
        filename = image.imageUrl.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
        assert filename == f"travel-scene-{scene_index:02d}.png"
        path = tmp_path / "generated" / str(travel_id) / filename
        assert path.exists()
        with Image.open(path) as saved_image:
            assert saved_image.size == travel_service._travel_card_output_size()


def test_travel_image_size_uses_configured_aspect_ratio() -> None:
    settings = SimpleNamespace(image_aspect_ratio="1:2")

    assert travel_service._travel_card_output_size(settings) == (540, 1080)
    assert travel_service._travel_image_size(settings) == "540x1080"
