from __future__ import annotations

import argparse
import html
import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.media.runtime import get_media_router
from app.schemas import LocalPetChatContext, LocalPetStats
from app.services.image_service import (
    PET_GENERATION_METADATA_FILENAME,
    PET_SCENE_IMAGE_SIZE,
    _atomic_write_nonempty,
    _is_valid_image_file,
    generate_pet_image_asset_set,
    generate_pet_scene_video_path,
    generated_dir_for,
    normalize_pet_scene_video_frame_bytes,
    reserve_image_edit_bytes,
)
from app.services.interactive_travel_media_service import (
    generate_interactive_travel_part_image,
    generate_interactive_travel_part_video,
)
from app.services.interactive_travel_service import generate_scheduled_interactive_episode_plan
from app.services.outfit_service import _outfit_edit_prompt

BUNDLE_ID = "provider-comparison-20260720"
BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "static" / "generated" / BUNDLE_ID
OUTFIT_REQUEST = "Одень персонажа в футболку Iron Maiden."
CHARACTERS = {
    "ice-dragon": "ледяной дракон",
    "apple-person": "человек яблоко",
    "toilet": "унитаз",
}
PROVIDER_VIDEO = {"openai": "openrouter", "kandinsky": "kandinsky"}
UUID_NAMESPACE = uuid.UUID("2dd8e2d5-9802-4903-9806-5f5bc88d25de")


def _stable_uuid(character_slug: str, provider: str, kind: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NAMESPACE, f"{BUNDLE_ID}:{character_slug}:{provider}:{kind}")


def _story_id(character_slug: str, provider: str) -> str:
    suffix = uuid.uuid5(
        UUID_NAMESPACE,
        f"{BUNDLE_ID}:{character_slug}:{provider}:story",
    ).hex
    return f"interactive-travel-{suffix}"


def _json_write(path: Path, payload: Any) -> None:
    _atomic_write_nonempty(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )


def _static_url(asset_id: str | uuid.UUID, filename: str) -> str:
    return f"/static/generated/{asset_id}/{filename}"


def _asset_record(asset_id: str | uuid.UUID, image_name: str, video_name: str) -> dict[str, str]:
    directory = generated_dir_for(asset_id)
    return {
        "imagePath": str((directory / image_name).resolve()),
        "imageUrl": _static_url(asset_id, image_name),
        "videoPath": str((directory / video_name).resolve()),
        "videoUrl": _static_url(asset_id, video_name),
    }


def _image_record(asset_id: str | uuid.UUID, image_name: str) -> dict[str, str]:
    path = generated_dir_for(asset_id) / image_name
    return {
        "imagePath": str(path.resolve()),
        "imageUrl": _static_url(asset_id, image_name),
    }


def _primary_character_bible(character_slug: str) -> dict[str, Any]:
    primary_id = _stable_uuid(character_slug, "openai", "normal")
    metadata_path = generated_dir_for(primary_id) / PET_GENERATION_METADATA_FILENAME
    if not metadata_path.is_file():
        raise RuntimeError(f"Primary OpenAI character must be generated first: {metadata_path}")
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    bible = payload.get("characterBible")
    if not isinstance(bible, dict):
        raise RuntimeError(f"Primary character bible is missing: {metadata_path}")
    return bible


def _story_plan(character_slug: str) -> dict[str, Any]:
    path = BUNDLE_ROOT / character_slug / "story-plan.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"Invalid story plan: {path}")
    state = random.getstate()
    try:
        random.seed(f"{BUNDLE_ID}:{character_slug}")
        payload = generate_scheduled_interactive_episode_plan()
    finally:
        random.setstate(state)
    _json_write(path, payload)
    return payload


def _asset_images(normal_url: str) -> dict[str, dict[str, str]]:
    moods = {
        "idle": normal_url,
        "happy": normal_url,
        "hungry": normal_url,
        "sad": normal_url,
    }
    return {stage: dict(moods) for stage in ("baby", "teen", "adult")}


def _generate_character(
    character_slug: str,
    description: str,
    provider: str,
) -> dict[str, Any]:
    video_provider = PROVIDER_VIDEO[provider]
    normal_id = _stable_uuid(character_slug, provider, "normal")
    character_bible = None if provider == "openai" else _primary_character_bible(character_slug)
    image_set = generate_pet_image_asset_set(
        description,
        image_provider=provider,
        character_bible=character_bible,
        asset_set_id=normal_id,
    )
    generate_pet_scene_video_path(
        normal_id,
        image_set.scene_path,
        provider=video_provider,
    )
    normal = _asset_record(normal_id, "teen-idle.png", "teen-idle.mp4")

    plan = _story_plan(character_slug)
    story_id = _story_id(character_slug, provider)
    pet = LocalPetChatContext(
        petId=f"comparison-{character_slug}-{provider}",
        description=description,
        stage="teen",
        mood="idle",
        stats=LocalPetStats(hunger=50, happiness=50, energy=50),
        characterBible=image_set.character_bible,
        assetImages=_asset_images(normal["imageUrl"]),
    )
    generate_interactive_travel_part_image(
        pet=pet,
        travel_id=story_id,
        destination=str(plan["destination"]),
        part_number=1,
        title=str(plan["title"]),
        story_text=str(plan["storyText"]),
    )
    generate_interactive_travel_part_video(
        travel_id=story_id,
        part_number=1,
    )
    story = {
        **_asset_record(
            story_id,
            "interactive-travel-part-01.png",
            "interactive-travel-part-01.mp4",
        ),
        "title": plan["title"],
        "storyText": plan["storyText"],
        "question": plan["question"],
        "choices": plan["choices"],
        "correctChoice": plan["correctChoice"],
        "animatedVariants": [],
    }

    outfit_id = _stable_uuid(character_slug, provider, "outfit")
    outfit_dir = generated_dir_for(outfit_id)
    outfit_image = outfit_dir / "teen-idle.png"
    if not _is_valid_image_file(outfit_image):
        prompt = _outfit_edit_prompt(OUTFIT_REQUEST)
        with reserve_image_edit_bytes(
            prompt,
            image_set.scene_path,
            label="pet_outfit/idle_image",
            size=PET_SCENE_IMAGE_SIZE,
            provider=provider,
        ) as image_bytes:
            _atomic_write_nonempty(
                outfit_image,
                normalize_pet_scene_video_frame_bytes(image_bytes),
            )
    outfit = {
        **_image_record(outfit_id, "teen-idle.png"),
        "request": OUTFIT_REQUEST,
    }

    return {
        "character": description,
        "provider": provider,
        "imageModel": "gpt-image-2" if provider == "openai" else "kandinsky",
        "videoModel": "bytedance/seedance-2.0" if provider == "openai" else "kandinsky",
        "normal": normal,
        "interactiveStoryStart": story,
        "outfit": outfit,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=tuple(PROVIDER_VIDEO))
    parser.add_argument("--character", choices=tuple(CHARACTERS))
    parser.add_argument("--index-only", action="store_true")
    return parser.parse_args()


def _build_index() -> None:
    comparisons: dict[str, dict[str, Any]] = {}
    for character_slug in CHARACTERS:
        comparisons[character_slug] = {}
        for provider in PROVIDER_VIDEO:
            path = BUNDLE_ROOT / character_slug / f"{provider}.json"
            if not path.is_file():
                raise RuntimeError(f"Comparison result is missing: {path}")
            comparisons[character_slug][provider] = json.loads(path.read_text(encoding="utf-8"))

    manifest = {
        "bundleId": BUNDLE_ID,
        "outfitRequest": OUTFIT_REQUEST,
        "interactiveStoryAnimation": "start-only",
        "comparisons": comparisons,
    }
    _json_write(BUNDLE_ROOT / "manifest.json", manifest)

    sections: list[str] = []
    for character_slug, description in CHARACTERS.items():
        cards: list[str] = []
        for provider in PROVIDER_VIDEO:
            item = comparisons[character_slug][provider]
            normal = item["normal"]
            story = item["interactiveStoryStart"]
            outfit = item["outfit"]
            label = "GPT Image 2 + Seedance 2.0" if provider == "openai" else "Kandinsky"
            cards.append(
                f"""
                <article>
                  <h3>{html.escape(label)}</h3>
                  <h4>Normal · animation</h4>
                  <video controls loop muted playsinline poster="{html.escape(normal["imageUrl"])}">
                    <source src="{html.escape(normal["videoUrl"])}" type="video/mp4">
                  </video>
                  <h4>Interactive story · start only</h4>
                  <video controls loop muted playsinline poster="{html.escape(story["imageUrl"])}">
                    <source src="{html.escape(story["videoUrl"])}" type="video/mp4">
                  </video>
                  <p>{html.escape(str(story["storyText"]))}</p>
                  <h4>Outfit · Iron Maiden T-shirt</h4>
                  <img src="{html.escape(outfit["imageUrl"])}" alt="">
                </article>
                """
            )
        sections.append(
            f"<section><h2>{html.escape(description)}</h2>"
            f"<div class=grid>{''.join(cards)}</div></section>"
        )

    document = f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Provider visual comparison</title>
<style>
  :root {{ color-scheme: dark; font-family: system-ui, sans-serif; background: #111; color: #eee; }}
  body {{ margin: 0 auto; max-width: 1200px; padding: 24px; }}
  h1, h2, h3, h4 {{ margin: 0 0 12px; }} h2 {{ margin-top: 40px; text-transform: capitalize; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }}
  article {{ background: #1b1b1b; border: 1px solid #333; border-radius: 16px; padding: 16px; }}
  video, img {{ display: block; width: 100%; max-height: 620px; object-fit: contain;
    background: #090909; border-radius: 10px; margin-bottom: 18px; }}
  p {{ color: #bbb; line-height: 1.45; }}
  @media (max-width: 760px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
<body><h1>GPT Image 2 + Seedance vs Kandinsky</h1>{"".join(sections)}</body></html>"""
    _atomic_write_nonempty(BUNDLE_ROOT / "index.html", document.encode("utf-8"))


def main() -> None:
    args = _parse_args()
    if args.index_only:
        _build_index()
        print((BUNDLE_ROOT / "index.html").resolve())
        return
    if not args.provider or not args.character:
        raise RuntimeError("--provider and --character are required unless --index-only is set")
    active_profile = get_media_router().profile_name
    if active_profile != args.provider:
        raise RuntimeError(f"MEDIA_PROFILE must be {args.provider!r}, got {active_profile!r}")
    settings = get_settings()
    if settings.openai_image_model != "gpt-image-2":
        raise RuntimeError(
            f"OPENAI_IMAGE_MODEL must be gpt-image-2, got {settings.openai_image_model}"
        )

    result = _generate_character(
        args.character,
        CHARACTERS[args.character],
        args.provider,
    )
    result["generatedAt"] = datetime.now(UTC).isoformat()
    result["bundleId"] = BUNDLE_ID
    result_path = BUNDLE_ROOT / args.character / f"{args.provider}.json"
    _json_write(result_path, result)
    print(result_path.resolve())


if __name__ == "__main__":
    main()
