from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps

from app.config import get_settings
from app.schemas import (
    GenerateTravelRequest,
    GenerateTravelResponse,
    LocalChatDebug,
    TravelSceneImage,
    TravelStory,
)
from app.services.image_service import generate_image_bytes, generated_dir_for
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
    get_openai_client,
)
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response

TRAVEL_STORY_STRUCTURE = """
# Story Structure
Generate 5-7 scenes. Follow this narrative arc:

## 1. Beginning
The pet discovers something unusual that sparks its curiosity. It decides to go on an adventure.

## 2. Exploration
The pet enters a new place and experiences its first wonder.
Introduce the world through the pet's emotions.

## 3-4. Discovery
The pet explores, meets friendly creatures, finds magical places or interesting objects.
Optionally introduce one small playful challenge that is solved through curiosity,
kindness or creativity.

## 5-6. Reward
The pet experiences the emotional highlight of the journey. Examples:
- finding a magical object
- making a new friend
- discovering a hidden place
- helping someone
- learning something surprising

## Final Scene
The pet returns home (or finishes the journey) feeling happier, wiser or inspired.
End with a warm emotional moment that naturally suggests future adventures.
""".strip()

TRAVEL_IMAGE_STYLE_PROMPT = """
mid-century children's book illustration meets contemporary layered paper diorama,
visible cut-paper edges, soft shadows between layers, muted moss green, pumpkin orange,
cream, and ink-blue palette. First glance: a cozy glowing market silhouette.
Second glance: many small vendor stories. Third glance: handmade paper texture,
tiny signage, and playful animal gestures. No photorealism, no 3D plastic look,
no cluttered unreadable faces.
""".strip()

TRAVEL_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "scenes"],
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "summary": {"type": "string", "maxLength": 260},
        "scenes": {
            "type": "array",
            "minItems": 5,
            "maxItems": 7,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "arc", "title", "text", "visualBrief"],
                "properties": {
                    "index": {"type": "integer", "minimum": 1, "maximum": 7},
                    "arc": {
                        "type": "string",
                        "enum": [
                            "beginning",
                            "exploration",
                            "discovery",
                            "reward",
                            "final",
                        ],
                    },
                    "title": {"type": "string", "maxLength": 70},
                    "text": {"type": "string", "maxLength": 260},
                    "visualBrief": {"type": "string", "maxLength": 900},
                },
            },
        },
    },
}

TRAVEL_CARD_OUTPUT_SIZE = (644, 1080)


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _compact_json(value: Any, *, max_chars: int = 5000) -> str:
    if value is None:
        return "{}"
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _selected_character_profile(character_bible: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(character_bible, dict):
        return {}

    keys = (
        "identity",
        "species",
        "signature",
        "visual",
        "main_colors",
        "signature_features",
        "materials",
        "proportions",
        "baby_design",
        "teen_design",
        "adult_design",
        "world",
        "lore",
        "inner_state",
        "opening_scenes",
    )
    return {key: character_bible[key] for key in keys if key in character_bible}


def _pet_context(payload: GenerateTravelRequest) -> dict[str, Any]:
    pet = payload.pet
    return {
        "name": pet.name,
        "description": pet.description,
        "stage": pet.stage,
        "mood": pet.mood,
        "stats": pet.stats.model_dump(),
        "characterProfile": _selected_character_profile(pet.characterBible),
    }


def _build_story_messages(payload: GenerateTravelRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You generate compact, warm adventure storyboards for a non-human virtual pet. "
                "Return JSON only. User-facing text must be in Russian. visualBrief must be "
                "in English and concrete enough for an image model."
            ),
        },
        {
            "role": "user",
            "content": f"""
Create one travel storyboard for this pet.

PET_CONTEXT_JSON:
{_compact_json(_pet_context(payload))}

NARRATIVE_TEMPLATE:
{TRAVEL_STORY_STRUCTURE}

Rules:
- Generate 5-7 scenes total.
- Keep scene indexes sequential starting from 1.
- Scene text is shown in a mobile pet app: 1-2 short Russian sentences, warm and concrete.
- Do not mention prompts, image generation, cameras, panels, or UI.
- Keep the pet visually and emotionally consistent with PET_CONTEXT_JSON.
- Avoid danger, fear, violence, adult themes, and copyrighted characters.
- visualBrief should describe what should be visible in the image, not the prose.
""".strip(),
        },
    ]


def _travel_reasoning_kwargs(settings: Any) -> dict[str, str]:
    return chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort)


def _generate_story(payload: GenerateTravelRequest) -> tuple[TravelStory, list[dict[str, Any]]]:
    settings = get_settings()
    client = get_openai_client()
    model = get_chat_model(settings)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": _build_story_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "travel_story",
                "schema": TRAVEL_STORY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": settings.openai_chat_timeout_seconds,
        **_travel_reasoning_kwargs(settings),
    }
    prompt_debug = [log_chat_completion_prompt("travel/story", request_kwargs)]
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("travel/story", completion)
    content = completion.choices[0].message.content or "{}"
    return TravelStory.model_validate(json.loads(content)), prompt_debug


def _stage_design_for(payload: GenerateTravelRequest) -> str:
    character_bible = (
        payload.pet.characterBible if isinstance(payload.pet.characterBible, dict) else {}
    )
    stage_key = f"{payload.pet.stage}_design"
    value = _string_value(character_bible.get(stage_key))
    if value:
        return value

    visual = character_bible.get("visual")
    if isinstance(visual, dict):
        growth_forms = visual.get("growth_forms")
        if isinstance(growth_forms, dict):
            return _string_value(growth_forms.get(payload.pet.stage))
    return ""


def _visual_identity_text(payload: GenerateTravelRequest) -> str:
    pet = payload.pet
    character_bible = pet.characterBible if isinstance(pet.characterBible, dict) else {}
    visual_parts: list[str] = [
        f"Pet name: {pet.name or 'unnamed pet'}",
        f"Pet description: {pet.description}",
        f"Life stage: {pet.stage}",
    ]

    stage_design = _stage_design_for(payload)
    if stage_design:
        visual_parts.append(f"Current stage design: {stage_design}")

    for key in ("main_colors", "signature_features", "materials", "proportions"):
        value = character_bible.get(key)
        if value:
            visual_parts.append(f"{key}: {_compact_json(value, max_chars=800)}")

    identity = character_bible.get("identity")
    if identity:
        visual_parts.append(f"identity: {_compact_json(identity, max_chars=800)}")

    return "\n".join(visual_parts)


def build_travel_scene_image_prompt(
    payload: GenerateTravelRequest,
    story: TravelStory,
    scene_index: int,
) -> str:
    scene = story.scenes[scene_index]
    return f"""
Create one vertical story card illustration for a virtual pet travel scene.

STYLE:
{TRAVEL_IMAGE_STYLE_PROMPT}

PET VISUAL IDENTITY:
{_visual_identity_text(payload)}

STORY TITLE:
{story.title}

SCENE {scene.index}: {scene.title}
{scene.visualBrief}

Composition rules:
- Tall portrait card composition, important subject centered with generous safe margins.
- Show the pet clearly as the main character, keeping its visual identity consistent.
- One finished illustration, no frames, no borders, no captions, no UI, no speech bubbles.
- Keep faces readable and simple; small background details are allowed but must not clutter.
""".strip()


def _normalize_travel_card_image(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.fit(
            normalized,
            TRAVEL_CARD_OUTPUT_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        buffer = BytesIO()
        fitted.save(buffer, format="PNG")
        return buffer.getvalue()


def _generate_first_scene_image(
    travel_id: uuid.UUID,
    payload: GenerateTravelRequest,
    story: TravelStory,
) -> TravelSceneImage:
    prompt = build_travel_scene_image_prompt(payload, story, 0)
    raw_image_bytes = generate_image_bytes(prompt, label="travel/scene_01_image")
    image_bytes = _normalize_travel_card_image(raw_image_bytes)

    output_dir = generated_dir_for(travel_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "travel-scene-01.png"
    path.write_bytes(image_bytes)
    version = int(datetime.now(UTC).timestamp())
    return TravelSceneImage(
        sceneIndex=story.scenes[0].index,
        imageUrl=f"/static/generated/{travel_id}/{path.name}?v={version}",
    )


def generate_travel(payload: GenerateTravelRequest) -> GenerateTravelResponse:
    travel_id = uuid.uuid4()
    story, prompt_debug = _generate_story(payload)
    first_image = _generate_first_scene_image(travel_id, payload, story)
    debug = (
        LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
        )
        if payload.includeDebug
        else None
    )

    return GenerateTravelResponse(
        travelId=str(travel_id),
        generatedAt=datetime.now(UTC),
        story=story,
        images=[first_image],
        debug=debug,
    )
