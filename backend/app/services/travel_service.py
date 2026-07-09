from __future__ import annotations

import json
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, model_validator

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
from app.services.pet_reply_engine.speech_runtime import (
    state_param_labels,
    state_param_usage_rule,
)
from app.services.prompt_debug import (
    log_chat_completion_prompt,
    log_chat_completion_response,
    write_prompt_log_line,
)
from app.services.tone_runtime import tone_prompt_block, tone_visual_style

ADVENTURE_SCENE_COUNT = 7
TRAVEL_CARD_OUTPUT_HEIGHT = 1080
IMAGE_PROVIDER_SIZE_MULTIPLE = 16
DEFAULT_IMAGE_ASPECT_RATIO = "322:540"
TRAVEL_CHAT_MAX_ATTEMPTS = 3
TRAVEL_CHAT_RETRY_SECONDS = (3.0, 8.0)
TRAVEL_IMAGE_MAX_ATTEMPTS = 3
TRAVEL_IMAGE_RETRY_SECONDS = (60.0, 90.0)
TRAVEL_STORY_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "travel_story_templates.json"
)
TRACERY_SLOT_PATTERN = re.compile(r"#([a-zA-Z0-9_]+)#")


@dataclass(frozen=True)
class StoryFramework:
    framework_id: str
    name: str
    description: str


STORY_FRAMEWORKS: tuple[StoryFramework, ...] = (
    StoryFramework(
        "chase_the_impossible",
        "Chase the Impossible",
        (
            "The character notices something extraordinary and decides to chase it. "
            "The journey continuously becomes bigger and more surprising. "
            "The final discovery is different from what the character originally expected."
        ),
    ),
    StoryFramework(
        "expedition",
        "Expedition",
        (
            "The character discovers an unknown place. Each new location reveals something "
            "more mysterious. The story ends with solving the world's central mystery."
        ),
    ),
    StoryFramework(
        "great_event",
        "Great Event",
        (
            "The character becomes part of a massive event such as a festival, race, "
            "celebration, migration, tournament, giant machine activation, seasonal "
            "phenomenon or magical phenomenon. The character actively participates."
        ),
    ),
    StoryFramework(
        "world_transformation",
        "World Transformation",
        (
            "The rules of the world suddenly change. The character explores these new "
            "rules, discovers why the world changed, and helps complete the "
            "transformation."
        ),
    ),
    StoryFramework(
        "grand_mission",
        "Grand Mission",
        (
            "The character receives an important objective. The adventure consists of "
            "multiple increasingly exciting steps leading toward that goal."
        ),
    ),
)

TRAVEL_IMAGE_STYLE_PROMPT = """
single finished premium illustration for a character adventure, expressive non-human
mascot acting, clean readable silhouettes, tactile materials, controlled color palette,
clear foreground/midground/background, high emotional readability, no photorealism,
no 3D plastic toy look, no clutter. The active generation profile defines the setting,
surface details, lighting mood and genre flavor.
""".strip()

ADVENTURE_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "adventureTitle",
        "coreIdea",
        "world",
        "mainObjective",
        "importantCharacters",
        "importantLocations",
        "importantObjects",
        "fullStory",
    ],
    "properties": {
        "adventureTitle": {"type": "string", "minLength": 1, "maxLength": 80},
        "coreIdea": {"type": "string", "minLength": 1, "maxLength": 500},
        "world": {"type": "string", "minLength": 1, "maxLength": 700},
        "mainObjective": {"type": "string", "minLength": 1, "maxLength": 360},
        "importantCharacters": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 260},
        },
        "importantLocations": {
            "type": "array",
            "minItems": 3,
            "maxItems": 9,
            "items": {"type": "string", "minLength": 1, "maxLength": 260},
        },
        "importantObjects": {
            "type": "array",
            "minItems": 0,
            "maxItems": 3,
            "items": {"type": "string", "minLength": 1, "maxLength": 260},
        },
        "fullStory": {
            "type": "array",
            "minItems": 8,
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 900},
        },
    },
}

STORYBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["panels"],
    "properties": {
        "panels": {
            "type": "array",
            "minItems": ADVENTURE_SCENE_COUNT,
            "maxItems": ADVENTURE_SCENE_COUNT,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sceneNumber", "title", "story", "imagePrompt"],
                "properties": {
                    "sceneNumber": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": ADVENTURE_SCENE_COUNT,
                    },
                    "title": {"type": "string", "minLength": 1, "maxLength": 70},
                    "story": {"type": "string", "minLength": 1, "maxLength": 260},
                    "imagePrompt": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1800,
                    },
                },
            },
        }
    },
}

LOCAL_REFERENCE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class AdventureStory(BaseModel):
    adventureTitle: str = Field(min_length=1, max_length=80)
    coreIdea: str = Field(min_length=1, max_length=500)
    world: str = Field(min_length=1, max_length=700)
    mainObjective: str = Field(min_length=1, max_length=360)
    importantCharacters: list[str] = Field(min_length=1, max_length=8)
    importantLocations: list[str] = Field(min_length=3, max_length=9)
    importantObjects: list[str] = Field(min_length=0, max_length=3)
    fullStory: list[str] = Field(min_length=8, max_length=12)


class StoryboardPanel(BaseModel):
    sceneNumber: int = Field(ge=1, le=ADVENTURE_SCENE_COUNT)
    title: str = Field(min_length=1, max_length=70)
    story: str = Field(min_length=1, max_length=260)
    imagePrompt: str = Field(min_length=1, max_length=1800)


class Storyboard(BaseModel):
    panels: list[StoryboardPanel] = Field(
        min_length=ADVENTURE_SCENE_COUNT,
        max_length=ADVENTURE_SCENE_COUNT,
    )

    @model_validator(mode="after")
    def require_sequential_panels(self) -> Storyboard:
        scene_numbers = [panel.sceneNumber for panel in self.panels]
        expected = list(range(1, ADVENTURE_SCENE_COUNT + 1))
        if scene_numbers != expected:
            raise ValueError(f"storyboard panels must be sequential: {expected}")
        return self


class TravelPlotBriefBeat(BaseModel):
    sceneNumber: int = Field(ge=1, le=ADVENTURE_SCENE_COUNT)
    function: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=700)
    visualSeed: str = Field(min_length=1, max_length=500)
    mustInclude: list[str] = Field(default_factory=list, max_length=6)
    mustAvoid: list[str] = Field(default_factory=list, max_length=6)


class TravelPlotBrief(BaseModel):
    templateId: str = Field(min_length=1, max_length=100)
    templateKind: str = Field(min_length=1, max_length=50)
    sourceTemplateIds: list[str] = Field(default_factory=list, max_length=10)
    title: str = Field(min_length=1, max_length=160)
    logline: str = Field(min_length=1, max_length=700)
    directorialIntent: str = Field(min_length=1, max_length=700)
    selectedSlots: dict[str, str] = Field(default_factory=dict)
    beats: list[TravelPlotBriefBeat] = Field(
        min_length=ADVENTURE_SCENE_COUNT,
        max_length=ADVENTURE_SCENE_COUNT,
    )

    @model_validator(mode="after")
    def require_sequential_beats(self) -> TravelPlotBrief:
        scene_numbers = [beat.sceneNumber for beat in self.beats]
        expected = list(range(1, ADVENTURE_SCENE_COUNT + 1))
        if scene_numbers != expected:
            raise ValueError(f"plot brief beats must be sequential: {expected}")
        return self


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


def _short_list_text(value: Any, *, limit: int = 3) -> str:
    if not isinstance(value, list | tuple):
        return ""
    parts: list[str] = []
    for item in value:
        text = _string_value(item)
        if text:
            parts.append(text)
        if len(parts) == limit:
            break
    return ", ".join(parts)


def _simple_character_description(payload: GenerateTravelRequest) -> str:
    pet = payload.pet
    character_bible = pet.characterBible if isinstance(pet.characterBible, dict) else {}
    identity = (
        character_bible.get("identity") if isinstance(character_bible.get("identity"), dict) else {}
    )
    parts = [
        _string_value(character_bible.get("signature")),
        _string_value(character_bible.get("species")),
        _string_value(identity.get("one_liner")) if isinstance(identity, dict) else "",
        _stage_design_for(payload),
        _short_list_text(character_bible.get("main_colors")),
        _short_list_text(character_bible.get("signature_features")),
        _short_list_text(character_bible.get("materials")),
        _string_value(character_bible.get("proportions")),
        pet.description,
    ]
    unique_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = " ".join(part.split())
        if normalized and normalized not in seen:
            unique_parts.append(normalized)
            seen.add(normalized)
        if len(unique_parts) == 5:
            break
    return "; ".join(unique_parts) or pet.description


def _current_asset_reference_entry(payload: GenerateTravelRequest) -> tuple[str, str, str] | None:
    asset_images = payload.pet.assetImages
    if not isinstance(asset_images, dict):
        return None

    stage_images = asset_images.get(payload.pet.stage)
    if not isinstance(stage_images, dict):
        return None

    image_url = _string_value(stage_images.get(payload.pet.mood))
    if not image_url:
        return None

    return payload.pet.stage, payload.pet.mood, image_url


def _asset_reference_entries(payload: GenerateTravelRequest) -> list[tuple[str, str, str]]:
    current_reference = _current_asset_reference_entry(payload)
    return [current_reference] if current_reference else []


def _asset_reference_context(payload: GenerateTravelRequest) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for stage, mood, image_url in _asset_reference_entries(payload):
        references.append(
            {
                "stage": stage,
                "mood": mood,
                "imageUrl": image_url,
                "priority": "primary",
            }
        )
    return references


def _asset_reference_text(payload: GenerateTravelRequest) -> str:
    entries = _asset_reference_entries(payload)
    if not entries:
        return "No sprite asset URLs were provided. Follow the text visual identity exactly."

    lines = [
        "Use only this current on-screen character sprite as the character reference "
        "when the image model can inspect URLs.",
    ]
    for stage, mood, image_url in entries:
        lines.append(f"- PRIMARY CURRENT SPRITE {stage}/{mood}: {image_url}")
    return "\n".join(lines)


def _state_params_context(payload: GenerateTravelRequest) -> dict[str, str]:
    labels = state_param_labels(
        hunger=payload.pet.stats.hunger,
        happiness=payload.pet.stats.happiness,
        energy=payload.pet.stats.energy,
    )
    return {
        "usageRule": state_param_usage_rule(),
        "голод": labels["hunger"],
        "настроение": labels["happiness"],
        "здоровье": labels["energy"],
    }


def _is_public_reference_url(image_url: str) -> bool:
    if image_url.startswith("data:image/"):
        return True
    parsed = urlparse(image_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname or ""
    return hostname not in LOCAL_REFERENCE_HOSTS and not hostname.endswith(".local")


def _absolute_reference_url(image_url: str, settings: Any) -> str:
    if _is_public_reference_url(image_url):
        return image_url
    if not image_url.startswith("/"):
        return ""

    base_url = _string_value(getattr(settings, "backend_public_url", None)) or _string_value(
        getattr(settings, "webapp_url", None)
    )
    if not base_url:
        return ""

    absolute_url = f"{base_url.rstrip('/')}/{image_url.lstrip('/')}"
    return absolute_url if _is_public_reference_url(absolute_url) else ""


def _asset_input_references(payload: GenerateTravelRequest) -> list[dict[str, Any]]:
    settings = get_settings()
    references: list[dict[str, Any]] = []
    for _, _, image_url in _asset_reference_entries(payload):
        absolute_url = _absolute_reference_url(image_url, settings)
        if not absolute_url:
            continue
        references.append(
            {
                "type": "image_url",
                "image_url": {"url": absolute_url},
            }
        )
        if len(references) == 16:
            break
    return references


def _pet_context(payload: GenerateTravelRequest) -> dict[str, Any]:
    pet = payload.pet
    return {
        "name": pet.name,
        "description": pet.description,
        "stage": pet.stage,
        "mood": pet.mood,
        "params": _state_params_context(payload),
        "characterProfile": _selected_character_profile(pet.characterBible),
        "assetReferenceImages": _asset_reference_context(payload),
    }


def _story_pet_context(payload: GenerateTravelRequest) -> dict[str, Any]:
    pet = payload.pet
    current_reference = _current_asset_reference_entry(payload)
    return {
        "name": pet.name,
        "description": pet.description,
        "stage": pet.stage,
        "mood": pet.mood,
        "params": _state_params_context(payload),
        "simpleCharacterDescription": _simple_character_description(payload),
        "currentReferenceImage": current_reference[2] if current_reference else None,
    }


def _select_story_framework() -> StoryFramework:
    return random.choice(STORY_FRAMEWORKS)


def _log_framework_selection(framework: StoryFramework) -> dict[str, Any]:
    payload = {
        "event": "travel_stage",
        "promptType": "internal_selection",
        "label": "travel/story_framework",
        "selectedFramework": {
            "id": framework.framework_id,
            "name": framework.name,
            "description": framework.description,
        },
    }
    return write_prompt_log_line(payload)


def _story_reasoning_kwargs(settings: Any) -> dict[str, str]:
    return chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort)


def _is_retryable_chat_error(exc: Exception) -> bool:
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, APIConnectionError | APITimeoutError)


def _is_retryable_image_error(exc: Exception) -> bool:
    return _is_retryable_chat_error(exc)


def _chat_completion_with_retry(client: Any, request_kwargs: dict[str, Any]) -> Any:
    for attempt_index in range(TRAVEL_CHAT_MAX_ATTEMPTS):
        try:
            return client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            is_last_attempt = attempt_index == TRAVEL_CHAT_MAX_ATTEMPTS - 1
            if is_last_attempt or not _is_retryable_chat_error(exc):
                raise
            retry_delay = TRAVEL_CHAT_RETRY_SECONDS[
                min(attempt_index, len(TRAVEL_CHAT_RETRY_SECONDS) - 1)
            ]
            time.sleep(retry_delay)
    raise RuntimeError("unreachable travel chat retry state")


def _generate_image_bytes_with_retry(
    *,
    prompt: str,
    label: str,
    size: str,
    input_references: list[dict[str, object]],
) -> bytes:
    for attempt_index in range(TRAVEL_IMAGE_MAX_ATTEMPTS):
        try:
            return generate_image_bytes(
                prompt,
                label=label,
                size=size,
                input_references=input_references,
            )
        except Exception as exc:
            is_last_attempt = attempt_index == TRAVEL_IMAGE_MAX_ATTEMPTS - 1
            if is_last_attempt or not _is_retryable_image_error(exc):
                raise
            retry_delay = TRAVEL_IMAGE_RETRY_SECONDS[
                min(attempt_index, len(TRAVEL_IMAGE_RETRY_SECONDS) - 1)
            ]
            write_prompt_log_line(
                {
                    "event": "travel_image_retry",
                    "label": label,
                    "attempt": attempt_index + 1,
                    "maxAttempts": TRAVEL_IMAGE_MAX_ATTEMPTS,
                    "retryDelaySeconds": retry_delay,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                }
            )
            time.sleep(retry_delay)
    raise RuntimeError("unreachable travel image retry state")


def _framework_context(framework: StoryFramework) -> dict[str, str]:
    return {
        "id": framework.framework_id,
        "name": framework.name,
        "description": framework.description,
    }


@lru_cache(maxsize=1)
def _travel_template_catalog() -> dict[str, Any]:
    return json.loads(TRAVEL_STORY_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _adventure_templates_for_framework(framework: StoryFramework) -> list[dict[str, Any]]:
    catalog = _travel_template_catalog()
    templates = catalog.get("big_adventures", {}).get("templates", [])
    if not isinstance(templates, list):
        return []

    matching_templates = [
        template
        for template in templates
        if isinstance(template, dict) and framework.framework_id in template.get("frameworkIds", [])
    ]
    if matching_templates:
        return matching_templates
    return [template for template in templates if isinstance(template, dict)]


def _select_adventure_template(framework: StoryFramework) -> dict[str, Any]:
    templates = _adventure_templates_for_framework(framework)
    if not templates:
        raise RuntimeError("No travel adventure templates are configured")
    return random.choice(templates)


def _expand_tracery_text(
    value: str,
    *,
    slots: dict[str, Any],
    selected_slots: dict[str, str],
    depth: int = 0,
) -> str:
    if depth > 8:
        return value

    def replace_slot(match: re.Match[str]) -> str:
        slot_name = match.group(1)
        if slot_name in selected_slots:
            return selected_slots[slot_name]

        choices = slots.get(slot_name)
        if not isinstance(choices, list) or not choices:
            return match.group(0)

        selected_value = str(random.choice(choices))
        expanded_value = _expand_tracery_text(
            selected_value,
            slots=slots,
            selected_slots=selected_slots,
            depth=depth + 1,
        )
        selected_slots[slot_name] = expanded_value
        return expanded_value

    return TRACERY_SLOT_PATTERN.sub(replace_slot, value)


def _expand_template_text(
    value: Any,
    *,
    slots: dict[str, Any],
    selected_slots: dict[str, str],
) -> str:
    if not isinstance(value, str):
        return ""
    return _expand_tracery_text(value, slots=slots, selected_slots=selected_slots)


def _expand_template_text_list(
    value: Any,
    *,
    slots: dict[str, Any],
    selected_slots: dict[str, str],
) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        _expand_template_text(item, slots=slots, selected_slots=selected_slots)
        for item in value
        if isinstance(item, str)
    ]


def _build_travel_plot_brief(
    payload: GenerateTravelRequest,
    framework: StoryFramework,
    *,
    template: dict[str, Any] | None = None,
) -> TravelPlotBrief:
    catalog = _travel_template_catalog()
    slots = catalog.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}

    selected_template = template or _select_adventure_template(framework)
    selected_slots = {
        "hero": _string_value(payload.pet.name) or "персонаж",
    }

    beats: list[dict[str, Any]] = []
    for beat in selected_template.get("beats", []):
        if not isinstance(beat, dict):
            continue
        beats.append(
            {
                "sceneNumber": beat.get("sceneNumber"),
                "function": _string_value(beat.get("function")),
                "purpose": _expand_template_text(
                    beat.get("purposePattern"),
                    slots=slots,
                    selected_slots=selected_slots,
                ),
                "visualSeed": _expand_template_text(
                    beat.get("visualSeedPattern"),
                    slots=slots,
                    selected_slots=selected_slots,
                ),
                "mustInclude": _expand_template_text_list(
                    beat.get("mustInclude"),
                    slots=slots,
                    selected_slots=selected_slots,
                ),
                "mustAvoid": _expand_template_text_list(
                    beat.get("mustAvoid"),
                    slots=slots,
                    selected_slots=selected_slots,
                ),
            }
        )

    return TravelPlotBrief.model_validate(
        {
            "templateId": selected_template.get("id"),
            "templateKind": selected_template.get("kind"),
            "sourceTemplateIds": selected_template.get("sourceTemplateIds", []),
            "title": _expand_template_text(
                selected_template.get("titlePattern"),
                slots=slots,
                selected_slots=selected_slots,
            ),
            "logline": _expand_template_text(
                selected_template.get("loglinePattern"),
                slots=slots,
                selected_slots=selected_slots,
            ),
            "directorialIntent": _string_value(selected_template.get("directorialIntent")),
            "selectedSlots": selected_slots,
            "beats": beats,
        }
    )


def _plot_brief_context(plot_brief: TravelPlotBrief | None) -> dict[str, Any]:
    if plot_brief is None:
        return {}
    return plot_brief.model_dump(mode="json")


def _log_plot_brief_selection(plot_brief: TravelPlotBrief) -> dict[str, Any]:
    return write_prompt_log_line(
        {
            "event": "travel_stage",
            "promptType": "internal_selection",
            "label": "travel/plot_template",
            "plotBrief": _plot_brief_context(plot_brief),
        }
    )


def _build_adventure_story_messages(
    payload: GenerateTravelRequest,
    framework: StoryFramework,
    plot_brief: TravelPlotBrief | None = None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a senior story artist for animated short. "
                "Create one complete adventure for a non-human character. Return JSON only. "
                "All story-facing text must be in Russian. Do not create scenes, panels, "
                "storyboards, image prompts, captions, UI copy, or camera instructions.\n\n"
                f"{tone_prompt_block('travelStory')}"
            ),
        },
        {
            "role": "user",
            "content": f"""
Generate a complete adventure from this input.

USER_PROMPT:
No explicit user prompt is available yet. Derive the adventure from CHARACTER_CONTEXT_JSON.

CHARACTER_CONTEXT_JSON:
{_compact_json(_story_pet_context(payload), max_chars=4500)}

SELECTED_STORY_FRAMEWORK_JSON:
{_compact_json(_framework_context(framework))}

PLOT_TEMPLATE_BRIEF_JSON:
{_compact_json(_plot_brief_context(plot_brief), max_chars=8500)}

Required output:
- adventureTitle
- coreIdea
- world
- mainObjective
- importantCharacters
- importantLocations
- importantObjects: 0-3 plot-relevant objects, only if each directly moves the plot
- fullStory: 8-12 paragraphs

Story requirements:
- Use the selected framework as the narrative backbone, but keep one central
  problem focused from beginning to end.
- Use PLOT_TEMPLATE_BRIEF_JSON as a hidden structural scaffold. Follow its
  7 beat functions in order, but do not copy its sentences literally.
- Let the character and active generation profile define locations, actions,
  causes and turns inside the scaffold. The output must feel like an original
  authored adventure, not a filled template.
- Every fullStory paragraph must serve one or more plot-brief beats. Do not
  add a second central premise, unrelated side quest, or random one-scene lore.
- Make the story specific enough that the next storyboard stage can create
  7 distinct image prompts from it.
- The story should read like a chapter from an adventure book.
- The reader should always understand where the character is, what the character wants,
  what is preventing the character, what the character decides to do next, and why the next
  event happens.
- Every paragraph must naturally lead to the next one through cause and effect.
- Avoid introducing special objects, characters or locations unless they
  naturally follow from the active generation profile and directly move the story forward.
- Do not introduce concepts that exist only for one paragraph or one storyboard
  scene.
- Every event must either reveal new information, create a new obstacle, solve
  part of the problem, or move the character closer to the goal.
- If an event can be removed without changing the story, do not include it.
- Introduce at most one special setting rule or unusual premise. Everything else
  should be an ordinary consequence of that rule.
- Do not invent multiple special objects, mysterious characters or setting rules
  unless they are absolutely necessary.
- Keep all characters, locations and objects internally consistent and reusable
  across the plot when they appear.
- Include one memorable climax and an emotionally satisfying ending.
- The character succeeds through curiosity, creativity, courage, agility or strength.
- Never solve conflict through violence, intimidation, harm-focused props, or cruelty.
- Keep the character's species, personality, home logic, visual identity and age stage
  consistent with CHARACTER_CONTEXT_JSON.
""".strip(),
        },
    ]


def _generate_complete_story(
    payload: GenerateTravelRequest,
    framework: StoryFramework,
    plot_brief: TravelPlotBrief | None = None,
) -> tuple[AdventureStory, dict[str, Any]]:
    settings = get_settings()
    client = get_openai_client()
    request_kwargs: dict[str, Any] = {
        "model": get_chat_model(settings),
        "messages": _build_adventure_story_messages(payload, framework, plot_brief),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "adventure_complete_story",
                "schema": ADVENTURE_STORY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": settings.openai_chat_timeout_seconds,
        **_story_reasoning_kwargs(settings),
    }
    prompt_debug = log_chat_completion_prompt("travel/full_story", request_kwargs)
    completion = _chat_completion_with_retry(client, request_kwargs)
    log_chat_completion_response("travel/full_story", completion)
    content = completion.choices[0].message.content or "{}"
    return AdventureStory.model_validate(json.loads(content)), prompt_debug


def _build_storyboard_messages(
    story: AdventureStory,
    plot_brief: TravelPlotBrief | None = None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a storyboard artist. Convert one complete adventure into exactly "
                "7 visually distinct storyboard panels. Return JSON only. Russian fields "
                "are user-facing; imagePrompt must be English. Do not invent a new plot.\n\n"
                f"{tone_prompt_block('storyboard')}"
            ),
        },
        {
            "role": "user",
            "content": f"""
Convert this complete adventure into exactly 7 storyboard panels.

COMPLETE_ADVENTURE_JSON:
{story.model_dump_json(indent=2)}

PLOT_TEMPLATE_BRIEF_JSON:
{_compact_json(_plot_brief_context(plot_brief), max_chars=8500)}

Storyboard rules:
- Exactly 7 panels, sceneNumber 1 through 7.
- Panel N should visualize beat N from PLOT_TEMPLATE_BRIEF_JSON while staying
  faithful to COMPLETE_ADVENTURE_JSON.
- Each panel must show a unique visual moment.
- Every panel must move the story forward through clear cause and effect.
- Avoid repeated environments, repeated actions, standing conversations, filler travel,
  and generic walking.
- Vary scale, location, movement, reveal size, emotional temperature and composition.
- The sequence should feel like a short premium animated film, not a list of incidents.
- Panel 1 is a clear beginning.
- Panels 2-5 escalate with new discoveries.
- Panel 6 is the memorable climax.
- Panel 7 is the emotional resolution.

Panel fields:
- title: short Russian scene title.
- story: 1-2 short Russian sentences describing the visible event.
- imagePrompt: English visual prompt for this panel only. Include event, environment,
  mood, movement, foreground/background, strong silhouette, and readable composition.
  Do not include character design details; the image stage will add them.
""".strip(),
        },
    ]


def _generate_storyboard(
    story: AdventureStory,
    plot_brief: TravelPlotBrief | None = None,
) -> tuple[Storyboard, dict[str, Any]]:
    settings = get_settings()
    client = get_openai_client()
    request_kwargs: dict[str, Any] = {
        "model": get_chat_model(settings),
        "messages": _build_storyboard_messages(story, plot_brief),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "adventure_storyboard",
                "schema": STORYBOARD_SCHEMA,
                "strict": True,
            },
        },
        "timeout": settings.openai_chat_timeout_seconds,
        **_story_reasoning_kwargs(settings),
    }
    prompt_debug = log_chat_completion_prompt("travel/storyboard", request_kwargs)
    completion = _chat_completion_with_retry(client, request_kwargs)
    log_chat_completion_response("travel/storyboard", completion)
    content = completion.choices[0].message.content or "{}"
    return Storyboard.model_validate(json.loads(content)), prompt_debug


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
    visual_parts: list[str] = [
        f"Character name: {pet.name or 'unnamed character'}",
        f"Character description: {pet.description}",
        f"Simple character description: {_simple_character_description(payload)}",
        f"Life stage: {pet.stage}",
        f"Current mood reference: {pet.mood}",
    ]
    return "\n".join(visual_parts)


def _parse_image_aspect_ratio(value: str | None) -> tuple[float, str]:
    raw = _string_value(value) or DEFAULT_IMAGE_ASPECT_RATIO
    cleaned = raw.lower().replace(" ", "")
    for separator in (":", "/", "x"):
        if separator not in cleaned:
            continue
        left, right = cleaned.split(separator, maxsplit=1)
        try:
            width = float(left)
            height = float(right)
        except ValueError as exc:
            raise ValueError(f"Invalid IMAGE_ASPECT_RATIO: {raw}") from exc
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid IMAGE_ASPECT_RATIO: {raw}")
        return width / height, f"{left}:{right}"

    try:
        ratio = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid IMAGE_ASPECT_RATIO: {raw}") from exc
    if ratio <= 0:
        raise ValueError(f"Invalid IMAGE_ASPECT_RATIO: {raw}")
    return ratio, raw


def _travel_card_output_size(settings: Any | None = None) -> tuple[int, int]:
    resolved_settings = settings or get_settings()
    ratio, _ = _parse_image_aspect_ratio(
        getattr(resolved_settings, "image_aspect_ratio", DEFAULT_IMAGE_ASPECT_RATIO)
    )
    height = TRAVEL_CARD_OUTPUT_HEIGHT
    width = max(1, round(height * ratio))
    return width, height


def _provider_compatible_image_size(settings: Any | None = None) -> tuple[int, int]:
    target_width, target_height = _travel_card_output_size(settings)
    target_ratio = target_width / target_height
    min_height = max(IMAGE_PROVIDER_SIZE_MULTIPLE, target_height - 256)
    max_height = target_height + 256
    best: tuple[int, float, int, int, int] | None = None

    for height in range(
        IMAGE_PROVIDER_SIZE_MULTIPLE,
        max_height + IMAGE_PROVIDER_SIZE_MULTIPLE,
        IMAGE_PROVIDER_SIZE_MULTIPLE,
    ):
        if height < min_height:
            continue
        approximate_width = max(IMAGE_PROVIDER_SIZE_MULTIPLE, round(height * target_ratio))
        for width in (
            approximate_width - (approximate_width % IMAGE_PROVIDER_SIZE_MULTIPLE),
            approximate_width
            + (IMAGE_PROVIDER_SIZE_MULTIPLE - approximate_width % IMAGE_PROVIDER_SIZE_MULTIPLE),
        ):
            if width <= 0:
                continue
            ratio_error = abs((width / height) - target_ratio)
            dimension_error = abs(width - target_width) + abs(height - target_height)
            area_error = abs((width * height) - (target_width * target_height))
            candidate = (dimension_error, ratio_error, area_error, width, height)
            if best is None or candidate < best:
                best = candidate

    if best is None:
        raise RuntimeError("Could not derive provider-compatible travel image size")
    return best[3], best[4]


def _travel_image_size(settings: Any | None = None) -> str:
    width, height = _provider_compatible_image_size(settings)
    return f"{width}x{height}"


def _travel_aspect_ratio_label(settings: Any | None = None) -> str:
    resolved_settings = settings or get_settings()
    _, label = _parse_image_aspect_ratio(
        getattr(resolved_settings, "image_aspect_ratio", DEFAULT_IMAGE_ASPECT_RATIO)
    )
    return label


def _arc_for_scene_number(scene_number: int) -> str:
    if scene_number == 1:
        return "beginning"
    if scene_number == 2:
        return "exploration"
    if scene_number in {3, 4}:
        return "discovery"
    if scene_number in {5, 6}:
        return "reward"
    return "final"


def _travel_story_from_pipeline(story: AdventureStory, storyboard: Storyboard) -> TravelStory:
    return TravelStory.model_validate(
        {
            "title": story.adventureTitle,
            "summary": story.coreIdea,
            "scenes": [
                {
                    "index": panel.sceneNumber,
                    "arc": _arc_for_scene_number(panel.sceneNumber),
                    "title": panel.title,
                    "text": panel.story,
                    "visualBrief": panel.imagePrompt,
                }
                for panel in storyboard.panels
            ],
        }
    )


def build_travel_scene_image_prompt(
    payload: GenerateTravelRequest,
    story: TravelStory,
    scene_index: int,
) -> str:
    settings = get_settings()
    scene = story.scenes[scene_index]
    return f"""
Create one illustration for scene {scene.index} of a 7-image adventure slideshow.
The image must work as an independent render and still match the same character,
art direction, lighting, rendering style and color palette as every other scene.

ASPECT RATIO:
{_travel_aspect_ratio_label(settings)}

OUTPUT SIZE:
{_travel_image_size(settings)}

SCENE DESCRIPTION:
{scene.visualBrief}

SCENE TITLE:
{scene.title}

SCENE STORY:
{scene.text}

SHARED ART STYLE:
{tone_visual_style()}

BASE ART STYLE:
{TRAVEL_IMAGE_STYLE_PROMPT}

CHARACTER APPEARANCE TO PRESERVE EXACTLY:
{_visual_identity_text(payload)}

CHARACTER REFERENCE ASSETS:
{_asset_reference_text(payload)}

Character consistency rules:
- Base the character on the provided sprite/reference asset visuals first, then translate
  that same character into the shared illustration style.
- The character must look like the same character as the current
  {payload.pet.stage}/{payload.pet.mood}
  sprite: preserve species, silhouette, body proportions, face placement, colors,
  markings, materials, clothing, accessories and age.
- Only pose, expression and action may change.
- Do not redesign the species, swap the palette, add new dominant features,
  age up/down the character, hide the character behind props, or replace it with another creature.
- If reference URLs are inaccessible to the image model, follow CHARACTER APPEARANCE text exactly.

Composition rules:
- Fill the requested aspect ratio; no extra borders, frames, cards, UI, text, captions,
  speech bubbles, watermarks, split panels or collage layouts.
- Strong readable silhouette, clear foreground/midground/background, cinematic depth,
  dynamic movement, and instantly understandable emotional action.
- Keep the character clearly visible as the main character while showing the environment
  and the story beat.
- No violence, intimidation, harm-focused props, horror, adult themes or copyrighted characters.
""".strip()


def _normalize_travel_card_image(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.fit(
            normalized,
            _travel_card_output_size(),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        buffer = BytesIO()
        fitted.save(buffer, format="PNG")
        return buffer.getvalue()


def _generate_scene_image(
    travel_id: uuid.UUID,
    payload: GenerateTravelRequest,
    story: TravelStory,
    scene_index: int,
) -> TravelSceneImage:
    scene = story.scenes[scene_index]
    prompt = build_travel_scene_image_prompt(payload, story, scene_index)
    raw_image_bytes = _generate_image_bytes_with_retry(
        prompt=prompt,
        label=f"travel/scene_{scene.index:02d}_image",
        size=_travel_image_size(),
        input_references=_asset_input_references(payload),
    )
    image_bytes = _normalize_travel_card_image(raw_image_bytes)

    output_dir = generated_dir_for(travel_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"travel-scene-{scene.index:02d}.png"
    path.write_bytes(image_bytes)
    version = int(datetime.now(UTC).timestamp())
    return TravelSceneImage(
        sceneIndex=scene.index,
        imageUrl=f"/static/generated/{travel_id}/{path.name}?v={version}",
    )


def _generate_scene_images(
    travel_id: uuid.UUID,
    payload: GenerateTravelRequest,
    story: TravelStory,
) -> list[TravelSceneImage]:
    return [
        _generate_scene_image(travel_id, payload, story, scene_index)
        for scene_index in range(len(story.scenes))
    ]


def generate_travel(payload: GenerateTravelRequest) -> GenerateTravelResponse:
    travel_id = uuid.uuid4()
    framework = _select_story_framework()
    plot_brief = _build_travel_plot_brief(payload, framework)
    prompt_debug = [_log_framework_selection(framework), _log_plot_brief_selection(plot_brief)]
    complete_story, story_prompt_debug = _generate_complete_story(payload, framework, plot_brief)
    storyboard, storyboard_prompt_debug = _generate_storyboard(complete_story, plot_brief)
    prompt_debug.extend([story_prompt_debug, storyboard_prompt_debug])
    story = _travel_story_from_pipeline(complete_story, storyboard)
    images = _generate_scene_images(travel_id, payload, story)
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
        images=images,
        debug=debug,
    )
