from __future__ import annotations

import json
import uuid
from collections import Counter
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.image_service import (
    PET_GENERATION_METADATA_FILENAME,
    comparison_asset_set_id,
    generate_kandinsky_pet_comparison_assets,
    generate_pet_happy_scene_path,
    generate_pet_happy_video_for_image_asset_set,
    generate_pet_image_asset_set,
    generate_pet_sad_scene_path,
    generate_pet_sad_video_for_image_asset_set,
    generate_pet_video_for_image_asset_set,
)


def _reserved(fake):
    @contextmanager
    def reservation(*args, **kwargs):
        yield fake(*args, **kwargs)

    return reservation


def _png_bytes(
    color: tuple[int, int, int, int],
    size: tuple[int, int] = (720, 1280),
) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def test_completed_generation_files_survive_synthetic_process_restart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: Counter[str] = Counter()
    primary_asset_id = uuid.uuid4()
    bible = {"identity": {"name": "Искра"}, "species": "мышь"}

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda asset_id: tmp_path / str(asset_id),
    )

    def create_bible(_description: str):
        calls["character_bible"] += 1
        return bible

    def generate_sprite(_prompt: str, **_kwargs):
        calls["sprite"] += 1
        return _png_bytes((80, 120, 180, 255), (256, 256))

    def make_foreground(_sprite: bytes):
        calls["foreground_postprocess"] += 1
        return _png_bytes((80, 120, 180, 255), (256, 256))

    def generate_scene(_source_path: Path, **_kwargs):
        calls["idle_scene"] += 1
        return _png_bytes((30, 40, 50, 255))

    def generate_edit(_prompt: str, _source_path: Path, *, label: str, **_kwargs):
        calls[label] += 1
        return _png_bytes((60, 70, 80, 255), (1024, 1536))

    def generate_multi(_prompt: str, _source_paths, *, label: str, **_kwargs):
        calls[label] += 1
        return _png_bytes((70, 80, 90, 255), (1024, 1536))

    def generate_video(_source_path: Path, **kwargs):
        calls[f"video:{kwargs.get('label') or kwargs.get('provider') or 'idle'}"] += 1
        return b"synthetic-video"

    monkeypatch.setattr(
        "app.services.image_service.create_character_bible",
        create_bible,
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_single_sprite_image_bytes",
        _reserved(generate_sprite),
    )
    monkeypatch.setattr(
        "app.services.image_service.make_character_foreground_image_bytes",
        make_foreground,
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_image_bytes",
        _reserved(generate_scene),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_image_edit_bytes",
        _reserved(generate_edit),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_multi_image_edit_bytes",
        _reserved(generate_multi),
    )
    monkeypatch.setattr(
        "app.services.image_service.extract_pet_character_region_bytes",
        lambda _path: _png_bytes((40, 50, 60, 255), (240, 320)),
    )
    monkeypatch.setattr(
        "app.services.image_service.composite_pet_character_region_bytes",
        lambda _path, _payload: _png_bytes((90, 100, 110, 255)),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_video_bytes",
        _reserved(generate_video),
    )

    def run_primary_pipeline():
        image_set = generate_pet_image_asset_set(
            "синяя мышь",
            image_provider="openai",
            asset_set_id=primary_asset_id,
        )
        idle_video = generate_pet_video_for_image_asset_set(image_set)
        sad_scene = generate_pet_sad_scene_path(image_set, image_provider="openai")
        sad_video = generate_pet_sad_video_for_image_asset_set(image_set, sad_scene)
        happy_scene = generate_pet_happy_scene_path(image_set, image_provider="openai")
        happy_video = generate_pet_happy_video_for_image_asset_set(image_set, happy_scene)
        return image_set, idle_video, sad_scene, sad_video, happy_scene, happy_video

    first_primary = run_primary_pipeline()
    calls_after_first_primary = calls.copy()

    # Models a crash after media files were atomically saved but before the job row advanced.
    second_primary = run_primary_pipeline()

    assert calls == calls_after_first_primary
    assert [path for path in first_primary[1:]] == [path for path in second_primary[1:]]
    assert second_primary[0].character_bible == bible
    assert second_primary[0].generated_at == first_primary[0].generated_at

    metadata_path = tmp_path / str(primary_asset_id) / PET_GENERATION_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["characterBible"] == bible
    assert metadata["assetSetId"] == str(primary_asset_id)

    comparison_id = comparison_asset_set_id(primary_asset_id)
    first_comparison = generate_kandinsky_pet_comparison_assets(
        "синяя мышь",
        bible,
        asset_set_id=comparison_id,
    )
    calls_after_first_comparison = calls.copy()

    second_comparison = generate_kandinsky_pet_comparison_assets(
        "синяя мышь",
        bible,
        asset_set_id=comparison_id,
    )

    assert calls == calls_after_first_comparison
    assert second_comparison == first_comparison
    assert first_comparison["assetSetId"] == str(comparison_id)
    assert not list(tmp_path.rglob("*.tmp"))
