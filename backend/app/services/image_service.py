from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from collections import deque
from datetime import UTC, datetime
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
from app.db import SessionLocal
from app.models import Pet
from app.prompts.pet_image_prompts import (
    build_character_bible_prompt,
    build_pet_sprite_sheet_prompt,
    create_lore_seed,
)
from app.services.birth_message_service import ensure_birth_message
from app.services.game_service import calculate_stage
from app.services.openai_service import MissingOpenAIAPIKey, get_openai_client
from app.services.pet_service import upsert_pet_image

logger = logging.getLogger(__name__)

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

CHARACTER_BIBLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "species",
        "personality",
        "signature",
        "main_colors",
        "signature_features",
        "materials",
        "proportions",
        "baby_design",
        "teen_design",
        "adult_design",
        "do_not_change",
        "lore",
    ],
    "properties": {
        "species": {"type": "string"},
        "personality": {
            "type": "string",
            "description": (
                "2-4 Russian sentences about motives, fears, contradictions, comfort, "
                "and behavior shaped by the pet's background, not a list of random events."
            ),
        },
        "signature": {
            "type": "string",
            "description": (
                "A compact Russian paragraph explaining the memorable core feature through "
                "specific everyday actions and relationship behavior."
            ),
        },
        "main_colors": {"type": "array", "items": {"type": "string"}},
        "signature_features": {"type": "array", "items": {"type": "string"}},
        "materials": {"type": "array", "items": {"type": "string"}},
        "proportions": {"type": "string"},
        "baby_design": {"type": "string"},
        "teen_design": {"type": "string"},
        "adult_design": {"type": "string"},
        "do_not_change": {"type": "array", "items": {"type": "string"}},
        "lore": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "world",
                "home",
                "origin",
                "relationships",
                "inner_life",
                "voice",
                "growth_arc",
                "story_seeds",
            ],
            "properties": {
                "world": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "name",
                        "environment",
                        "story",
                        "rules",
                        "sensory_details",
                    ],
                    "properties": {
                        "name": {"type": "string"},
                        "environment": {"type": "string"},
                        "story": {
                            "type": "string",
                            "description": (
                                "Background foundation paragraph: what kind of place this is, "
                                "how daily life works there, what social roles or tensions exist, "
                                "and what can be revealed later. Avoid one-off named incidents."
                            ),
                        },
                        "rules": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Concrete cause-and-effect rules of the local world, not slogans."
                            ),
                        },
                        "sensory_details": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "home": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "place",
                        "room",
                        "favorite_spot",
                        "story",
                        "objects",
                    ],
                    "properties": {
                        "place": {"type": "string"},
                        "room": {"type": "string"},
                        "favorite_spot": {"type": "string"},
                        "story": {
                            "type": "string",
                            "description": (
                                "Home foundation paragraph: layout, routines, emotional role, "
                                "and reusable details. Avoid resolved gift/rescue incidents."
                            ),
                        },
                        "objects": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Physical objects tied to events, habits, or relationships."
                            ),
                        },
                    },
                },
                "origin": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "birthplace",
                        "caretakers",
                        "formative_event",
                        "story",
                    ],
                    "properties": {
                        "birthplace": {"type": "string"},
                        "caretakers": {"type": "array", "items": {"type": "string"}},
                        "formative_event": {
                            "type": "string",
                            "description": (
                                "Broad formative pressure or repeated early pattern that shaped "
                                "the pet. It should guide future stories without locking a random "
                                "micro-event."
                            ),
                        },
                        "story": {
                            "type": "string",
                            "description": (
                                "Origin background with caretakers, early conditions, current "
                                "habits, and open hooks that can be specified later in chat."
                            ),
                        },
                    },
                },
                "relationships": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["family", "friends", "attitude_to_user", "story"],
                    "properties": {
                        "family": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Role-based non-human relatives or caretaker-like figures. Prefer "
                                "clear roles over many invented proper names."
                            ),
                        },
                        "friends": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "name",
                                    "role",
                                    "species_or_form",
                                    "relationship_dynamic",
                                ],
                                "properties": {
                                    "name": {"type": "string"},
                                    "role": {"type": "string"},
                                    "species_or_form": {"type": "string"},
                                    "relationship_dynamic": {
                                        "type": "string",
                                        "description": (
                                            "Recurring shared dynamic and open story hook: who "
                                            "usually helps, teases, argues, protects, or teaches "
                                            "whom. Avoid finished random incidents."
                                        ),
                                    },
                                },
                            },
                        },
                        "attitude_to_user": {"type": "string"},
                        "story": {
                            "type": "string",
                            "description": (
                                "Relationship network foundation: who exists around the pet, what "
                                "roles they play, and what emotional tensions can be revealed "
                                "later. Avoid dumping several named incidents."
                            ),
                        },
                    },
                },
                "inner_life": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "core_want",
                        "inner_conflict",
                        "likes",
                        "dislikes",
                        "fears",
                        "dreams",
                        "habits",
                        "comfort_actions",
                        "flaws",
                    ],
                    "properties": {
                        "core_want": {
                            "type": "string",
                            "description": "Direct desire caused by the pet's background.",
                        },
                        "inner_conflict": {
                            "type": "string",
                            "description": "Direct tension caused by the pet's background.",
                        },
                        "likes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Concrete objects, places, actions, or sensory details tied to "
                                "a routine, home zone, relationship role, or background tension. "
                                "No decorative standalone items, "
                                "no user-behavior preferences like short requests."
                            ),
                        },
                        "dislikes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Concrete irritants or situations caused by the background."
                            ),
                        },
                        "fears": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Specific fears with a clear cause in origin, home, or "
                                "relationships, without over-defining the full incident."
                            ),
                        },
                        "dreams": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific future wish tied to the pet's world.",
                        },
                        "habits": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Physical actions the pet does repeatedly, not personality "
                                "summaries."
                            ),
                        },
                        "comfort_actions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Physical self-soothing actions tied to home objects or friends."
                            ),
                        },
                        "flaws": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Concrete behavioral flaws caused by background pressures, "
                                "not vague adjectives."
                            ),
                        },
                    },
                },
                "voice": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "speech_pattern",
                        "favorite_phrases",
                        "topic_hooks",
                        "secret_details",
                        "avoid_saying",
                    ],
                    "properties": {
                        "speech_pattern": {"type": "string"},
                        "favorite_phrases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "topic_hooks": {"type": "array", "items": {"type": "string"}},
                        "secret_details": {"type": "array", "items": {"type": "string"}},
                        "avoid_saying": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "growth_arc": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["baby", "teen", "adult"],
                    "properties": {
                        "baby": {
                            "type": "string",
                            "description": "Baby-stage behavior, need, or likely first discovery.",
                        },
                        "teen": {
                            "type": "string",
                            "description": "Teen-stage behavior change or social opening.",
                        },
                        "adult": {
                            "type": "string",
                            "description": "Adult-stage responsibility or deeper unresolved theme.",
                        },
                    },
                },
                "story_seeds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Open-ended future reveal hooks. Each item names a domain that chat may "
                        "invent later, such as a nickname, old relative, local tradition, "
                        "favorite hidden place, or first argument, without deciding the exact "
                        "fact now."
                    ),
                },
            },
        },
    },
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
            for child in item.values():
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
    return tuple(issues)


def _character_bible_completion(
    client: Any,
    settings: Any,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    completion = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "character_bible",
                "schema": CHARACTER_BIBLE_SCHEMA,
                "strict": True,
            },
        },
        timeout=settings.openai_chat_timeout_seconds,
    )
    content = completion.choices[0].message.content or "{}"
    return json.loads(content)


def _repair_character_bible_prompt(
    description: str,
    character_bible: dict[str, Any],
    issues: tuple[str, ...],
    lore_seed: dict[str, str] | None = None,
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

QUALITY_ISSUES:
{", ".join(issues)}

Repair rules:
- Preserve the same visual identity and all required schema fields.
- If the pet is not plant/garden/window/shelf-based, remove greenhouse, shelf, moss, dew,
  warm-lamp, seed, and tiny-garden defaults from lore.
- Replace generic cozy-corner lore with a concrete setting that follows the pet's own premise.
- Fix physical nonsense. Steam can hiss, warm, curl, fog, or tickle; steam itself is not loud.
  If something makes sound, name the valve, kettle, vent, bell, shell, gear, or creature doing it.
- Keep world, home, origin, relationships, and inner_life connected by clear cause and effect.

CURRENT_CHARACTER_BIBLE:
{json.dumps(character_bible, ensure_ascii=False, indent=2)}
""".strip()


STAGE_ROWS = ("baby", "teen", "adult")
STATE_COLUMNS = ("idle", "happy", "sad", "hungry")
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


def create_character_bible(
    user_description: str,
    lore_seed: dict[str, str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    client = get_openai_client()
    effective_lore_seed = lore_seed or create_lore_seed()
    system_message = {
        "role": "system",
        "content": (
            "Create scaffold-first JSON character bibles with varied, coherent "
            "storybook canon that can be revealed gradually in chat."
        ),
    }
    character_bible = _character_bible_completion(
        client,
        settings,
        [
            system_message,
            {
                "role": "user",
                "content": build_character_bible_prompt(
                    user_description,
                    lore_seed=effective_lore_seed,
                ),
            },
        ],
    )
    issues = character_bible_quality_issues(user_description, character_bible)
    if not issues:
        return character_bible

    logger.info("Repairing character bible for quality issues: %s", issues)
    repaired = _character_bible_completion(
        client,
        settings,
        [
            system_message,
            {
                "role": "user",
                "content": _repair_character_bible_prompt(
                    user_description,
                    character_bible,
                    issues,
                    effective_lore_seed,
                ),
            },
        ],
    )
    return repaired


def build_image_generate_kwargs(settings: Any, prompt: str) -> dict[str, Any]:
    return {
        "model": settings.openai_image_model,
        "prompt": prompt,
        "size": settings.openai_image_size,
        "quality": settings.openai_image_quality,
        "n": 1,
        "output_format": settings.openai_image_output_format,
        "timeout": settings.openai_image_timeout_seconds,
    }


def generate_sprite_sheet_bytes(prompt: str) -> bytes:
    settings = get_settings()
    client = get_openai_client()
    kwargs = build_image_generate_kwargs(settings, prompt)
    response = client.images.generate(**kwargs)
    first = response.data[0]
    b64_json = getattr(first, "b64_json", None)
    if b64_json:
        return base64.b64decode(b64_json)

    image_url = getattr(first, "url", None)
    if image_url:
        download = httpx.get(image_url, timeout=60)
        download.raise_for_status()
        return download.content

    raise RuntimeError("IMAGE_RESPONSE_EMPTY")


def generated_dir_for(pet_id: uuid.UUID) -> Path:
    return Path(__file__).resolve().parents[2] / "static" / "generated" / str(pet_id)


def crop_sprite_sheet(pet_id: uuid.UUID, sprite_path: Path) -> dict[tuple[str, str], Path]:
    output_paths: dict[tuple[str, str], Path] = {}
    with Image.open(sprite_path) as image:
        cell_images = extract_sprite_cells(image)

        for (stage, state), crop in cell_images.items():
            path = generated_dir_for(pet_id) / f"{stage}-{state}.png"
            crop.save(path, format="PNG")
            output_paths[(stage, state)] = path

    return output_paths


def mark_generation_failed(pet_id: uuid.UUID, code: str) -> None:
    with SessionLocal() as db:
        pet = db.get(Pet, pet_id)
        if pet is None:
            return
        pet.status = "failed"
        pet.generation_error = code
        db.add(pet)
        db.commit()


def generation_error_code(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "OPENAI_TIMEOUT"
    if isinstance(exc, AuthenticationError):
        return "OPENAI_AUTH_FAILED"
    if isinstance(exc, PermissionDeniedError):
        return "OPENAI_PERMISSION_DENIED"
    if isinstance(exc, RateLimitError):
        return "OPENAI_RATE_LIMIT"
    if isinstance(exc, BadRequestError):
        message = str(exc).lower()
        if any(term in message for term in ("safety", "policy", "moderation", "rejected")):
            return "IMAGE_PROMPT_REJECTED"
        return "OPENAI_BAD_REQUEST"
    if isinstance(exc, APIStatusError):
        return f"OPENAI_STATUS_{exc.status_code}"
    if isinstance(exc, APIConnectionError | httpx.HTTPError):
        return "OPENAI_CONNECTION_FAILED"
    if isinstance(exc, OSError):
        return "IMAGE_SAVE_FAILED"
    return "GENERATION_FAILED"


def generate_pet_assets(pet_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        pet = db.get(Pet, pet_id)
        if pet is None:
            return

        try:
            character_bible = create_character_bible(pet.original_description)
            pet.character_profile_json = character_bible
            pet.current_stage = calculate_stage(pet.created_at)
            db.add(pet)
            db.commit()

            sprite_prompt = build_pet_sprite_sheet_prompt(pet.original_description, character_bible)
            image_bytes = generate_sprite_sheet_bytes(sprite_prompt)

            output_dir = generated_dir_for(pet.id)
            output_dir.mkdir(parents=True, exist_ok=True)
            sprite_path = output_dir / "sprite-sheet.png"
            sprite_path.write_bytes(image_bytes)
            cropped_paths = crop_sprite_sheet(pet.id, sprite_path)

            for (stage, state), path in cropped_paths.items():
                version = int(path.stat().st_mtime)
                upsert_pet_image(
                    db,
                    pet_id=pet.id,
                    stage=stage,
                    state=state,
                    image_url=f"/static/generated/{pet.id}/{path.name}?v={version}",
                    generation_prompt=sprite_prompt,
                )

            pet.status = "ready"
            pet.generation_error = None
            db.add(pet)
            ensure_birth_message(db, pet)
            db.commit()
        except MissingOpenAIAPIKey:
            db.rollback()
            mark_generation_failed(pet_id, "MISSING_OPENAI_API_KEY")
        except Exception as exc:
            db.rollback()
            code = generation_error_code(exc)
            logger.exception("Pet asset generation failed for %s with %s", pet_id, code)
            mark_generation_failed(pet_id, code)


def generate_pet_asset_set(description: str) -> dict[str, Any]:
    asset_set_id = uuid.uuid4()
    output_dir = generated_dir_for(asset_set_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    character_bible = create_character_bible(description)
    sprite_prompt = build_pet_sprite_sheet_prompt(description, character_bible)
    image_bytes = generate_sprite_sheet_bytes(sprite_prompt)

    sprite_path = output_dir / "sprite-sheet.png"
    sprite_path.write_bytes(image_bytes)
    cropped_paths = crop_sprite_sheet(asset_set_id, sprite_path)
    version = int(datetime.now(UTC).timestamp())

    images: dict[str, dict[str, str]] = {stage: {} for stage in STAGE_ROWS}
    for (stage, state), path in cropped_paths.items():
        images[stage][state] = f"/static/generated/{asset_set_id}/{path.name}?v={version}"

    return {
        "assetSetId": str(asset_set_id),
        "generatedAt": datetime.now(UTC),
        "images": images,
        "spriteSheetUrl": f"/static/generated/{asset_set_id}/sprite-sheet.png?v={version}",
        "characterBible": character_bible,
    }
