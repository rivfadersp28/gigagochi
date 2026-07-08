from __future__ import annotations

import base64
import json
import logging
import math
import re
import subprocess
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)
from PIL import Image, ImageFilter

from app.config import get_settings
from app.prompts.pet_image_prompts import (
    build_character_bible_prompt,
    build_pet_state_strip_prompt,
    build_pet_state_strip_safety_retry_prompt,
    create_lore_seed,
)
from app.prompts.style_direction import CHARACTER_BIBLE_STYLE_DIRECTION
from app.prompts.world_description_anchors import (
    WorldDescriptionAnchor,
    format_world_description_anchors_for_prompt,
    select_world_description_anchors,
)
from app.services.character_bible_template import (
    character_bible_legacy_defaults,
    character_bible_schema,
    character_bible_system_prompt,
)
from app.services.character_cards import upgrade_character_bible_v2
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_character_model,
    get_image_model,
    get_openai_client,
    get_openrouter_api_key,
    get_openrouter_headers,
    get_openrouter_image_model,
    get_openrouter_image_url,
    is_openrouter_provider,
)
from app.services.prompt_debug import (
    log_chat_completion_prompt,
    log_chat_completion_response,
    log_image_generation_prompt,
    log_image_generation_response,
)
from app.services.tone_runtime import tone_prompt_block

logger = logging.getLogger(__name__)


class MissingKandinskyAPIKey(RuntimeError):
    pass


class KandinskyTaskError(RuntimeError):
    pass


KANDINSKY_HTTP_MAX_ATTEMPTS = 2
KANDINSKY_HTTP_RETRY_SECONDS = (3.0,)
BACKGROUND_REMOVAL_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "remove_background.mjs"
)

PLANT_DESCRIPTION_PATTERN = re.compile(
    r"(?:лист|растен|цвет|гриб|мох|сад|теплиц|оранжер|росток|кактус|трава|дерев)",
    re.IGNORECASE,
)
OVERUSED_PLANT_DEFAULT_PATTERN = re.compile(
    r"(?:мох|мохов|теплиц|оранжер|подоконник|роса|росин|тепл\w*\s+ламп|"
    r"ламп\w*\s+гре|полк\w*)",
    re.IGNORECASE,
)
INCOHERENT_LORE_PATTERN = re.compile(
    r"(?:пар\w*(?:\W+\w+){0,8}\W+громк\w*|громк\w*(?:\W+\w+){0,8}\W+пар\w*|"
    r"пар\w*(?:\W+\w+){0,8}\W+шумн\w*|"
    r"свет\w*(?:\W+\w+){0,8}\W+слуша\w*|"
    r"тень\w*(?:\W+\w+){0,8}\W+вкус\w*|"
    r"цвет\w*(?:\W+\w+){0,8}\W+уста\w*)",
    re.IGNORECASE,
)
WEAK_LIFE_LESSON_PATTERN = re.compile(
    r"(?:коротк\w*\s+просьб|добры\w*\s+слов|урок\w*\s+жизн|"
    r"важно\s+быть|правил\w*\s+жизн|норм[аы]\b|морал\w*|"
    r"учит\w*\s+(?:меня|его|её|нас)|быть\s+собой)",
    re.IGNORECASE,
)

CHARACTER_BIBLE_SCHEMA: dict[str, Any] = character_bible_schema()
OPENROUTER_SEEDREAM_IMAGE_RESOLUTION = "4K"
OPENROUTER_IMAGE_ASPECT_RATIOS = {
    "1:1",
    "1:2",
    "2:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "9:19.5",
    "19.5:9",
    "9:20",
    "20:9",
    "9:21",
    "21:9",
    "auto",
}


def _collect_character_bible_text(value: Any) -> str:
    parts: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                if key == "world_description_anchors_used":
                    continue
                collect(child)

    collect(value)
    return " ".join(parts)


def character_bible_quality_issues(
    description: str,
    character_bible: dict[str, Any],
) -> tuple[str, ...]:
    text = _collect_character_bible_text(character_bible)
    issues: list[str] = []
    if not PLANT_DESCRIPTION_PATTERN.search(description) and OVERUSED_PLANT_DEFAULT_PATTERN.search(
        text
    ):
        issues.append("non_plant_pet_uses_greenhouse_shelf_moss_dew_or_warm_lamp_defaults")
    if INCOHERENT_LORE_PATTERN.search(text):
        issues.append("incoherent_physical_or_sensory_logic")
    if WEAK_LIFE_LESSON_PATTERN.search(text):
        issues.append("generic_life_lesson_or_user_behavior_preference")
    return tuple(issues)


def _character_bible_completion(
    client: Any,
    settings: Any,
    label: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    timeout = getattr(
        settings,
        "openai_character_timeout_seconds",
        settings.openai_chat_timeout_seconds,
    )
    model = get_character_model(settings)
    request_kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "character_bible",
                "schema": character_bible_schema(),
                "strict": True,
            },
        },
        "timeout": timeout,
        **_character_reasoning_effort_kwargs(settings, model),
    }
    log_chat_completion_prompt(label, request_kwargs)
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response(label, completion)
    content = completion.choices[0].message.content or "{}"
    return json.loads(content)


def _character_reasoning_effort_kwargs(settings: Any, model: str) -> dict[str, str]:
    model_name = model.rsplit("/", 1)[-1].lower()
    if not model_name.startswith(("gpt-5", "o1", "o3", "o4")):
        return {}
    return chat_reasoning_effort_kwargs(
        getattr(
            settings,
            "openai_character_reasoning_effort",
            getattr(settings, "openai_chat_reasoning_effort", None),
        )
    )


def _repair_character_bible_prompt(
    description: str,
    character_bible: dict[str, Any],
    issues: tuple[str, ...],
    lore_seed: dict[str, str] | None = None,
    world_description_anchors: str | None = None,
) -> str:
    lore_seed_text = (
        "\nLORE_VARIATION_SEED_USED:\n" + json.dumps(lore_seed, ensure_ascii=False, indent=2)
        if lore_seed
        else ""
    )
    return f"""
Repair this character bible. Return the full corrected JSON only.

USER_CHARACTER_DESCRIPTION:
{description}
{lore_seed_text}

WORLD_DESCRIPTION_ANCHORS_USED:
{world_description_anchors or "нет"}

CHARACTER_BIBLE_STYLE_DIRECTION:
{CHARACTER_BIBLE_STYLE_DIRECTION}

{tone_prompt_block("characterBible")}

CREATURE_DESCRIPTION_STYLE_GUIDE:
Use the same clean creature-description logic as the original prompt: physical anchor,
mechanism, behavior trigger, habitat, want/conflict, and voice.

QUALITY_ISSUES:
{", ".join(issues)}

Repair rules:
- Keep the repaired lore, personality, voice, openings, sample replies, and lorebook entries
  aligned with CHARACTER_BIBLE_STYLE_DIRECTION.
- Preserve the same visual identity and all required schema fields.
- Preserve Character Profile V2 fields: identity, voice, inner_state, world, dialogue_moves,
  openings, provenance, extensions, and schema_version=2.
- Keep 8-12 voice.sample_replies, 5-8 lorebook entries, and 3-5 dialogue_moves.
- Preserve the transformed habitat logic from WORLD_DESCRIPTION_ANCHORS_USED when repairing
  world, home, origin, routines, objects, sensory details, and story seeds. Do not copy anchor
  text verbatim.
- Remove fake-life phrases like "короткие просьбы", "добрые слова", "урок", "норма",
  "важно быть", or "быть собой". Replace them with concrete pet-owned objects, places,
  habits, dislikes, and contradictions from the creature's body, element, habitat, and needs.
- If the pet is not plant/garden/window/shelf-based, remove greenhouse, shelf, moss, dew,
  warm-lamp, seed, and tiny-garden defaults from lore.
- Replace generic cozy-corner lore with a compact habitat that follows the pet's own premise.
- Remove random bureaus, boxes, labels, maps, travel cases, schools, guilds, workshops, relatives,
  neighbors, jobs, and object societies unless the user description explicitly asks for them.
- Fix physical nonsense. Steam can hiss, warm, curl, fog, or tickle; steam itself is not loud.
  If something makes sound, name the valve, kettle, vent, bell, shell, gear, or creature doing it.
- Keep world, home, origin, relationships, and inner_life connected by clear cause and effect.
- If one ability, field, glow, charge, flame, frost, shell, or body mechanism appears in too many
  fields, keep it as the core mechanism but replace repeated mentions with routines, sensory
  details, small wants, comfort actions, relationship behavior, flaws, or open story seeds.
- Ensure voice.sample_replies do not all mention the same ability; most should express character
  through emotion, relationship, observation, opinion, rhythm, or a tiny compatible invention.

CURRENT_CHARACTER_BIBLE:
{json.dumps(character_bible, ensure_ascii=False, indent=2)}
""".strip()


STAGE_ROWS = ("baby", "teen", "adult")
STATE_COLUMNS = ("idle", "happy", "sad", "hungry")
FAST_GENERATION_STAGE = "teen"
FAST_GENERATION_STATES = ("idle", "happy", "sad")
FAST_GENERATION_SKINS = tuple((FAST_GENERATION_STAGE, state) for state in FAST_GENERATION_STATES)
FAST_GENERATION_STATE_FALLBACKS = {
    "idle": ("teen", "idle"),
    "happy": ("teen", "happy"),
    "sad": ("teen", "sad"),
    "hungry": ("teen", "sad"),
}
SPRITE_FOREGROUND_DISTANCE = 28
SPRITE_COMPONENT_DILATION_PX = 25
SPRITE_SEARCH_PADDING_X_RATIO = 0.2
SPRITE_SEARCH_PADDING_Y_RATIO = 0.45
SPRITE_CONTENT_PADDING_RATIO = 0.025
SPRITE_BOTTOM_PADDING_RATIO = 0.08


def is_sprite_foreground(pixel: tuple[int, int, int, int]) -> bool:
    r, g, b, alpha = pixel
    if alpha <= 16:
        return False

    distance_from_white = ((255 - r) ** 2 + (255 - g) ** 2 + (255 - b) ** 2) ** 0.5
    return distance_from_white >= SPRITE_FOREGROUND_DISTANCE


def background_pixel_for(image: Image.Image) -> tuple[int, int, int, int]:
    corners = (
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    )
    if any(pixel[3] < 255 for pixel in corners):
        return (255, 255, 255, 0)
    return corners[0]


def foreground_component_bbox(
    image: Image.Image,
    cell_box: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    cell_left, cell_top, cell_right, cell_bottom = cell_box
    cell_width = cell_right - cell_left
    cell_height = cell_bottom - cell_top
    search_pad_x = round(cell_width * SPRITE_SEARCH_PADDING_X_RATIO)
    search_pad_y = round(cell_height * SPRITE_SEARCH_PADDING_Y_RATIO)
    search_left = max(0, cell_left - search_pad_x)
    search_top = max(0, cell_top - search_pad_y)
    search_right = min(image.width, cell_right + search_pad_x)
    search_bottom = min(image.height, cell_bottom + search_pad_y)
    search = image.crop((search_left, search_top, search_right, search_bottom))
    mask = Image.new("L", search.size, 0)
    mask_pixels = mask.load()
    search_pixels = search.load()

    for y in range(search.height):
        for x in range(search.width):
            if is_sprite_foreground(search_pixels[x, y]):
                mask_pixels[x, y] = 255

    dilation_size = SPRITE_COMPONENT_DILATION_PX
    if dilation_size % 2 == 0:
        dilation_size += 1
    dilated = mask.filter(ImageFilter.MaxFilter(dilation_size))
    dilated_data = dilated.tobytes()
    original_data = mask.tobytes()
    visited = bytearray(len(dilated_data))
    width, height = search.size
    best_bbox: tuple[int, int, int, int] | None = None
    best_score = -1.0
    cell_center_x = (cell_left + cell_right) / 2
    cell_center_y = (cell_top + cell_bottom) / 2

    for start_index, value in enumerate(dilated_data):
        if not value or visited[start_index]:
            continue

        queue: deque[int] = deque([start_index])
        visited[start_index] = 1
        original_area = 0
        original_overlap = 0
        min_x = image.width
        min_y = image.height
        max_x = -1
        max_y = -1

        while queue:
            index = queue.popleft()
            x = index % width
            y = index // width

            if original_data[index]:
                global_x = search_left + x
                global_y = search_top + y
                original_area += 1
                if cell_left <= global_x < cell_right and cell_top <= global_y < cell_bottom:
                    original_overlap += 1
                min_x = min(min_x, global_x)
                min_y = min(min_y, global_y)
                max_x = max(max_x, global_x + 1)
                max_y = max(max_y, global_y + 1)

            if x > 0:
                neighbor = index - 1
                if dilated_data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if x < width - 1:
                neighbor = index + 1
                if dilated_data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if y > 0:
                neighbor = index - width
                if dilated_data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if y < height - 1:
                neighbor = index + width
                if dilated_data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)

        if original_area == 0 or original_overlap == 0:
            continue

        component_center_x = (min_x + max_x) / 2
        component_center_y = (min_y + max_y) / 2
        normalized_distance = (
            abs(component_center_x - cell_center_x) / cell_width
            + abs(component_center_y - cell_center_y) / cell_height
        )
        score = original_overlap * 10 + original_area - normalized_distance * original_area
        if score > best_score:
            best_score = score
            best_bbox = (min_x, min_y, max_x, max_y)

    return best_bbox


def normalize_sprite_cell(
    image: Image.Image,
    content_bbox: tuple[int, int, int, int],
    output_size: tuple[int, int],
    background_pixel: tuple[int, int, int, int],
) -> Image.Image:
    output_width, output_height = output_size
    content_padding = max(2, round(min(output_size) * SPRITE_CONTENT_PADDING_RATIO))
    bottom_padding = max(2, round(output_height * SPRITE_BOTTOM_PADDING_RATIO))
    left, top, right, bottom = content_bbox
    source_box = (
        max(0, left - content_padding),
        max(0, top - content_padding),
        min(image.width, right + content_padding),
        min(image.height, bottom + content_padding),
    )
    sprite = image.crop(source_box)
    max_sprite_width = output_width - content_padding * 2
    max_sprite_height = output_height - bottom_padding - content_padding
    scale = min(1.0, max_sprite_width / sprite.width, max_sprite_height / sprite.height)

    if scale < 1:
        sprite = sprite.resize(
            (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
            Image.Resampling.LANCZOS,
        )

    canvas = Image.new("RGBA", output_size, background_pixel)
    x = round((output_width - sprite.width) / 2)
    y = max(content_padding, output_height - bottom_padding - sprite.height)
    canvas.alpha_composite(sprite, (x, y))
    return canvas


def extract_sprite_cells(image: Image.Image) -> dict[tuple[str, str], Image.Image]:
    normalized = image.convert("RGBA")
    cell_width = normalized.width // len(STATE_COLUMNS)
    cell_height = normalized.height // len(STAGE_ROWS)
    cell_images: dict[tuple[str, str], Image.Image] = {}
    background_pixel = background_pixel_for(normalized)

    for row, stage in enumerate(STAGE_ROWS):
        for col, state in enumerate(STATE_COLUMNS):
            left = col * cell_width
            top = row * cell_height
            cell_box = (left, top, left + cell_width, top + cell_height)
            content_bbox = foreground_component_bbox(normalized, cell_box)
            if content_bbox is None:
                crop = normalized.crop(cell_box)
            else:
                crop = normalize_sprite_cell(
                    normalized,
                    content_bbox,
                    (cell_width, cell_height),
                    background_pixel,
                )
            cell_images[(stage, state)] = crop

    return cell_images


def extract_state_strip_cells(
    image: Image.Image,
    *,
    stage: str = FAST_GENERATION_STAGE,
) -> dict[tuple[str, str], Image.Image]:
    normalized = image.convert("RGBA")
    cell_width = normalized.width // len(FAST_GENERATION_STATES)
    cell_height = normalized.height
    output_side = min(cell_width, cell_height)
    cell_images: dict[tuple[str, str], Image.Image] = {}
    background_pixel = background_pixel_for(normalized)

    for col, state in enumerate(FAST_GENERATION_STATES):
        left = col * cell_width
        right = normalized.width if col == len(FAST_GENERATION_STATES) - 1 else left + cell_width
        cell_box = (left, 0, right, cell_height)
        content_bbox = foreground_component_bbox(normalized, cell_box)
        if content_bbox is None:
            crop = normalized.crop(cell_box)
            if crop.size != (output_side, output_side):
                crop = crop.resize((output_side, output_side), Image.Resampling.LANCZOS)
        else:
            crop = normalize_sprite_cell(
                normalized,
                content_bbox,
                (output_side, output_side),
                background_pixel,
            )
        cell_images[(stage, state)] = crop

    return cell_images


def _attach_world_anchor_trace(
    character_bible: dict[str, Any],
    anchors: tuple[WorldDescriptionAnchor, ...],
) -> dict[str, Any]:
    extensions = character_bible.get("extensions")
    if not isinstance(extensions, dict):
        extensions = {}
    extensions["world_description_anchors_used"] = [anchor.debug_dict() for anchor in anchors[:8]]
    return {**character_bible, "extensions": extensions}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        text = _string_value(value)
        return [text] if text else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _string_value(item)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _lorebook_entries(value: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        data = _dict_value(item)
        keys = _string_list(data.get("keys"), limit=6)
        content = _string_value(data.get("content"))
        if not keys or not content:
            continue
        result.append(
            {
                "keys": keys,
                "content": content,
                "priority": int(data.get("priority") or 0),
                "constant": bool(data.get("constant") or False),
                "selective": bool(data.get("selective", True)),
            }
        )
        if len(result) >= limit:
            break
    return result


def expand_compact_character_bible(
    character_bible: dict[str, Any],
    *,
    raw_description: str,
) -> dict[str, Any]:
    """Populate legacy fields from the compact generated profile.

    Chat and image code still read the older Character Profile V2 shape. Keeping this
    adapter lets generation stay small while the rest of the app migrates gradually.
    """
    bible = dict(character_bible)
    identity = _dict_value(bible.get("identity"))
    visual = _dict_value(bible.get("visual"))
    compact_voice = _dict_value(bible.get("voice"))
    inner = _dict_value(bible.get("inner_state"))
    world = _dict_value(bible.get("world"))
    openings = _dict_value(bible.get("openings"))
    growth_forms = _dict_value(visual.get("growth_forms"))
    lorebook_entries = _lorebook_entries(bible.get("lorebook_entries"))
    legacy_defaults = character_bible_legacy_defaults()

    species = _string_value(identity.get("species")) or raw_description
    one_liner = _string_value(identity.get("one_liner")) or species
    voice_rules = _string_list(compact_voice.get("rules"), limit=8)
    sample_replies = _string_list(compact_voice.get("sample_replies"), limit=8)
    avoid_patterns = _string_list(compact_voice.get("avoid"), limit=8)
    catchphrases = _string_list(compact_voice.get("catchphrases"), limit=5)
    rhythm = _string_value(compact_voice.get("rhythm")) or legacy_defaults["voiceRhythm"]
    objects = _string_list(world.get("objects"), limit=6)
    routines = _string_list(world.get("routines"), limit=6)
    relationships = _string_list(world.get("relationships"), limit=6)
    story_seeds = _string_list(world.get("story_seeds"), limit=6)
    fears = _string_list(inner.get("fears"), limit=5)
    comfort_actions = _string_list(inner.get("comfort_actions"), limit=5)
    home = _string_value(world.get("home"))
    habitat = _string_value(world.get("habitat"))
    first_message = _string_value(openings.get("first_message"))
    alternate_greetings = _string_list(openings.get("alternate_greetings"), limit=4)
    opening_scenes = [item for item in [first_message, *alternate_greetings] if item]

    bible["identity"] = {
        "name": _string_value(identity.get("name")),
        "nickname": _string_value(identity.get("nickname")),
        "species": species,
        "role": _string_value(identity.get("role")) or legacy_defaults["identityRole"],
        "one_liner": one_liner,
    }
    bible["species"] = _string_value(bible.get("species")) or species
    bible["signature"] = _string_value(bible.get("signature")) or one_liner
    bible["personality"] = _string_value(bible.get("personality")) or " ".join(
        text
        for text in (
            _string_value(inner.get("core_want")),
            _string_value(inner.get("inner_conflict")),
        )
        if text
    )
    bible["main_colors"] = _string_list(visual.get("colors"), limit=5)
    bible["signature_features"] = _string_list(visual.get("features"), limit=6)
    bible["materials"] = _string_list(visual.get("materials"), limit=5)
    bible["proportions"] = _string_value(visual.get("proportions"))
    bible["baby_design"] = _string_value(growth_forms.get("baby"))
    bible["teen_design"] = _string_value(growth_forms.get("teen"))
    bible["adult_design"] = _string_value(growth_forms.get("adult"))
    bible["do_not_change"] = _string_list(visual.get("anchors"), limit=6)
    bible["voice"] = {
        "voice_rules": voice_rules,
        "speech_rules": voice_rules,
        "sentence_rhythm": rhythm,
        "addressing_user": legacy_defaults["addressingUser"],
        "humor_style": legacy_defaults["humorStyle"],
        "uncertainty_style": legacy_defaults["uncertaintyStyle"],
        "catchphrases": catchphrases,
        "sample_replies": sample_replies,
        "avoid_patterns": avoid_patterns,
    }
    bible["dialogue_style"] = {
        "voice_rules": voice_rules,
        "emotional_reactions": comfort_actions,
        "initiative_style": legacy_defaults["initiativeStyle"],
        "sample_replies": sample_replies[:6],
        "avoid_patterns": avoid_patterns,
    }
    bible["inner_state"] = {
        "core_want": _string_value(inner.get("core_want")),
        "inner_conflict": _string_value(inner.get("inner_conflict")),
        "fears": fears,
        "comfort_actions": comfort_actions,
    }
    bible["world"] = {
        "home": home,
        "habitat": habitat,
        "objects": objects,
        "routines": routines,
        "relationships": relationships,
        "story_seeds": story_seeds,
        "lorebook_entries": lorebook_entries,
    }
    bible["openings"] = {
        "first_message": first_message,
        "alternate_greetings": alternate_greetings,
        "opening_scenes": opening_scenes,
    }
    bible["opening_scenes"] = opening_scenes
    bible["lorebook_entries"] = [
        {"keys": item["keys"], "content": item["content"]} for item in lorebook_entries
    ]
    bible["lore"] = {
        "world": {
            "name": "",
            "environment": habitat,
            "story": habitat,
            "rules": bible["do_not_change"],
            "sensory_details": objects,
        },
        "home": {
            "place": home,
            "room": home,
            "favorite_spot": objects[0] if objects else home,
            "story": home,
            "objects": objects,
        },
        "origin": {
            "birthplace": habitat,
            "caretakers": [],
            "formative_event": _string_value(inner.get("inner_conflict")),
            "story": one_liner,
        },
        "relationships": {
            "family": [],
            "friends": [],
            "attitude_to_user": legacy_defaults["attitudeToUser"],
            "story": "; ".join(relationships),
        },
        "inner_life": {
            "core_want": _string_value(inner.get("core_want")),
            "inner_conflict": _string_value(inner.get("inner_conflict")),
            "likes": objects[:3] + routines[:2],
            "dislikes": [],
            "fears": fears,
            "dreams": story_seeds[:3],
            "habits": routines,
            "comfort_actions": comfort_actions,
            "flaws": [],
        },
        "voice": {
            "speech_pattern": rhythm,
            "favorite_phrases": catchphrases,
            "topic_hooks": [key for entry in lorebook_entries for key in entry["keys"][:2]][:8],
            "secret_details": story_seeds,
            "avoid_saying": avoid_patterns,
        },
        "growth_arc": {
            "baby": bible["baby_design"],
            "teen": bible["teen_design"],
            "adult": bible["adult_design"],
        },
        "story_seeds": story_seeds,
    }
    bible["provenance"] = {
        "source": "generated",
        "source_urls": [],
        "license_notes": legacy_defaults["provenanceLicenseNotes"],
    }
    bible.setdefault("extensions", {})
    return bible


def create_character_bible(
    user_description: str,
    lore_seed: dict[str, str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    client = get_openai_client()
    effective_lore_seed = lore_seed or create_lore_seed()
    world_anchors = select_world_description_anchors(user_description, count=2)
    world_anchor_block = format_world_description_anchors_for_prompt(world_anchors)
    system_message = {
        "role": "system",
        "content": character_bible_system_prompt(),
    }
    character_bible = _character_bible_completion(
        client,
        settings,
        "pet_creation/character_bible",
        [
            system_message,
            {
                "role": "user",
                "content": build_character_bible_prompt(
                    user_description,
                    lore_seed=effective_lore_seed,
                    world_description_anchors=world_anchor_block,
                ),
            },
        ],
    )
    character_bible = expand_compact_character_bible(
        character_bible,
        raw_description=user_description,
    )
    character_bible = upgrade_character_bible_v2(
        character_bible,
        raw_description=user_description,
    )
    character_bible = _attach_world_anchor_trace(character_bible, world_anchors)
    issues = character_bible_quality_issues(user_description, character_bible)
    if issues:
        logger.info("Compact character bible quality flags: %s", issues)
    return character_bible


def build_image_generate_kwargs(
    settings: Any,
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    kwargs = {
        "model": model or get_image_model(settings),
        "prompt": prompt,
        "size": size or settings.openai_image_size,
        "quality": settings.openai_image_quality,
        "n": 1,
        "output_format": settings.openai_image_output_format,
        "timeout": settings.openai_image_timeout_seconds,
    }
    if input_references:
        kwargs["input_references"] = input_references
    return kwargs


def _is_seedream_image_model(model: Any) -> bool:
    return "seedream" in str(model or "").lower()


def _aspect_ratio_from_size(size: Any) -> str | None:
    match = re.fullmatch(r"\s*(\d+)x(\d+)\s*", str(size or ""))
    if not match:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    divisor = math.gcd(width, height)
    ratio = f"{width // divisor}:{height // divisor}"
    return ratio if ratio in OPENROUTER_IMAGE_ASPECT_RATIOS else None


def _openrouter_image_generate_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(kwargs)
    if not _is_seedream_image_model(normalized.get("model")):
        return normalized

    size = normalized.pop("size", None)
    normalized["resolution"] = OPENROUTER_SEEDREAM_IMAGE_RESOLUTION
    normalized["aspect_ratio"] = _aspect_ratio_from_size(size) or "auto"
    return normalized


def build_openrouter_image_generate_kwargs(
    settings: Any,
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _openrouter_image_generate_kwargs(
        build_image_generate_kwargs(
            settings,
            prompt,
            model=model or get_openrouter_image_model(settings),
            size=size,
            input_references=input_references,
        )
    )


def _image_result_bytes(first: Any) -> bytes:
    b64_json = (
        first.get("b64_json") if isinstance(first, dict) else getattr(first, "b64_json", None)
    )
    if b64_json:
        return base64.b64decode(b64_json)

    image_url = first.get("url") if isinstance(first, dict) else getattr(first, "url", None)
    if image_url:
        download = httpx.get(image_url, timeout=60)
        download.raise_for_status()
        return download.content

    raise RuntimeError("IMAGE_RESPONSE_EMPTY")


def _clean_setting_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _kandinsky_api_key(settings: Any) -> str:
    api_key = _clean_setting_string(getattr(settings, "kandinsky_api_key", None))
    if not api_key:
        raise MissingKandinskyAPIKey
    return api_key


def _kandinsky_base_url(settings: Any) -> str:
    return (
        _clean_setting_string(getattr(settings, "kandinsky_base_url", None))
        or "https://studio.kandinskylab.ai/api"
    ).rstrip("/")


def _kandinsky_headers(settings: Any) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_kandinsky_api_key(settings)}",
        "Content-Type": "application/json",
    }


def _kandinsky_http_timeout(settings: Any) -> float:
    return max(30.0, float(getattr(settings, "openai_image_timeout_seconds", 180)))


def _is_retryable_kandinsky_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def _kandinsky_retry_delay(attempt_index: int) -> float:
    return KANDINSKY_HTTP_RETRY_SECONDS[min(attempt_index, len(KANDINSKY_HTTP_RETRY_SECONDS) - 1)]


def _kandinsky_with_retry(label: str, operation: str, call: Any) -> Any:
    for attempt_index in range(KANDINSKY_HTTP_MAX_ATTEMPTS):
        try:
            response = call()
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                is_last_attempt = attempt_index == KANDINSKY_HTTP_MAX_ATTEMPTS - 1
                if is_last_attempt or not _is_retryable_kandinsky_error(exc):
                    return response
                retry_delay = _kandinsky_retry_delay(attempt_index)
                logger.warning(
                    "kandinsky_image_%s retry label=%s attempt=%s maxAttempts=%s "
                    "retryDelaySeconds=%s errorType=%s status=%s response=%s",
                    operation,
                    label,
                    attempt_index + 1,
                    KANDINSKY_HTTP_MAX_ATTEMPTS,
                    retry_delay,
                    type(exc).__name__,
                    exc.response.status_code,
                    exc.response.text[:1000],
                )
                time.sleep(retry_delay)
                continue
            return response
        except Exception as exc:
            is_last_attempt = attempt_index == KANDINSKY_HTTP_MAX_ATTEMPTS - 1
            if is_last_attempt or not _is_retryable_kandinsky_error(exc):
                raise
            retry_delay = _kandinsky_retry_delay(attempt_index)
            logger.warning(
                "kandinsky_image_%s retry label=%s attempt=%s maxAttempts=%s "
                "retryDelaySeconds=%s errorType=%s error=%s",
                operation,
                label,
                attempt_index + 1,
                KANDINSKY_HTTP_MAX_ATTEMPTS,
                retry_delay,
                type(exc).__name__,
                str(exc),
            )
            time.sleep(retry_delay)
    raise RuntimeError("unreachable kandinsky retry state")


def _reference_url_from_entry(reference: dict[str, Any]) -> str:
    image_url = reference.get("image_url")
    if isinstance(image_url, dict):
        return _clean_setting_string(image_url.get("url"))
    if isinstance(image_url, str):
        return _clean_setting_string(image_url)
    return _clean_setting_string(reference.get("url"))


def _kandinsky_reference_image_b64(image_url: str) -> str:
    if image_url.startswith("data:image/"):
        header, separator, payload = image_url.partition(",")
        if not separator or not payload:
            return ""
        if ";base64" in header:
            return payload.replace("\n", "").replace("\r", "").strip()
        return base64.b64encode(payload.encode("utf-8")).decode("utf-8")

    response = httpx.get(image_url, timeout=30)
    response.raise_for_status()
    return base64.b64encode(response.content).decode("utf-8")


def _kandinsky_reference_images(
    input_references: list[dict[str, Any]] | None,
) -> list[str]:
    encoded_images: list[str] = []
    for reference in input_references or []:
        image_url = _reference_url_from_entry(reference)
        if not image_url:
            continue
        encoded_image = _kandinsky_reference_image_b64(image_url)
        if encoded_image:
            encoded_images.append(encoded_image)
        if len(encoded_images) == 4:
            break
    return encoded_images


def _kandinsky_create_task(
    settings: Any,
    *,
    task_type: str,
    params: dict[str, Any],
    label: str,
) -> str:
    url = f"{_kandinsky_base_url(settings)}/tasks/{task_type}"
    response = _kandinsky_with_retry(
        label,
        "task_create",
        lambda: httpx.post(
            url,
            headers=_kandinsky_headers(settings),
            json={"params": params},
            timeout=_kandinsky_http_timeout(settings),
        ),
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.error(
            "kandinsky_image_task_create failed label=%s task_type=%s status=%s response=%s",
            label,
            task_type,
            response.status_code,
            response.text[:2000],
        )
        raise
    payload = response.json()
    task_id = _clean_setting_string(payload.get("task_id") or payload.get("id"))
    if not task_id:
        raise KandinskyTaskError("KANDINSKY_TASK_ID_MISSING")
    return task_id


def _kandinsky_wait_done(settings: Any, *, task_id: str, label: str) -> dict[str, Any]:
    url = f"{_kandinsky_base_url(settings)}/tasks/{task_id}"
    headers = _kandinsky_headers(settings)
    timeout_seconds = max(1.0, float(getattr(settings, "openai_image_timeout_seconds", 180)))
    poll_seconds = max(1.0, float(getattr(settings, "kandinsky_poll_interval_seconds", 5)))
    deadline = time.monotonic() + timeout_seconds

    while True:
        response = _kandinsky_with_retry(
            label,
            "task_status",
            lambda: httpx.get(
                url,
                headers=headers,
                timeout=_kandinsky_http_timeout(settings),
            ),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            logger.error(
                "kandinsky_image_task_status failed label=%s task_id=%s status=%s response=%s",
                label,
                task_id,
                response.status_code,
                response.text[:2000],
            )
            raise
        payload = response.json()
        status = _clean_setting_string(payload.get("status")).lower()
        if status == "done":
            return payload
        if status in {"failed", "error"}:
            error = payload.get("error") or payload.get("message") or "unknown error"
            raise KandinskyTaskError(f"KANDINSKY_TASK_FAILED: {error}")
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError(f"Kandinsky image task timed out: {task_id}")
        time.sleep(min(poll_seconds, remaining_seconds))


def _kandinsky_download_result(settings: Any, *, task_id: str, label: str) -> bytes:
    url = f"{_kandinsky_base_url(settings)}/tasks/{task_id}/result"
    response = _kandinsky_with_retry(
        label,
        "result",
        lambda: httpx.get(
            url,
            headers=_kandinsky_headers(settings),
            timeout=_kandinsky_http_timeout(settings),
        ),
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.error(
            "kandinsky_image_result failed label=%s task_id=%s status=%s response=%s",
            label,
            task_id,
            response.status_code,
            response.text[:2000],
        )
        raise
    if not response.content:
        raise RuntimeError("KANDINSKY_IMAGE_RESPONSE_EMPTY")
    return response.content


def _generate_openrouter_image_bytes(
    settings: Any,
    prompt: str,
    *,
    label: str,
    model: str | None = None,
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> bytes:
    kwargs = build_openrouter_image_generate_kwargs(
        settings,
        prompt,
        model=model or get_openrouter_image_model(settings),
        size=size,
        input_references=input_references,
    )
    request_body = {
        key: value for key, value in kwargs.items() if key != "timeout" and value is not None
    }
    headers = {
        "Authorization": f"Bearer {get_openrouter_api_key(settings)}",
        "Content-Type": "application/json",
        **get_openrouter_headers(settings),
    }
    response = httpx.post(
        get_openrouter_image_url(settings),
        headers=headers,
        json=request_body,
        timeout=settings.openai_image_timeout_seconds,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.error(
            "openrouter_image_generation failed label=%s model=%s status=%s response=%s",
            label,
            request_body.get("model"),
            response.status_code,
            response.text[:2000],
        )
        raise
    response_payload = response.json()
    log_image_generation_response(
        label,
        kwargs,
        response_payload,
        headers=getattr(response, "headers", None),
    )
    data = response_payload.get("data") or []
    if not data:
        raise RuntimeError("IMAGE_RESPONSE_EMPTY")
    return _image_result_bytes(data[0])


def generate_kandinsky_image_bytes(
    prompt: str,
    *,
    label: str,
    input_references: list[dict[str, Any]] | None = None,
) -> bytes:
    settings = get_settings()
    reference_images = _kandinsky_reference_images(input_references)
    if reference_images:
        task_type = _clean_setting_string(getattr(settings, "kandinsky_i2i_task_type", None))
        task_type = task_type or "k6-i2i"
        params: dict[str, Any] = {
            "image": reference_images,
            "query": prompt,
        }
    else:
        task_type = _clean_setting_string(getattr(settings, "kandinsky_t2i_task_type", None))
        task_type = task_type or "k6-image-t2i"
        params = {
            "query": prompt,
            "resolution": (
                _clean_setting_string(getattr(settings, "kandinsky_image_resolution", None))
                or "1280x768"
            ),
        }

    request_kwargs = {
        "model": f"kandinsky/{task_type}",
        "prompt": prompt,
        "resolution": params.get("resolution"),
        "n": 1,
        "input_references": input_references or [],
        "timeout": getattr(settings, "openai_image_timeout_seconds", 180),
    }
    log_image_generation_prompt(label, request_kwargs)
    task_id = _kandinsky_create_task(
        settings,
        task_type=task_type,
        params=params,
        label=label,
    )
    status_payload = _kandinsky_wait_done(settings, task_id=task_id, label=label)
    image_bytes = _kandinsky_download_result(settings, task_id=task_id, label=label)
    log_image_generation_response(
        label,
        request_kwargs,
        {
            "id": task_id,
            "status": status_payload.get("status"),
            "resultBytes": len(image_bytes),
        },
    )
    return image_bytes


def generate_image_bytes(
    prompt: str,
    *,
    label: str = "pet_creation/image",
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> bytes:
    settings = get_settings()
    if is_openrouter_provider(settings):
        openrouter_kwargs = build_openrouter_image_generate_kwargs(
            settings,
            prompt,
            model=get_openrouter_image_model(settings),
            size=size,
            input_references=input_references,
        )
        log_image_generation_prompt(label, openrouter_kwargs)
        return _generate_openrouter_image_bytes(
            settings,
            prompt,
            label=label,
            model=get_openrouter_image_model(settings),
            size=size,
            input_references=input_references,
        )

    client = get_openai_client()
    kwargs = build_image_generate_kwargs(settings, prompt, size=size)
    log_image_generation_prompt(label, kwargs)
    response = client.images.generate(**kwargs)
    response_payload = response.model_dump() if hasattr(response, "model_dump") else {}
    log_image_generation_response(label, kwargs, response_payload)
    return _image_result_bytes(response.data[0])


def generate_openrouter_image_bytes(
    prompt: str,
    *,
    label: str,
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> bytes:
    settings = get_settings()
    model = get_openrouter_image_model(settings)
    openrouter_kwargs = build_openrouter_image_generate_kwargs(
        settings,
        prompt,
        model=model,
        size=size,
        input_references=input_references,
    )
    log_image_generation_prompt(label, openrouter_kwargs)
    return _generate_openrouter_image_bytes(
        settings,
        prompt,
        label=label,
        model=model,
        size=size,
        input_references=input_references,
    )


def remove_image_background(image_bytes: bytes) -> bytes:
    settings = get_settings()
    if not BACKGROUND_REMOVAL_SCRIPT.exists():
        raise RuntimeError(f"Background removal script not found: {BACKGROUND_REMOVAL_SCRIPT}")

    try:
        result = subprocess.run(
            ["node", str(BACKGROUND_REMOVAL_SCRIPT)],
            input=image_bytes,
            capture_output=True,
            check=False,
            timeout=settings.background_removal_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Node.js is required for background removal.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Background removal timed out.") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Background removal failed: {stderr or 'unknown error'}")

    if not result.stdout:
        raise RuntimeError("Background removal returned empty output.")

    return result.stdout


def generate_sprite_sheet_bytes(prompt: str) -> bytes:
    return remove_image_background(generate_image_bytes(prompt))


def generated_dir_for(pet_id: uuid.UUID) -> Path:
    return Path(__file__).resolve().parents[2] / "static" / "generated" / str(pet_id)


def normalize_single_sprite_image(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        normalized = image.convert("RGBA")
        output_side = min(normalized.width, normalized.height)
        background_pixel = background_pixel_for(normalized)
        content_bbox = foreground_component_bbox(
            normalized,
            (0, 0, normalized.width, normalized.height),
        )
        if content_bbox is None:
            output = normalized
        else:
            output = normalize_sprite_cell(
                normalized,
                content_bbox,
                (output_side, output_side),
                background_pixel,
            )

        buffer = BytesIO()
        output.save(buffer, format="PNG")
        return buffer.getvalue()


def generate_single_sprite_image_bytes(prompt: str) -> bytes:
    return normalize_single_sprite_image(remove_image_background(generate_image_bytes(prompt)))


def generate_individual_sprite_paths(
    asset_id: uuid.UUID,
    description: str,
    character_bible: dict[str, Any],
) -> dict[tuple[str, str], tuple[Path, str]]:
    output_dir = generated_dir_for(asset_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[tuple[str, str], tuple[Path, str]] = {}
    prompt = build_pet_state_strip_prompt(
        description,
        character_bible,
        stage=FAST_GENERATION_STAGE,
    )
    try:
        strip_bytes = remove_image_background(generate_image_bytes(prompt))
    except Exception as exc:
        if generation_error_code(exc) != "IMAGE_PROMPT_REJECTED":
            raise
        logger.info("Retrying image generation with safety-constrained sprite prompt")
        prompt = build_pet_state_strip_safety_retry_prompt(
            description,
            character_bible,
            stage=FAST_GENERATION_STAGE,
        )
        strip_bytes = remove_image_background(generate_image_bytes(prompt))
    with Image.open(BytesIO(strip_bytes)) as strip_image:
        cell_images = extract_state_strip_cells(strip_image, stage=FAST_GENERATION_STAGE)

    for stage, state in FAST_GENERATION_SKINS:
        path = output_dir / f"{stage}-{state}.png"
        cell_images[(stage, state)].save(path, format="PNG")
        output_paths[(stage, state)] = (path, prompt)

    return output_paths


def crop_sprite_sheet(pet_id: uuid.UUID, sprite_path: Path) -> dict[tuple[str, str], Path]:
    output_paths: dict[tuple[str, str], Path] = {}
    with Image.open(sprite_path) as image:
        cell_images = extract_sprite_cells(image)

        for (stage, state), crop in cell_images.items():
            path = generated_dir_for(pet_id) / f"{stage}-{state}.png"
            crop.save(path, format="PNG")
            output_paths[(stage, state)] = path

    return output_paths


def generation_error_code(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "OPENAI_TIMEOUT"
    if isinstance(exc, httpx.TimeoutException):
        return "OPENAI_TIMEOUT"
    if isinstance(exc, AuthenticationError):
        return "OPENAI_AUTH_FAILED"
    if isinstance(exc, PermissionDeniedError):
        return "OPENAI_PERMISSION_DENIED"
    if isinstance(exc, RateLimitError):
        return "OPENAI_RATE_LIMIT"
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 401:
            return "OPENAI_AUTH_FAILED"
        if status_code == 403:
            return "OPENAI_PERMISSION_DENIED"
        if status_code == 429:
            return "OPENAI_RATE_LIMIT"
        if status_code == 400:
            return "OPENAI_BAD_REQUEST"
        return f"OPENAI_STATUS_{status_code}"
    if isinstance(exc, BadRequestError):
        message = str(exc).lower()
        if any(term in message for term in ("safety", "policy", "moderation", "rejected")):
            return "IMAGE_PROMPT_REJECTED"
        return "OPENAI_BAD_REQUEST"
    if isinstance(exc, APIStatusError):
        return f"OPENAI_STATUS_{exc.status_code}"
    if isinstance(exc, APIConnectionError | httpx.HTTPError):
        return "OPENAI_CONNECTION_FAILED"
    if isinstance(exc, RuntimeError):
        message = str(exc)
        if message.startswith("Background removal"):
            return "IMAGE_POSTPROCESS_FAILED"
    if isinstance(exc, OSError):
        return "IMAGE_SAVE_FAILED"
    return "GENERATION_FAILED"


def generate_pet_asset_set(description: str) -> dict[str, Any]:
    asset_set_id = uuid.uuid4()
    output_dir = generated_dir_for(asset_set_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    character_bible = create_character_bible(description)
    generated_paths = generate_individual_sprite_paths(asset_set_id, description, character_bible)
    version = int(datetime.now(UTC).timestamp())

    generated_urls = {
        key: f"/static/generated/{asset_set_id}/{path.name}?v={version}"
        for key, (path, _prompt) in generated_paths.items()
    }
    images: dict[str, dict[str, str]] = {stage: {} for stage in STAGE_ROWS}
    for stage in STAGE_ROWS:
        for state in STATE_COLUMNS:
            source_key = FAST_GENERATION_STATE_FALLBACKS[state]
            images[stage][state] = generated_urls[source_key]

    return {
        "assetSetId": str(asset_set_id),
        "generatedAt": datetime.now(UTC),
        "images": images,
        "spriteSheetUrl": None,
        "characterBible": character_bible,
    }
