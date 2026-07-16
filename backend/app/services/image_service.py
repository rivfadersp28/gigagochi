from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import socket
import subprocess
import time
import uuid
import warnings
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urljoin, urlsplit

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
from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.llm.compat import complete_chat, response_log_value
from app.llm.contracts import LLMProviderError
from app.llm.runtime import resolve_llm_model
from app.media import ImageRequest, VideoRequest, get_media_gateway
from app.media.kandinsky_prompt_adapter import adapt_kandinsky_prompt
from app.media.runtime import get_media_router
from app.prompts.pet_image_prompts import (
    build_character_bible_prompt,
    build_pet_single_sprite_prompt,
    build_pet_single_sprite_safety_retry_prompt,
)
from app.services.character_bible_template import (
    character_bible_legacy_defaults,
    character_bible_schema,
    character_bible_system_prompt,
)
from app.services.character_cards import upgrade_character_bible_v2
from app.services.openai_service import (
    MissingOpenAIAPIKey,
    chat_reasoning_effort_kwargs,
    get_character_model,
    get_image_model,
    get_openai_platform_client,
    get_openrouter_api_key,
    get_openrouter_headers,
    get_openrouter_image_model,
    get_openrouter_image_url,
    get_openrouter_video_model,
    get_openrouter_video_url,
)
from app.services.prompt_debug import (
    log_chat_completion_prompt,
    log_chat_completion_response,
    log_image_generation_prompt,
    log_image_generation_response,
    log_video_generation_prompt,
)
from app.services.provider_task_checkpoint import (
    find_current_provider_task,
    has_current_provider_task_scope,
    implicit_provider_task_scope,
    mark_current_provider_task_failed,
    mark_current_provider_task_media_saved,
    provider_task_payload_fingerprint,
    release_current_provider_task_admission,
    save_current_provider_task,
)
from app.services.reference_assets import trusted_generated_asset_url
from app.services.storage_health_service import StorageCapacityError

logger = logging.getLogger(__name__)


class MissingKandinskyAPIKey(RuntimeError):
    pass


class KandinskyTaskError(RuntimeError):
    pass


class OpenRouterVideoTaskError(RuntimeError):
    pass


class OpenRouterVideoHTTPError(RuntimeError):
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        super().__init__(
            f"OpenRouter video generation failed: status={status_code} response={payload}"
        )


class MediaResultError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PromptRepairExhausted(RuntimeError):
    code = "OUTFIT_PROMPT_REPAIR_EXHAUSTED"


KANDINSKY_HTTP_MAX_ATTEMPTS = 2
KANDINSKY_HTTP_RETRY_SECONDS = (3.0,)
KANDINSKY_RESULT_RETRY_WINDOW_SECONDS = 50.0
KANDINSKY_RESULT_RETRY_INTERVAL_SECONDS = 3.0
OPENROUTER_VIDEO_HTTP_MAX_ATTEMPTS = 3
OPENROUTER_VIDEO_HTTP_RETRY_SECONDS = (1.0, 3.0)
OPENROUTER_VIDEO_POLL_RETRY_SECONDS = (1.0, 2.0, 4.0, 8.0, 15.0)
IMAGE_RESULT_MAX_BYTES = 25 * 1024 * 1024
IMAGE_RESULT_MAX_DIMENSION = 8192
IMAGE_RESULT_MAX_PIXELS = 16_000_000
VIDEO_RESULT_MAX_BYTES = 100 * 1024 * 1024
VIDEO_RESULT_MAX_DIMENSION = 4096
VIDEO_RESULT_MAX_PIXELS = 4096 * 2160
VIDEO_RESULT_MAX_DURATION_SECONDS = 60.0
PING_PONG_VIDEO_MAX_DURATION_SECONDS = 15.0
VIDEO_RESULT_MAX_FPS = 120.0
VIDEO_PROBE_TIMEOUT_SECONDS = 30
VIDEO_PROCESS_TIMEOUT_SECONDS = 180
MEDIA_RESULT_STREAM_CHUNK_BYTES = 64 * 1024
MEDIA_RESULT_ERROR_PREVIEW_MAX_BYTES = 64 * 1024
PET_SCENE_VIDEO_START_OFFSET_SECONDS = 0.1
SEEDANCE_PET_SCENE_VIDEO_START_OFFSET_SECONDS = 0.2
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
CONCRETE_HOME_PATTERN = re.compile(
    r"(?:\b(?:в|на|под|среди|у|между)\b|"
    r"нор|лес|руин|пещ|дом|гнезд|берег|склон|долин|полян|болот|"
    r"башн|храм|мост|дорог|ущел|луг|пустош|озер|рек|корн|дупл|"
    r"убежищ|логов|приют|туннел|зал|остров|ниш)",
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
    world = character_bible.get("world")
    home = world.get("home") if isinstance(world, dict) else None
    if not isinstance(home, str) or not CONCRETE_HOME_PATTERN.search(home):
        issues.append("home_is_not_a_concrete_location")
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
    fallback_model = get_character_model(settings)
    model = (
        fallback_model
        if client is not None
        else resolve_llm_model("character_bible", fallback_model)
    )
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
    completion = complete_chat("character_bible", request_kwargs, client=client)
    log_chat_completion_response(label, response_log_value(completion))
    content = completion.content or "{}"
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


STAGE_ROWS = ("baby", "teen", "adult")
STATE_COLUMNS = ("idle", "happy", "sad", "hungry")
FAST_GENERATION_STAGE = "teen"
FAST_GENERATION_STATES = ("idle",)
FAST_GENERATION_SKINS = tuple((FAST_GENERATION_STAGE, state) for state in FAST_GENERATION_STATES)
STATE_STRIP_STATES = ("idle", "happy", "sad")
FAST_GENERATION_STATE_FALLBACKS = {
    "idle": ("teen", "idle"),
    "happy": ("teen", "idle"),
    "sad": ("teen", "idle"),
    "hungry": ("teen", "idle"),
}
PET_SCENE_COMPOSITION_PROMPT = "Добавь персонажа с первой картинки на вторую в центр"
PET_SCENE_IMAGE_SIZE = "1024x1536"
PET_CHARACTER_REGION_SIZE = "480x720"
PET_CHARACTER_REGION_CENTER_Y_RATIO = 0.53125
PET_CHARACTER_REGION_FEATHER_RATIO = 0.04
PET_SCENE_BACKGROUND_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "backgrounds" / "pet-generation-forest.png"
)
PET_SCENE_VIDEO_PROMPT = (
    "Handcrafted stop-motion animation with a static locked camera and deliberately slow, "
    "restrained timing. Hold each pose for several frames so the motion has a tactile stepped "
    "cadence, like a puppet animated at about 6 frames per second and presented at 24 fps. "
    "The character remains perfectly still in the exact same pose, position, scale, "
    "composition, lighting, colors, facial expression, clothing, props, background, focus, "
    "depth of field and camera angle. The only action is exactly one slow natural blink: the "
    "eyelids close in three or four small stop-motion steps, hold closed for one short beat, "
    "then reopen in three or four small steps. Keep the eyes open for the rest of the clip. "
    "Do not move the pupils, head, body, ears, tail, mouth, nose, hands, clothing or any object. "
    "No expression change, camera motion, reframing, lighting change, color shift, morphing, "
    "new object or additional effect. Preserve the original image everywhere except the eyelids."
)
KANDINSKY_PET_SCENE_VIDEO_PROMPT = (
    "Статичная зафиксированная камера. Персонаж остаётся на том же месте, в том же масштабе и "
    "исходной позе. Он спокойно дышит: грудь, плечи и корпус едва заметно поднимаются и "
    "опускаются. "
    "Разрешены лёгкое естественное покачивание корпуса, небольшой перенос веса, медленный малый "
    "наклон или поворот головы и редкое моргание. Уши, хвост и свободные края одежды могут слегка "
    "следовать за движением тела. Движение плавное, сдержанное и цикличное, с возвращением в "
    "исходную позу. Ступни остаются на тех же точках опоры. Без ходьбы, прыжков, резких жестов, "
    "размахивания руками, речи и открывания рта. Не меняй анатомию, пропорции, лицо, одежду, "
    "предметы, фон, свет, цвета, фокус и глубину резкости. Без движения камеры, морфинга, новых "
    "объектов и дополнительных эффектов."
)
PET_SAD_SCENE_IMAGE_PROMPT = (
    "пусть персонаж сидит на земле и грустно плачет, но без видимых слёз\n"
    "больше ничего не меняй\n\n"
    "КРИТИЧЕСКИ ВАЖНО СОХРАНИТЬ КОМПОЗИЦИЮ: используй точно ту же камеру, дистанцию "
    "до персонажа, кадрирование, перспективу и размер персонажа в кадре. Не приближай "
    "персонажа, не увеличивай голову или тело, не делай портретный или крупный план. "
    "Голова персонажа должна остаться того же пиксельного размера, что на исходной "
    "картинке, а персонаж должен занимать не больше места в кадре, чем на исходнике. "
    "Сохрани точное положение камеры, фон, освещение, цвета, одежду и все предметы. "
    "Измени только позу персонажа на сидящую на земле и добавь грустный плач. "
    "Не рисуй слёзы, капли, влагу, мокрые дорожки на щеках или любую другую жидкость."
)
PET_SAD_SCENE_COMPOSITION_REFINEMENT_PROMPT = (
    "Первая картинка — единственный обязательный эталон композиции и масштаба. "
    "Сохрани из первой картинки точную камеру, кадрирование, перспективу, фон, "
    "положение персонажа, размер головы в пикселях и общий размер персонажа в кадре. "
    "Вторая картинка используется только как референс сидящей плачущей позы.\n\n"
    "Верни сцену в композиции первой картинки: персонаж должен находиться на той же "
    "дистанции от камеры и занимать не больше места, чем персонаж на первой картинке. "
    "Не приближай, не увеличивай, не делай крупный план. Персонаж сидит на земле и "
    "грустно плачет, как на второй картинке, но без видимых слёз. Не рисуй слёзы, "
    "капли, влагу, мокрые дорожки на щеках или любую другую жидкость. Сохрани дизайн, "
    "одежду, посох, книгу, флаконы, освещение и окружение первой картинки. Больше "
    "ничего не меняй."
)
PET_HAPPY_SCENE_IMAGE_PROMPT = (
    "Это фиксированный crop области персонажа. Сохрани его точные границы и координаты: "
    "не центрируй, не сдвигай, не масштабируй и не приближай персонажа внутри crop. "
    "Положение тела, ног, головы, головного убора, одежды и всех предметов должно остаться "
    "на тех же пиксельных координатах. Пусть персонаж лишь слегка приподнимет подбородок, "
    "чуть более жизнерадостно посмотрит и слегка естественно улыбнётся закрытым ртом. "
    "Сделай глаза немного более открытыми и живыми, сохранив их исходную форму. Не меняй "
    "фон внутри crop, дизайн, пропорции, освещение и цвета. Не открывай рот, не показывай "
    "зубы и не превращай эмоцию в широкую или мультяшную."
)
PET_HAPPY_SCENE_COMPOSITION_REFINEMENT_PROMPT = (
    "Обе картинки имеют одинаковые фиксированные границы области персонажа. Первая картинка — "
    "обязательный эталон всех пиксельных координат, масштаба, фона, дизайна и предметов. Вторая "
    "картинка используется только как референс эмоции. Верни crop с персонажем точно в положении "
    "первой картинки: не центрируй, не сдвигай, не масштабируй и не меняй позу тела. Перенеси "
    "со второй картинки только чуть приподнятый подбородок, немного более открытые и живые глаза "
    "и лёгкую естественную закрытую улыбку. Не изменяй размер головы или глаз, не открывай рот, "
    "не показывай зубы и не меняй ничего больше."
)
PET_SAD_SCENE_VIDEO_PROMPT = (
    "Static locked camera. The character remains perfectly still in the exact same pose, "
    "position, scale, composition, lighting, colors, clothing, props, background, focus, "
    "depth of field and camera angle. Do not move the head, body, ears, tail, mouth, nose, "
    "hands, clothing or any object. Do not change the environment or framing. The only "
    "animation is gentle tearless crying, expressed through occasional slight eyelid trembling. "
    "The eyes remain sorrowful. No visible tears, droplets, moisture, wet streaks on the cheeks, "
    "or any other liquid. No eye movement, no pupil movement, "
    "no head movement, no body movement, no camera motion, no lighting changes, no color "
    "shifts, no additional effects. Preserve every pixel of the original image except for "
    "the subtle eyelid trembling."
)
PET_SCENE_VIDEO_SIZE = "720x1280"
PET_SCENE_VIDEO_RESOLUTION = "480p"
PET_SCENE_VIDEO_ASPECT_RATIO = "9:16"
PET_SCENE_VIDEO_DURATION_SECONDS = 4
SPRITE_FOREGROUND_DISTANCE = 28
SPRITE_COMPONENT_DILATION_PX = 25
SPRITE_SEARCH_PADDING_X_RATIO = 0.2
SPRITE_SEARCH_PADDING_Y_RATIO = 0.45
SPRITE_CONTENT_PADDING_RATIO = 0.025
SPRITE_BOTTOM_PADDING_RATIO = 0.08


@dataclass(frozen=True)
class PetAssetImageSet:
    asset_set_id: uuid.UUID
    generated_paths: dict[tuple[str, str], tuple[Path, str]]
    scene_path: Path
    character_bible: dict[str, Any]
    version: int
    generated_at: datetime


PET_GENERATION_METADATA_FILENAME = ".generation.json"
PET_GENERATION_METADATA_SCHEMA_VERSION = 1


def generation_job_asset_set_id(job_id: str) -> uuid.UUID:
    """Return an asset id based only on the durable generation job identity."""
    try:
        return uuid.UUID(job_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"tamagochi:generation-job:{job_id}")


def comparison_asset_set_id(primary_asset_set_id: uuid.UUID | str) -> uuid.UUID:
    try:
        namespace = uuid.UUID(str(primary_asset_set_id))
    except ValueError:
        namespace = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"tamagochi:primary-asset-set:{primary_asset_set_id}",
        )
    return uuid.uuid5(namespace, "kandinsky-comparison")


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _is_valid_image_file(path: Path) -> bool:
    if not _is_nonempty_file(path):
        return False
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, UnidentifiedImageError):
        return False
    return True


def _atomic_write_nonempty(path: Path, payload: bytes) -> None:
    if not payload:
        raise OSError(f"Refusing to persist empty generated media: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def _pet_generation_metadata_path(asset_set_id: uuid.UUID) -> Path:
    return generated_dir_for(asset_set_id) / PET_GENERATION_METADATA_FILENAME


def _load_or_create_pet_generation_metadata(
    asset_set_id: uuid.UUID,
    description: str,
    image_provider: str | None,
    character_bible: str | dict[str, Any] | None,
) -> tuple[dict[str, Any], datetime, int]:
    metadata_path = _pet_generation_metadata_path(asset_set_id)
    provider = image_provider or "default"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            stored_bible = metadata["characterBible"]
            generated_at = datetime.fromisoformat(str(metadata["generatedAt"]))
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=UTC)
            if (
                metadata.get("schemaVersion") != PET_GENERATION_METADATA_SCHEMA_VERSION
                or metadata.get("assetSetId") != str(asset_set_id)
                or metadata.get("description") != description
                or metadata.get("imageProvider") != provider
                or not isinstance(stored_bible, dict)
            ):
                raise ValueError("pet generation metadata does not match this job")
            if character_bible is not None and character_bible != stored_bible:
                raise ValueError("pet generation character bible changed during resume")
            version = int(metadata["version"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid persisted pet generation metadata") from exc
        return stored_bible, generated_at, version

    resolved_bible = character_bible or create_character_bible(description)
    if not isinstance(resolved_bible, dict):
        raise ValueError("pet character bible must be an object")
    generated_at = datetime.now(UTC)
    version = int(generated_at.timestamp())
    metadata = {
        "schemaVersion": PET_GENERATION_METADATA_SCHEMA_VERSION,
        "assetSetId": str(asset_set_id),
        "description": description,
        "imageProvider": provider,
        "characterBible": resolved_bible,
        "generatedAt": generated_at.isoformat(),
        "version": version,
    }
    _atomic_write_nonempty(
        metadata_path,
        json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )
    return resolved_bible, generated_at, version


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
    states: tuple[str, ...] = STATE_STRIP_STATES,
) -> dict[tuple[str, str], Image.Image]:
    normalized = image.convert("RGBA")
    cell_width = normalized.width // len(states)
    cell_height = normalized.height
    output_side = min(cell_width, cell_height)
    cell_images: dict[tuple[str, str], Image.Image] = {}
    background_pixel = background_pixel_for(normalized)

    for col, state in enumerate(states):
        left = col * cell_width
        right = normalized.width if col == len(states) - 1 else left + cell_width
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


def _merge_string_lists(*values: Any, limit: int = 8) -> list[str]:
    result: list[str] = []
    for value in values:
        for text in _string_list(value, limit=limit):
            if text in result:
                continue
            result.append(text)
            if len(result) >= limit:
                return result
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
    genesis = _dict_value(bible.get("genesis"))
    roleplay_contract = _dict_value(bible.get("roleplay_contract"))
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
    roleplay_voice_rules = _string_list(roleplay_contract.get("voice_rules"), limit=7)
    genesis_description = _string_value(genesis.get("description")) or _string_value(
        genesis.get("core_reading")
    )
    character_trait = _string_value(genesis.get("character_trait")) or _string_value(
        genesis.get("central_trait")
    )
    genesis_likes = _string_list(genesis.get("likes"), limit=8)
    genesis_does = _string_list(genesis.get("does"), limit=8)
    appetite = _string_value(genesis.get("appetite")) or _string_value(
        genesis.get("safe_adaptation")
    )
    conflict = _string_value(genesis.get("conflict")) or _string_value(
        genesis.get("inner_conflict")
    )
    story_engine = _string_value(genesis.get("story_engine")) or _string_value(
        genesis.get("daily_life_hook")
    )
    voice_rules = _merge_string_lists(
        compact_voice.get("rules"),
        roleplay_voice_rules,
        limit=10,
    )
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
    genesis_personality = " ".join(
        text
        for text in (
            genesis_description,
            character_trait,
            conflict,
            story_engine,
        )
        if text
    )
    bible["personality"] = (
        _string_value(bible.get("personality"))
        or genesis_personality
        or " ".join(
            text
            for text in (
                _string_value(inner.get("core_want")),
                _string_value(inner.get("inner_conflict")),
            )
            if text
        )
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
        "core_want": _string_value(inner.get("core_want"))
        or story_engine
        or "; ".join(genesis_does[:3]),
        "inner_conflict": _string_value(inner.get("inner_conflict")) or conflict,
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
            "core_want": _string_value(inner.get("core_want"))
            or story_engine
            or "; ".join(genesis_does[:3]),
            "inner_conflict": _string_value(inner.get("inner_conflict")) or conflict,
            "likes": genesis_likes or objects[:3] + routines[:2],
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
    bible["genesis"] = {
        "description": genesis_description,
        "character_trait": character_trait,
        "likes": genesis_likes,
        "does": genesis_does,
        "appetite": appetite,
        "conflict": conflict,
        "story_engine": story_engine,
    }
    bible["roleplay_contract"] = {
        "self_intro": _string_value(roleplay_contract.get("self_intro")),
        "how_to_answer_who_are_you": _string_value(
            roleplay_contract.get("how_to_answer_who_are_you")
        ),
        "how_to_answer_what_do_you_eat": _string_value(
            roleplay_contract.get("how_to_answer_what_do_you_eat")
        ),
        "how_to_answer_where_do_you_live": _string_value(
            roleplay_contract.get("how_to_answer_where_do_you_live")
        ),
        "voice_rules": roleplay_voice_rules,
    }
    extensions = _dict_value(bible.get("extensions"))
    generation_meta = _dict_value(extensions.get("generation"))
    extensions["generation"] = {
        **generation_meta,
        "pipeline": "direct_creature_profile_v4",
        "usesDirectProfileQuestions": True,
    }
    bible["extensions"] = extensions
    return bible


def create_character_bible(user_description: str) -> dict[str, Any]:
    settings = get_settings()
    client = None
    system_message = {
        "role": "system",
        "content": character_bible_system_prompt(),
    }
    compact_bible = _character_bible_completion(
        client,
        settings,
        "pet_creation/character_bible",
        [
            system_message,
            {
                "role": "user",
                "content": build_character_bible_prompt(user_description),
            },
        ],
    )
    character_bible = expand_compact_character_bible(
        compact_bible,
        raw_description=user_description,
    )
    character_bible = upgrade_character_bible_v2(
        character_bible,
        raw_description=user_description,
    )
    issues = character_bible_quality_issues(user_description, character_bible)
    if issues:
        logger.info("Compact character bible quality flags: %s", issues)
        compact_bible = _character_bible_completion(
            client,
            settings,
            "pet_creation/character_bible_repair",
            [
                system_message,
                {
                    "role": "user",
                    "content": (
                        f"{build_character_bible_prompt(user_description)}\n\n"
                        "Repair this character bible before returning the complete JSON. "
                        f"Quality issues: {', '.join(issues)}. Preserve the user's core idea, "
                        "but replace generic defaults and incoherent physical or sensory logic.\n\n"
                        f"CURRENT_JSON:\n{json.dumps(compact_bible, ensure_ascii=False)}"
                    ),
                },
            ],
        )
        character_bible = expand_compact_character_bible(
            compact_bible,
            raw_description=user_description,
        )
        character_bible = upgrade_character_bible_v2(
            character_bible,
            raw_description=user_description,
        )
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


def build_image_edit_kwargs(
    settings: Any,
    prompt: str,
    *,
    model: str | None = None,
    size: str | None = None,
) -> dict[str, Any]:
    return {
        "model": model or get_image_model(settings),
        "prompt": prompt,
        "size": size or settings.openai_image_size,
        "quality": settings.openai_image_quality,
        "n": 1,
        "output_format": settings.openai_image_output_format,
        "timeout": settings.openai_image_timeout_seconds,
    }


def _decode_bounded_base64_result(payload: Any) -> bytes:
    max_encoded_bytes = 4 * ((IMAGE_RESULT_MAX_BYTES + 2) // 3)
    if isinstance(payload, str):
        if len(payload) > max_encoded_bytes:
            raise MediaResultError("IMAGE_RESULT_TOO_LARGE")
        try:
            encoded = payload.encode("ascii")
        except UnicodeEncodeError as exc:
            raise MediaResultError("IMAGE_RESULT_BASE64_INVALID") from exc
    elif isinstance(payload, bytes):
        if len(payload) > max_encoded_bytes:
            raise MediaResultError("IMAGE_RESULT_TOO_LARGE")
        encoded = payload
    else:
        raise MediaResultError("IMAGE_RESULT_BASE64_INVALID")

    encoded = b"".join(encoded.split())
    try:
        result = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise MediaResultError("IMAGE_RESULT_BASE64_INVALID") from exc
    if len(result) > IMAGE_RESULT_MAX_BYTES:
        raise MediaResultError("IMAGE_RESULT_TOO_LARGE")
    return result


def _validate_provider_image_result(image_bytes: bytes) -> bytes:
    """Validate untrusted provider bytes without decoding the full pixel buffer."""

    if not image_bytes:
        raise MediaResultError("IMAGE_RESULT_INVALID")
    if len(image_bytes) > IMAGE_RESULT_MAX_BYTES:
        raise MediaResultError("IMAGE_RESULT_TOO_LARGE")
    try:
        # Pillow's default bomb threshold is deliberately permissive and initially
        # emits only a warning. Treat that warning as an error, then enforce the
        # considerably smaller limit required by this service before any convert/load.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > IMAGE_RESULT_MAX_DIMENSION
                    or height > IMAGE_RESULT_MAX_DIMENSION
                    or width * height > IMAGE_RESULT_MAX_PIXELS
                ):
                    raise MediaResultError("IMAGE_RESULT_DIMENSIONS_EXCEEDED")
                image.verify()
    except MediaResultError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MediaResultError("IMAGE_RESULT_DIMENSIONS_EXCEEDED") from exc
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise MediaResultError("IMAGE_RESULT_INVALID") from exc
    return image_bytes


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", {})
    return str(headers.get("content-type", "")).partition(";")[0].strip().lower()


def _validate_streamed_result_headers(
    response: Any,
    *,
    max_bytes: int,
    too_large_code: str,
    invalid_content_type_code: str,
    content_type_prefix: str,
) -> None:
    content_type = _response_content_type(response)
    binary_content_types = {"application/octet-stream", "binary/octet-stream"}
    if content_type and not (
        content_type.startswith(content_type_prefix) or content_type in binary_content_types
    ):
        raise MediaResultError(invalid_content_type_code)

    headers = getattr(response, "headers", {})
    content_length = headers.get("content-length")
    if content_length is None:
        return
    try:
        declared_bytes = int(content_length)
    except (TypeError, ValueError):
        return
    if declared_bytes > max_bytes:
        raise MediaResultError(too_large_code)


def _read_streamed_result_bytes(
    response: Any,
    *,
    max_bytes: int,
    too_large_code: str,
    invalid_content_type_code: str,
    content_type_prefix: str,
) -> bytes:
    _validate_streamed_result_headers(
        response,
        max_bytes=max_bytes,
        too_large_code=too_large_code,
        invalid_content_type_code=invalid_content_type_code,
        content_type_prefix=content_type_prefix,
    )
    result = bytearray()
    for chunk in response.iter_bytes(chunk_size=MEDIA_RESULT_STREAM_CHUNK_BYTES):
        if len(result) + len(chunk) > max_bytes:
            raise MediaResultError(too_large_code)
        result.extend(chunk)
    return bytes(result)


def _read_streamed_error_preview(response: Any) -> str:
    result = bytearray()
    for chunk in response.iter_bytes(chunk_size=MEDIA_RESULT_STREAM_CHUNK_BYTES):
        remaining = MEDIA_RESULT_ERROR_PREVIEW_MAX_BYTES - len(result)
        if remaining <= 0:
            break
        result.extend(chunk[:remaining])
        if len(chunk) > remaining:
            break
    return result.decode("utf-8", errors="replace")


def _is_global_unicast_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _validated_provider_image_result_url(image_url: Any) -> str:
    if not isinstance(image_url, str):
        raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED")
    result_url = image_url.strip()
    try:
        parsed = urlsplit(result_url)
        port = parsed.port
    except ValueError as exc:
        raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
    ):
        raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED")

    hostname = parsed.hostname.rstrip(".")
    if not hostname:
        raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            address_info = socket.getaddrinfo(
                hostname,
                port or 443,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED") from exc
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for family, _socket_type, _protocol, _canonical_name, socket_address in address_info:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            try:
                addresses.add(ipaddress.ip_address(socket_address[0]))
            except ValueError as exc:
                raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED") from exc
        if not addresses or any(not _is_global_unicast_address(address) for address in addresses):
            raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED") from None
    else:
        raise MediaResultError("IMAGE_RESULT_URL_UNTRUSTED")
    return result_url


def _download_image_result_bytes(image_url: str) -> bytes:
    validated_url = _validated_provider_image_result_url(image_url)
    with httpx.stream(
        "GET",
        validated_url,
        timeout=60,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        result = _read_streamed_result_bytes(
            response,
            max_bytes=IMAGE_RESULT_MAX_BYTES,
            too_large_code="IMAGE_RESULT_TOO_LARGE",
            invalid_content_type_code="IMAGE_RESULT_CONTENT_TYPE_INVALID",
            content_type_prefix="image/",
        )
    if not result:
        raise RuntimeError("IMAGE_RESPONSE_EMPTY")
    return result


def _image_result_bytes(first: Any) -> bytes:
    b64_json = (
        first.get("b64_json") if isinstance(first, dict) else getattr(first, "b64_json", None)
    )
    if b64_json:
        return _validate_provider_image_result(_decode_bounded_base64_result(b64_json))

    image_url = first.get("url") if isinstance(first, dict) else getattr(first, "url", None)
    if image_url:
        return _validate_provider_image_result(_download_image_result_bytes(image_url))

    raise RuntimeError("IMAGE_RESPONSE_EMPTY")


def _clean_setting_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _provider_task_operation(kind: str, label: str) -> str:
    return f"{kind}:{label}"


def _provider_account_namespace(settings: Any, provider: str) -> str:
    if provider == "openrouter":
        credential = get_openrouter_api_key(settings)
        configured = _clean_setting_string(getattr(settings, "openrouter_account_namespace", None))
    elif provider == "kandinsky":
        credential = _kandinsky_api_key(settings)
        configured = _clean_setting_string(getattr(settings, "kandinsky_account_namespace", None))
    else:
        raise ValueError(f"unsupported provider account namespace: {provider}")
    if configured:
        return f"configured:{configured}"
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    return f"credential-sha256:{digest}"


def _is_definitely_not_accepted_submit_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        MissingKandinskyAPIKey
        | MissingOpenAIAPIKey
        | httpx.ConnectError
        | httpx.ConnectTimeout
        | httpx.PoolTimeout
        | httpx.HTTPStatusError,
    )


def _implicit_provider_task_store_path(settings: Any) -> str | None:
    if has_current_provider_task_scope():
        return None
    configured = _clean_setting_string(
        getattr(settings, "provider_task_receipt_store_path", None)
    ) or _clean_setting_string(os.environ.get("PROVIDER_TASK_RECEIPT_STORE_PATH"))
    if not configured:
        raise RuntimeError("PROVIDER_TASK_RECEIPT_STORE_PATH_REQUIRED")
    return configured


def _implicit_provider_task_store_max_records(settings: Any) -> int:
    return int(getattr(settings, "provider_task_receipt_store_max_records", 100_000))


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


def _is_safe_paid_submit_retry_error(exc: Exception) -> bool:
    """Return true only when the paid request was not sent, or was explicitly rejected."""
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    return False


def _kandinsky_retry_delay(attempt_index: int) -> float:
    return KANDINSKY_HTTP_RETRY_SECONDS[min(attempt_index, len(KANDINSKY_HTTP_RETRY_SECONDS) - 1)]


def _kandinsky_with_retry(
    label: str,
    operation: str,
    call: Callable[[], Any],
    *,
    is_retryable: Callable[[Exception], bool] = _is_retryable_kandinsky_error,
) -> Any:
    for attempt_index in range(KANDINSKY_HTTP_MAX_ATTEMPTS):
        try:
            response = call()
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                is_last_attempt = attempt_index == KANDINSKY_HTTP_MAX_ATTEMPTS - 1
                if is_last_attempt or not is_retryable(exc):
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
            if is_last_attempt or not is_retryable(exc):
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


REFERENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
GENERATED_ASSET_ROOT = Path(__file__).resolve().parents[2] / "static" / "generated"


def _bounded_data_image_bytes(image_url: str) -> bytes:
    header, separator, payload = image_url.partition(",")
    if not separator or not payload:
        return b""
    cleaned = payload.replace("\n", "").replace("\r", "").strip()
    if ";base64" in header:
        if len(cleaned) > ((REFERENCE_IMAGE_MAX_BYTES + 2) // 3) * 4:
            raise RuntimeError("REFERENCE_IMAGE_TOO_LARGE")
        result = base64.b64decode(cleaned, validate=True)
    else:
        result = cleaned.encode("utf-8")
    if len(result) > REFERENCE_IMAGE_MAX_BYTES:
        raise RuntimeError("REFERENCE_IMAGE_TOO_LARGE")
    return result


def _download_reference_image_bytes(image_url: str) -> bytes:
    with httpx.stream(
        "GET",
        image_url,
        timeout=30,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not content_type.startswith("image/"):
            raise RuntimeError("REFERENCE_IMAGE_CONTENT_TYPE_INVALID")
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > REFERENCE_IMAGE_MAX_BYTES:
                    raise RuntimeError("REFERENCE_IMAGE_TOO_LARGE")
            except ValueError:
                pass

        result = bytearray()
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            result.extend(chunk)
            if len(result) > REFERENCE_IMAGE_MAX_BYTES:
                raise RuntimeError("REFERENCE_IMAGE_TOO_LARGE")
        return bytes(result)


def _local_reference_image_bytes(image_url: str) -> bytes | None:
    path = urlsplit(image_url).path
    prefix = "/static/generated/"
    if not path.startswith(prefix):
        return None
    relative_parts = Path(path.removeprefix(prefix)).parts
    if not relative_parts or any(
        part in {"", ".", ".."} or part.startswith(".") for part in relative_parts
    ):
        raise RuntimeError("REFERENCE_IMAGE_PATH_INVALID")

    root = GENERATED_ASSET_ROOT.resolve()
    candidate = root.joinpath(*relative_parts)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("REFERENCE_IMAGE_PATH_INVALID") from exc
    if not resolved.is_file():
        raise RuntimeError("REFERENCE_IMAGE_PATH_INVALID")
    if resolved.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
        raise RuntimeError("REFERENCE_IMAGE_TOO_LARGE")
    result = resolved.read_bytes()
    if len(result) > REFERENCE_IMAGE_MAX_BYTES:
        raise RuntimeError("REFERENCE_IMAGE_TOO_LARGE")
    return result


def _reference_image_bytes(image_url: str) -> bytes:
    if image_url.startswith("data:image/"):
        return _bounded_data_image_bytes(image_url)

    settings = get_settings()
    trusted_url = trusted_generated_asset_url(image_url, settings)
    if not trusted_url:
        raise RuntimeError("REFERENCE_IMAGE_URL_UNTRUSTED")

    local_bytes = _local_reference_image_bytes(trusted_url)
    if local_bytes is not None:
        return local_bytes

    fetch_url = _internal_reference_image_url(trusted_url)
    try:
        return _download_reference_image_bytes(fetch_url)
    except httpx.HTTPError:
        if fetch_url == trusted_url:
            raise

    return _download_reference_image_bytes(trusted_url)


def _internal_reference_image_url(image_url: str) -> str:
    settings = get_settings()
    internal_url = _clean_setting_string(getattr(settings, "backend_internal_url", None))
    if not internal_url:
        return image_url

    source = urlsplit(image_url)
    internal = urlsplit(internal_url)
    if source.scheme not in {"http", "https"} or not source.netloc:
        return image_url
    if internal.scheme not in {"http", "https"} or not internal.netloc:
        return image_url

    public_origins = {
        (parsed.scheme.lower(), parsed.netloc.lower())
        for value in (
            getattr(settings, "backend_public_url", None),
            getattr(settings, "webapp_url", None),
        )
        if (parsed := urlsplit(_clean_setting_string(value))) and parsed.scheme and parsed.netloc
    }
    if (source.scheme.lower(), source.netloc.lower()) not in public_origins:
        return image_url

    return internal._replace(path=source.path, query=source.query, fragment="").geturl()


def _openai_reference_image_files(
    input_references: list[dict[str, Any]] | None,
) -> list[BytesIO]:
    image_files: list[BytesIO] = []
    for index, reference in enumerate(input_references or []):
        image_url = _reference_url_from_entry(reference)
        if not image_url:
            continue
        image_bytes = _reference_image_bytes(image_url)
        if not image_bytes:
            continue
        with Image.open(BytesIO(image_bytes)) as source:
            normalized = source.convert("RGBA")
            image_file = BytesIO()
            normalized.save(image_file, format="PNG")
        image_file.name = f"reference-{index + 1}.png"
        image_file.seek(0)
        image_files.append(image_file)
        if len(image_files) == 4:
            break
    return image_files


def _kandinsky_reference_image_b64(image_url: str) -> str:
    image_bytes = _reference_image_bytes(image_url)
    if not image_bytes:
        return ""
    settings = get_settings()
    max_side = int(getattr(settings, "kandinsky_reference_max_side", 1280))
    jpeg_quality = int(getattr(settings, "kandinsky_reference_jpeg_quality", 85))
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                rgba = source.convert("RGBA")
                normalized = Image.new("RGB", rgba.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = source.convert("RGB")
            output = BytesIO()
            normalized.save(
                output,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
            )
            image_bytes = output.getvalue()
    except (OSError, UnidentifiedImageError):
        logger.warning("kandinsky_reference_normalization_skipped invalid image payload")
    return base64.b64encode(image_bytes).decode("utf-8")


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


def _compact_kandinsky_prompt(prompt: str, *, task: str = "default") -> str:
    compacted = adapt_kandinsky_prompt(prompt, task=task)
    if compacted != prompt.strip():
        logger.info(
            "kandinsky_image_prompt_adapted task=%s originalChars=%s adaptedChars=%s",
            task,
            len(prompt.strip()),
            len(compacted),
        )
    return compacted


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
        is_retryable=_is_safe_paid_submit_retry_error,
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


def _kandinsky_wait_done(
    settings: Any,
    *,
    task_id: str,
    label: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    url = f"{_kandinsky_base_url(settings)}/tasks/{task_id}"
    headers = _kandinsky_headers(settings)
    timeout_seconds = max(
        1.0,
        float(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "openai_image_timeout_seconds", 180)
        ),
    )
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
            raise TimeoutError(f"Kandinsky task timed out: {task_id}")
        time.sleep(min(poll_seconds, remaining_seconds))


def _kandinsky_download_result(
    settings: Any,
    *,
    task_id: str,
    label: str,
    result_kind: str = "image",
) -> bytes:
    if result_kind == "image":
        max_bytes = IMAGE_RESULT_MAX_BYTES
        too_large_code = "IMAGE_RESULT_TOO_LARGE"
        invalid_content_type_code = "IMAGE_RESULT_CONTENT_TYPE_INVALID"
        content_type_prefix = "image/"
        empty_response_code = "KANDINSKY_IMAGE_RESPONSE_EMPTY"
    elif result_kind == "video":
        max_bytes = VIDEO_RESULT_MAX_BYTES
        too_large_code = "VIDEO_RESULT_TOO_LARGE"
        invalid_content_type_code = "VIDEO_RESULT_CONTENT_TYPE_INVALID"
        content_type_prefix = "video/"
        empty_response_code = "KANDINSKY_VIDEO_RESPONSE_EMPTY"
    else:
        raise ValueError(f"Unsupported Kandinsky result kind: {result_kind}")

    url = f"{_kandinsky_base_url(settings)}/tasks/{task_id}/result"
    timeout_seconds = max(1.0, float(getattr(settings, "openai_image_timeout_seconds", 180)))
    retry_window = min(KANDINSKY_RESULT_RETRY_WINDOW_SECONDS, timeout_seconds)
    deadline = time.monotonic() + retry_window
    while True:
        retry_censor_delay: float | None = None
        for attempt_index in range(KANDINSKY_HTTP_MAX_ATTEMPTS):
            status_code: int | None = None
            response_preview = ""
            try:
                with httpx.stream(
                    "GET",
                    url,
                    headers=_kandinsky_headers(settings),
                    timeout=_kandinsky_http_timeout(settings),
                    follow_redirects=False,
                ) as response:
                    status_code = response.status_code
                    if status_code >= 400:
                        response_preview = _read_streamed_error_preview(response)
                    normalized_preview = response_preview.lower()
                    remaining_seconds = deadline - time.monotonic()
                    retryable_censor_error = status_code == 422 and (
                        "output censor service unavailable" in normalized_preview
                        or "gigachat returned no response" in normalized_preview
                    )
                    if retryable_censor_error and remaining_seconds > 0:
                        retry_censor_delay = min(
                            KANDINSKY_RESULT_RETRY_INTERVAL_SECONDS,
                            remaining_seconds,
                        )
                        logger.warning(
                            "kandinsky_image_result retry label=%s task_id=%s "
                            "retryDelaySeconds=%s response=%s",
                            label,
                            task_id,
                            retry_censor_delay,
                            response_preview[:1000],
                        )
                        break

                    response.raise_for_status()
                    result = _read_streamed_result_bytes(
                        response,
                        max_bytes=max_bytes,
                        too_large_code=too_large_code,
                        invalid_content_type_code=invalid_content_type_code,
                        content_type_prefix=content_type_prefix,
                    )
                    if not result:
                        raise RuntimeError(empty_response_code)
                    if result_kind == "image":
                        return _validate_provider_image_result(result)
                    return result
            except Exception as exc:
                is_last_attempt = attempt_index == KANDINSKY_HTTP_MAX_ATTEMPTS - 1
                if not is_last_attempt and _is_retryable_kandinsky_error(exc):
                    retry_delay = _kandinsky_retry_delay(attempt_index)
                    logger.warning(
                        "kandinsky_image_result retry label=%s attempt=%s maxAttempts=%s "
                        "retryDelaySeconds=%s errorType=%s status=%s response=%s",
                        label,
                        attempt_index + 1,
                        KANDINSKY_HTTP_MAX_ATTEMPTS,
                        retry_delay,
                        type(exc).__name__,
                        status_code,
                        response_preview[:1000],
                    )
                    time.sleep(retry_delay)
                    continue
                if isinstance(exc, httpx.HTTPStatusError):
                    logger.error(
                        "kandinsky_image_result failed label=%s task_id=%s status=%s response=%s",
                        label,
                        task_id,
                        status_code,
                        response_preview[:2000],
                    )
                raise

        if retry_censor_delay is None:
            raise RuntimeError("unreachable kandinsky result retry state")
        time.sleep(retry_censor_delay)


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
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> bytes:
    settings = get_settings()
    provider_prompt = _compact_kandinsky_prompt(prompt, task=label)
    reference_images = _kandinsky_reference_images(input_references)
    if reference_images:
        task_type = _clean_setting_string(getattr(settings, "kandinsky_i2i_task_type", None))
        task_type = task_type or "k6-i2i"
        params: dict[str, Any] = {
            "image": reference_images,
            "query": provider_prompt,
        }
    else:
        task_type = _clean_setting_string(getattr(settings, "kandinsky_t2i_task_type", None))
        task_type = task_type or "k6-image-t2i"
        params = {
            "query": provider_prompt,
            "resolution": (
                _clean_setting_string(size)
                or (
                    _clean_setting_string(getattr(settings, "kandinsky_pet_image_resolution", None))
                    if label == "pet_creation/image"
                    else ""
                )
                or _clean_setting_string(getattr(settings, "kandinsky_image_resolution", None))
                or "1280x768"
            ),
        }

    request_kwargs = {
        "model": f"kandinsky/{task_type}",
        "prompt": provider_prompt,
        "resolution": params.get("resolution"),
        "n": 1,
        "input_references": input_references or [],
        "timeout": getattr(settings, "openai_image_timeout_seconds", 180),
    }
    log_image_generation_prompt(label, request_kwargs)
    operation = _provider_task_operation("image", label)
    provider_origin = _kandinsky_base_url(settings)
    account_namespace = _provider_account_namespace(settings, "kandinsky")
    payload_fingerprint = provider_task_payload_fingerprint(
        {
            "task_type": task_type,
            "params": params,
        }
    )
    with implicit_provider_task_scope(
        _implicit_provider_task_store_path(settings),
        max_records=_implicit_provider_task_store_max_records(settings),
        operation=operation,
        provider="kandinsky",
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    ):
        receipt = find_current_provider_task(
            operation=operation,
            provider="kandinsky",
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            payload_fingerprint=payload_fingerprint,
        )
        if receipt is None:
            try:
                task_id = _kandinsky_create_task(
                    settings,
                    task_type=task_type,
                    params=params,
                    label=label,
                )
            except Exception as exc:
                if _is_definitely_not_accepted_submit_error(exc):
                    release_current_provider_task_admission(operation)
                raise
            save_current_provider_task(
                operation=operation,
                provider="kandinsky",
                provider_origin=provider_origin,
                account_namespace=account_namespace,
                task_id=task_id,
                polling_url=f"{provider_origin}/tasks/{task_id}",
                payload_fingerprint=payload_fingerprint,
            )
        else:
            task_id = receipt.task_id
            logger.info(
                "Kandinsky image task resumed label=%s taskId=%s state=%s",
                label,
                task_id,
                receipt.state,
            )
        try:
            status_payload = _kandinsky_wait_done(settings, task_id=task_id, label=label)
            image_bytes = _kandinsky_download_result(
                settings,
                task_id=task_id,
                label=label,
                result_kind="image",
            )
        except KandinskyTaskError:
            mark_current_provider_task_failed(operation)
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                mark_current_provider_task_failed(operation)
            raise
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


def _kandinsky_source_image_b64(image_bytes: bytes) -> str:
    settings = get_settings()
    jpeg_quality = int(getattr(settings, "kandinsky_reference_jpeg_quality", 85))
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                rgba = source.convert("RGBA")
                normalized = Image.new("RGB", rgba.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = source.convert("RGB")
            output = BytesIO()
            normalized.save(
                output,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
            )
            image_bytes = output.getvalue()
    except (OSError, UnidentifiedImageError):
        logger.warning("kandinsky_video_source_normalization_skipped invalid image payload")
    return base64.b64encode(image_bytes).decode("utf-8")


def generate_kandinsky_video_from_image_bytes(
    image_bytes: bytes,
    *,
    label: str,
    prompt: str,
) -> bytes:
    if not image_bytes:
        raise ValueError("image_bytes must not be empty")
    settings = get_settings()
    task_type = _clean_setting_string(getattr(settings, "kandinsky_i2v_task_type", None))
    task_type = task_type or "k5-i2v-hd"
    encoded_image = _kandinsky_source_image_b64(image_bytes)
    params = {
        "query": prompt.strip(),
        "image": encoded_image,
        "beautificator": "disabled",
    }
    timeout_seconds = max(
        1.0,
        float(getattr(settings, "kandinsky_video_timeout_seconds", 900)),
    )
    log_video_generation_prompt(
        label,
        {
            "model": f"kandinsky/{task_type}",
            "prompt": prompt,
            "has_source_image": True,
        },
    )
    logger.info(
        "Kandinsky video generation requested label=%s taskType=%s",
        label,
        task_type,
    )
    operation = _provider_task_operation("video", label)
    provider_origin = _kandinsky_base_url(settings)
    account_namespace = _provider_account_namespace(settings, "kandinsky")
    payload_fingerprint = provider_task_payload_fingerprint(
        {
            "task_type": task_type,
            "params": params,
        }
    )
    with implicit_provider_task_scope(
        _implicit_provider_task_store_path(settings),
        max_records=_implicit_provider_task_store_max_records(settings),
        operation=operation,
        provider="kandinsky",
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    ):
        receipt = find_current_provider_task(
            operation=operation,
            provider="kandinsky",
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            payload_fingerprint=payload_fingerprint,
        )
        if receipt is None:
            try:
                task_id = _kandinsky_create_task(
                    settings,
                    task_type=task_type,
                    params=params,
                    label=label,
                )
            except Exception as exc:
                if _is_definitely_not_accepted_submit_error(exc):
                    release_current_provider_task_admission(operation)
                raise
            save_current_provider_task(
                operation=operation,
                provider="kandinsky",
                provider_origin=provider_origin,
                account_namespace=account_namespace,
                task_id=task_id,
                polling_url=f"{provider_origin}/tasks/{task_id}",
                payload_fingerprint=payload_fingerprint,
            )
        else:
            task_id = receipt.task_id
            logger.info(
                "Kandinsky video task resumed label=%s taskId=%s state=%s",
                label,
                task_id,
                receipt.state,
            )
        try:
            _kandinsky_wait_done(
                settings,
                task_id=task_id,
                label=label,
                timeout_seconds=timeout_seconds,
            )
            video_bytes = _kandinsky_download_result(
                settings,
                task_id=task_id,
                label=label,
                result_kind="video",
            )
        except KandinskyTaskError:
            mark_current_provider_task_failed(operation)
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                mark_current_provider_task_failed(operation)
            raise
        logger.info(
            "Kandinsky video generation completed label=%s taskId=%s resultBytes=%s",
            label,
            task_id,
            len(video_bytes),
        )
        return video_bytes


def _image_request(
    prompt: str,
    *,
    label: str = "pet_creation/image",
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
    provider: str | None,
) -> ImageRequest:
    return ImageRequest(
        prompt=prompt,
        task=label,
        size=size,
        input_references=tuple(input_references or ()),
        provider=provider,
    )


def generate_image_bytes(
    prompt: str,
    *,
    label: str = "pet_creation/image",
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
    provider: str | None = None,
) -> bytes:
    return get_media_gateway().generate_image(
        _image_request(
            prompt,
            label=label,
            size=size,
            input_references=input_references,
            provider=provider,
        )
    )


@contextmanager
def reserve_image_bytes(
    prompt: str,
    *,
    label: str = "pet_creation/image",
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
    provider: str | None = None,
) -> Iterator[bytes]:
    with get_media_gateway().generate_image_reserved(
        _image_request(
            prompt,
            label=label,
            size=size,
            input_references=input_references,
            provider=provider,
        )
    ) as payload:
        yield payload


def generate_openai_image_bytes(
    prompt: str,
    *,
    label: str,
    size: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
) -> bytes:
    settings = get_settings()
    # This synchronous endpoint exposes neither a durable task id nor a polling API.
    # A local receipt therefore cannot close the accepted-request/response-lost window.
    # Disable SDK retries so an ambiguous response is not automatically charged twice.
    client = get_openai_platform_client().with_options(max_retries=0)
    reference_files = _openai_reference_image_files(input_references)
    if reference_files:
        kwargs = build_image_edit_kwargs(settings, prompt, size=size)
        log_image_generation_prompt(
            label,
            {**kwargs, "input_references": input_references or []},
        )
        image_input: BytesIO | list[BytesIO]
        image_input = reference_files[0] if len(reference_files) == 1 else reference_files
        try:
            response = client.images.edit(**kwargs, image=image_input)
        finally:
            for image_file in reference_files:
                image_file.close()
        response_payload = response.model_dump() if hasattr(response, "model_dump") else {}
        log_image_generation_response(label, kwargs, response_payload)
        return _image_result_bytes(response.data[0])

    kwargs = build_image_generate_kwargs(settings, prompt, size=size)
    log_image_generation_prompt(label, kwargs)
    response = client.images.generate(**kwargs)
    response_payload = response.model_dump() if hasattr(response, "model_dump") else {}
    log_image_generation_response(label, kwargs, response_payload)
    return _image_result_bytes(response.data[0])


def _image_path_data_url(path: Path) -> str:
    return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode('utf-8')}"


def generate_image_edit_bytes(
    prompt: str,
    source_path: Path,
    *,
    label: str,
    size: str | None = None,
    provider: str | None = None,
) -> bytes:
    input_references = [
        {
            "type": "image_url",
            "image_url": {"url": _image_path_data_url(source_path)},
        }
    ]
    return generate_image_bytes(
        prompt,
        label=label,
        size=size,
        input_references=input_references,
        provider=provider,
    )


@contextmanager
def reserve_image_edit_bytes(
    prompt: str,
    source_path: Path,
    *,
    label: str,
    size: str | None = None,
    provider: str | None = None,
) -> Iterator[bytes]:
    input_references = [
        {
            "type": "image_url",
            "image_url": {"url": _image_path_data_url(source_path)},
        }
    ]
    with reserve_image_bytes(
        prompt,
        label=label,
        size=size,
        input_references=input_references,
        provider=provider,
    ) as payload:
        yield payload
        mark_current_provider_task_media_saved(_provider_task_operation("image", label))


def generate_multi_image_edit_bytes(
    prompt: str,
    source_paths: list[Path],
    *,
    label: str,
    size: str | None = None,
    provider: str | None = None,
) -> bytes:
    input_references = [
        {
            "type": "image_url",
            "image_url": {"url": _image_path_data_url(source_path)},
        }
        for source_path in source_paths
    ]
    return generate_image_bytes(
        prompt,
        label=label,
        size=size,
        input_references=input_references,
        provider=provider,
    )


@contextmanager
def reserve_multi_image_edit_bytes(
    prompt: str,
    source_paths: list[Path],
    *,
    label: str,
    size: str | None = None,
    provider: str | None = None,
) -> Iterator[bytes]:
    input_references = [
        {
            "type": "image_url",
            "image_url": {"url": _image_path_data_url(source_path)},
        }
        for source_path in source_paths
    ]
    with reserve_image_bytes(
        prompt,
        label=label,
        size=size,
        input_references=input_references,
        provider=provider,
    ) as payload:
        yield payload
        mark_current_provider_task_media_saved(_provider_task_operation("image", label))


def _openrouter_video_headers(settings: Any) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_openrouter_api_key(settings)}",
        "Content-Type": "application/json",
        **get_openrouter_headers(settings),
    }


def _openrouter_video_content_headers(settings: Any) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_openrouter_api_key(settings)}",
        **get_openrouter_headers(settings),
    }


def _trusted_openrouter_polling_url(settings: Any, polling_url: str) -> str:
    base_url = get_openrouter_video_url(settings)
    resolved_url = urljoin(f"{base_url.rstrip('/')}/", polling_url.strip())
    base = urlsplit(base_url)
    resolved = urlsplit(resolved_url)
    if (
        resolved.scheme.lower() != base.scheme.lower()
        or resolved.netloc.lower() != base.netloc.lower()
        or resolved.username is not None
        or resolved.password is not None
    ):
        raise RuntimeError("OPENROUTER_VIDEO_POLL_URL_UNTRUSTED")
    return resolved_url


def _openrouter_video_error(response: httpx.Response) -> OpenRouterVideoHTTPError:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return OpenRouterVideoHTTPError(response.status_code, payload)


def _is_safe_openrouter_video_submit_response_retry(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code not in {502, 503, 504}:
        return False
    try:
        payload_text = json.dumps(response.json(), ensure_ascii=False, default=str)
    except ValueError:
        payload_text = response.text
    normalized = payload_text.casefold()
    return any(
        marker in normalized
        for marker in (
            "upstream connect error",
            "connection refused",
            "failed to connect to upstream",
            "no healthy upstream",
        )
    )


def _submit_openrouter_video_job(
    settings: Any,
    payload: dict[str, Any],
    *,
    label: str,
) -> httpx.Response:
    for attempt_index in range(OPENROUTER_VIDEO_HTTP_MAX_ATTEMPTS):
        try:
            response = httpx.post(
                get_openrouter_video_url(settings),
                headers=_openrouter_video_headers(settings),
                json=payload,
                timeout=60,
            )
        except httpx.TransportError as exc:
            is_last_attempt = attempt_index == OPENROUTER_VIDEO_HTTP_MAX_ATTEMPTS - 1
            if is_last_attempt or not _is_safe_paid_submit_retry_error(exc):
                raise
            retry_delay = OPENROUTER_VIDEO_HTTP_RETRY_SECONDS[attempt_index]
            logger.warning(
                "openrouter_video_submit retry label=%s attempt=%s maxAttempts=%s "
                "retryDelaySeconds=%s errorType=%s error=%s",
                label,
                attempt_index + 1,
                OPENROUTER_VIDEO_HTTP_MAX_ATTEMPTS,
                retry_delay,
                type(exc).__name__,
                str(exc),
            )
            time.sleep(retry_delay)
            continue

        is_last_attempt = attempt_index == OPENROUTER_VIDEO_HTTP_MAX_ATTEMPTS - 1
        is_retryable = _is_safe_openrouter_video_submit_response_retry(response)
        if response.status_code < 400 or is_last_attempt or not is_retryable:
            return response

        retry_delay = OPENROUTER_VIDEO_HTTP_RETRY_SECONDS[attempt_index]
        logger.warning(
            "openrouter_video_submit retry label=%s attempt=%s maxAttempts=%s "
            "retryDelaySeconds=%s error=%s",
            label,
            attempt_index + 1,
            OPENROUTER_VIDEO_HTTP_MAX_ATTEMPTS,
            retry_delay,
            _openrouter_video_error(response),
        )
        time.sleep(retry_delay)

    raise RuntimeError("unreachable OpenRouter video retry state")


def _poll_openrouter_video_job(
    settings: Any,
    job_id: str,
    *,
    polling_url: str | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(settings.openrouter_video_timeout_seconds)
    poll_interval = max(1.0, float(settings.openrouter_video_poll_interval_seconds))
    poll_url = polling_url or f"{get_openrouter_video_url(settings)}/{job_id}"
    headers = _openrouter_video_content_headers(settings)
    consecutive_errors = 0

    while time.monotonic() < deadline:
        try:
            response = httpx.get(poll_url, headers=headers, timeout=60)
        except httpx.TransportError as exc:
            if consecutive_errors >= len(OPENROUTER_VIDEO_POLL_RETRY_SECONDS):
                raise
            retry_delay = OPENROUTER_VIDEO_POLL_RETRY_SECONDS[consecutive_errors]
            consecutive_errors += 1
            logger.warning(
                "openrouter_video_poll retry jobId=%s consecutiveErrors=%s "
                "retryDelaySeconds=%s errorType=%s error=%s",
                job_id,
                consecutive_errors,
                retry_delay,
                type(exc).__name__,
                str(exc),
            )
            time.sleep(retry_delay)
            continue

        if response.status_code >= 400:
            is_retryable = response.status_code == 429 or response.status_code >= 500
            if is_retryable and consecutive_errors < len(OPENROUTER_VIDEO_POLL_RETRY_SECONDS):
                retry_delay = OPENROUTER_VIDEO_POLL_RETRY_SECONDS[consecutive_errors]
                consecutive_errors += 1
                logger.warning(
                    "openrouter_video_poll retry jobId=%s consecutiveErrors=%s "
                    "retryDelaySeconds=%s error=%s",
                    job_id,
                    consecutive_errors,
                    retry_delay,
                    _openrouter_video_error(response),
                )
                time.sleep(retry_delay)
                continue
            raise _openrouter_video_error(response)

        consecutive_errors = 0
        payload = response.json()
        status_value = str(payload.get("status") or "").lower()
        if status_value == "completed":
            return payload
        if status_value in {"failed", "cancelled", "canceled", "expired", "error"}:
            raise OpenRouterVideoTaskError(f"OpenRouter video job failed: {payload}")
        time.sleep(poll_interval)

    raise RuntimeError(f"OpenRouter video generation timed out for job {job_id}")


def _download_openrouter_video_bytes(settings: Any, job_id: str) -> bytes:
    content_url = f"{get_openrouter_video_url(settings)}/{job_id}/content"
    with httpx.stream(
        "GET",
        content_url,
        headers=_openrouter_video_content_headers(settings),
        timeout=180,
        follow_redirects=False,
    ) as response:
        if response.status_code >= 400:
            response_preview = _read_streamed_error_preview(response)
            raise OpenRouterVideoHTTPError(response.status_code, response_preview)
        result = _read_streamed_result_bytes(
            response,
            max_bytes=VIDEO_RESULT_MAX_BYTES,
            too_large_code="VIDEO_RESULT_TOO_LARGE",
            invalid_content_type_code="VIDEO_RESULT_CONTENT_TYPE_INVALID",
            content_type_prefix="video/",
        )
    if not result:
        raise RuntimeError("OpenRouter video content response was empty")
    return result


def _parse_pixel_size(size: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)x(\d+)\s*", size)
    if not match:
        raise ValueError(f"Invalid pixel size: {size}")
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid pixel size: {size}")
    return width, height


def normalize_pet_scene_video_frame_bytes(image_bytes: bytes) -> bytes:
    target_width, target_height = _parse_pixel_size(PET_SCENE_VIDEO_SIZE)
    target_ratio = target_width / target_height

    with Image.open(BytesIO(image_bytes)) as image:
        normalized = image.convert("RGB")
        source_ratio = normalized.width / normalized.height
        if source_ratio > target_ratio:
            crop_width = max(1, round(normalized.height * target_ratio))
            left = max(0, (normalized.width - crop_width) // 2)
            crop_box = (left, 0, left + crop_width, normalized.height)
        else:
            crop_height = max(1, round(normalized.width / target_ratio))
            top = max(0, (normalized.height - crop_height) // 2)
            crop_box = (0, top, normalized.width, top + crop_height)

        output = normalized.crop(crop_box)
        if output.size != (target_width, target_height):
            output = output.resize((target_width, target_height), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        output.save(buffer, format="PNG")
        return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class _GeneratedVideoMetadata:
    duration_seconds: float
    frame_rate: float
    width: int
    height: int


def _probe_generated_video(path: Path) -> _GeneratedVideoMetadata:
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-f",
                "mov",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-use_absolute_path",
                "0",
                "-i",
                str(path),
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate,width,height:format=duration",
                "-of",
                "json",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=VIDEO_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaResultError("VIDEO_PROCESS_TIMEOUT") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaResultError("VIDEO_RESULT_INVALID") from exc

    try:
        metadata = json.loads(probe.stdout)
        if not isinstance(metadata, dict):
            raise ValueError("video metadata is not an object")
        streams = metadata.get("streams")
        stream = streams[0] if isinstance(streams, list) and streams else None
        if not isinstance(stream, dict):
            raise ValueError("video stream is missing")
        format_metadata = metadata.get("format")
        if not isinstance(format_metadata, dict):
            raise ValueError("video format metadata is missing")
        duration = float(format_metadata.get("duration"))
        width = int(stream.get("width"))
        height = int(stream.get("height"))
        frame_rate = float(Fraction(str(stream.get("avg_frame_rate"))))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise MediaResultError("VIDEO_RESULT_INVALID") from exc

    if not math.isfinite(duration) or duration <= 0:
        raise MediaResultError("VIDEO_RESULT_INVALID")
    if duration > VIDEO_RESULT_MAX_DURATION_SECONDS:
        raise MediaResultError("VIDEO_RESULT_DURATION_EXCEEDED")
    if (
        width <= 0
        or height <= 0
        or width > VIDEO_RESULT_MAX_DIMENSION
        or height > VIDEO_RESULT_MAX_DIMENSION
        or width * height > VIDEO_RESULT_MAX_PIXELS
    ):
        raise MediaResultError("VIDEO_RESULT_DIMENSIONS_EXCEEDED")
    if not math.isfinite(frame_rate) or frame_rate <= 0 or frame_rate > VIDEO_RESULT_MAX_FPS:
        raise MediaResultError("VIDEO_RESULT_INVALID")
    return _GeneratedVideoMetadata(
        duration_seconds=duration,
        frame_rate=frame_rate,
        width=width,
        height=height,
    )


def _run_generated_video_process(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=VIDEO_PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaResultError("VIDEO_PROCESS_TIMEOUT") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaResultError("VIDEO_POSTPROCESS_FAILED") from exc


def _read_generated_video_result(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise MediaResultError("VIDEO_POSTPROCESS_FAILED") from exc
    if size <= 0:
        raise MediaResultError("VIDEO_POSTPROCESS_FAILED")
    if size > VIDEO_RESULT_MAX_BYTES:
        raise MediaResultError("VIDEO_RESULT_TOO_LARGE")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise MediaResultError("VIDEO_POSTPROCESS_FAILED") from exc


def render_ping_pong_video_bytes(
    video_bytes: bytes,
    *,
    start_offset_seconds: float = PET_SCENE_VIDEO_START_OFFSET_SECONDS,
    end_offset_seconds: float = 0.35,
) -> bytes:
    if not video_bytes:
        raise ValueError("video_bytes must not be empty")
    if len(video_bytes) > VIDEO_RESULT_MAX_BYTES:
        raise MediaResultError("VIDEO_RESULT_TOO_LARGE")
    if (
        not math.isfinite(start_offset_seconds)
        or not math.isfinite(end_offset_seconds)
        or start_offset_seconds < 0
        or end_offset_seconds < 0
    ):
        raise ValueError("video trim offsets must be finite and non-negative")

    with TemporaryDirectory(prefix="pet-ping-pong-") as temp_dir_value:
        temp_dir = Path(temp_dir_value)
        source_path = temp_dir / "source.mp4"
        output_path = temp_dir / "ping-pong.mp4"
        source_path.write_bytes(video_bytes)

        metadata = _probe_generated_video(source_path)
        duration = metadata.duration_seconds
        if duration > PING_PONG_VIDEO_MAX_DURATION_SECONDS:
            raise MediaResultError("VIDEO_RESULT_DURATION_EXCEEDED")
        frame_rate = metadata.frame_rate
        frame_seconds = 1 / max(1.0, frame_rate)
        trimmed_duration = duration - start_offset_seconds - end_offset_seconds
        reverse_end = trimmed_duration - frame_seconds
        if reverse_end <= frame_seconds:
            raise RuntimeError(f"Video is too short for ping-pong rendering: duration={duration}")

        filter_graph = (
            f"[0:v]trim=start={start_offset_seconds:.6f}:"
            f"end={duration - end_offset_seconds:.6f},"
            "setpts=PTS-STARTPTS,split=2[f][r];"
            f"[r]reverse,trim=start={frame_seconds:.6f}:end={reverse_end:.6f},"
            "setpts=PTS-STARTPTS[rev];"
            "[f][rev]concat=n=2:v=1:a=0,fps=24[out]"
        )
        _run_generated_video_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "mov",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-use_absolute_path",
                "0",
                "-i",
                str(source_path),
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-level:v",
                "3.1",
                "-video_track_timescale",
                "12288",
                "-movflags",
                "+faststart",
                "-fs",
                str(VIDEO_RESULT_MAX_BYTES),
                str(output_path),
            ]
        )
        return _read_generated_video_result(output_path)


def strip_generated_video_auxiliary_streams(video_bytes: bytes) -> bytes:
    """Keep only the primary video stream for Telegram-safe generated MP4 output."""
    if not video_bytes:
        raise ValueError("video_bytes must not be empty")
    if len(video_bytes) > VIDEO_RESULT_MAX_BYTES:
        raise MediaResultError("VIDEO_RESULT_TOO_LARGE")

    with TemporaryDirectory(prefix="generated-video-main-stream-") as temp_dir_value:
        temp_dir = Path(temp_dir_value)
        source_path = temp_dir / "source.mp4"
        output_path = temp_dir / "main-stream.mp4"
        source_path.write_bytes(video_bytes)
        _probe_generated_video(source_path)
        _run_generated_video_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "mov",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-use_absolute_path",
                "0",
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-movflags",
                "+faststart",
                "-fs",
                str(VIDEO_RESULT_MAX_BYTES),
                str(output_path),
            ]
        )
        return _read_generated_video_result(output_path)


def pet_character_region_box(scene_size: tuple[int, int]) -> tuple[int, int, int, int]:
    scene_width, scene_height = scene_size
    target_width, target_height = _parse_pixel_size(PET_CHARACTER_REGION_SIZE)
    width_ratio = target_width / _parse_pixel_size(PET_SCENE_VIDEO_SIZE)[0]
    region_width = min(scene_width, max(1, round(scene_width * width_ratio)))
    region_height = min(
        scene_height,
        max(1, round(region_width * target_height / target_width)),
    )
    center_x = scene_width // 2
    center_y = round(scene_height * PET_CHARACTER_REGION_CENTER_Y_RATIO)
    left = max(0, min(scene_width - region_width, center_x - region_width // 2))
    top = max(0, min(scene_height - region_height, center_y - region_height // 2))
    return left, top, left + region_width, top + region_height


def extract_pet_character_region_bytes(scene_path: Path) -> bytes:
    with Image.open(scene_path) as image:
        normalized = image.convert("RGB")
        region = normalized.crop(pet_character_region_box(normalized.size))
        buffer = BytesIO()
        region.save(buffer, format="PNG")
        return buffer.getvalue()


def normalize_pet_character_region_bytes(
    image_bytes: bytes,
    target_size: tuple[int, int],
) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        output = ImageOps.fit(
            image.convert("RGB"),
            target_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        buffer = BytesIO()
        output.save(buffer, format="PNG")
        return buffer.getvalue()


def composite_pet_character_region_bytes(
    scene_path: Path,
    generated_region_bytes: bytes,
) -> bytes:
    with Image.open(scene_path) as scene_image:
        output = scene_image.convert("RGB")
        left, top, right, bottom = pet_character_region_box(output.size)
        region_size = (right - left, bottom - top)
        normalized_region_bytes = normalize_pet_character_region_bytes(
            generated_region_bytes,
            region_size,
        )
        with Image.open(BytesIO(normalized_region_bytes)) as region_image:
            region = region_image.convert("RGB")

        feather = max(2, round(min(region_size) * PET_CHARACTER_REGION_FEATHER_RATIO))
        mask = Image.new("L", region_size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rectangle(
            (
                feather,
                feather,
                region_size[0] - feather - 1,
                region_size[1] - feather - 1,
            ),
            fill=255,
        )
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, feather / 2)))
        output.paste(region, (left, top), mask)

        buffer = BytesIO()
        output.save(buffer, format="PNG")
        return buffer.getvalue()


def generate_openrouter_video_bytes(
    source_path: Path | None,
    *,
    label: str,
    prompt: str = PET_SCENE_VIDEO_PROMPT,
    source_bytes: bytes | None = None,
    resolution: str = PET_SCENE_VIDEO_RESOLUTION,
    aspect_ratio: str = PET_SCENE_VIDEO_ASPECT_RATIO,
    duration: int = PET_SCENE_VIDEO_DURATION_SECONDS,
    input_references: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> bytes:
    if source_bytes is not None:
        source_data_url = f"data:image/png;base64,{base64.b64encode(source_bytes).decode('utf-8')}"
    elif source_path is not None:
        source_data_url = _image_path_data_url(source_path)
    elif input_references:
        source_data_url = None
    else:
        raise ValueError("source_path, source_bytes or input_references is required")

    settings = get_settings()
    model = (model or "").strip() or get_openrouter_video_model(settings)
    payload = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": False,
    }
    if source_data_url:
        payload["frame_images"] = [
            {
                "type": "image_url",
                "image_url": {"url": source_data_url},
                "frame_type": "first_frame",
            }
        ]
    else:
        payload["input_references"] = input_references or []
    log_video_generation_prompt(
        label,
        {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "has_source_image": source_data_url is not None,
            "input_references": input_references or [],
        },
    )
    logger.info(
        "OpenRouter video generation requested label=%s model=%s duration=%s "
        "resolution=%s aspectRatio=%s hasSourceImage=%s inputReferenceCount=%s",
        label,
        model,
        duration,
        resolution,
        aspect_ratio,
        source_data_url is not None,
        len(input_references or []),
    )

    operation = _provider_task_operation("video", label)
    provider_origin = get_openrouter_video_url(settings).rstrip("/")
    account_namespace = _provider_account_namespace(settings, "openrouter")
    payload_fingerprint = provider_task_payload_fingerprint(payload)
    with implicit_provider_task_scope(
        _implicit_provider_task_store_path(settings),
        max_records=_implicit_provider_task_store_max_records(settings),
        operation=operation,
        provider="openrouter",
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    ):
        receipt = find_current_provider_task(
            operation=operation,
            provider="openrouter",
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            payload_fingerprint=payload_fingerprint,
        )
        if receipt is None:
            try:
                response = _submit_openrouter_video_job(settings, payload, label=label)
            except Exception as exc:
                if _is_definitely_not_accepted_submit_error(exc):
                    release_current_provider_task_admission(operation)
                raise
            if response.status_code >= 400:
                release_current_provider_task_admission(operation)
                raise _openrouter_video_error(response)
            submit_payload = response.json()
            job_id = str(submit_payload.get("id") or "").strip()
            if not job_id:
                raise RuntimeError(f"OpenRouter video response missing job id: {submit_payload}")

            polling_url_value = submit_payload.get("polling_url")
            polling_url = (
                _trusted_openrouter_polling_url(settings, str(polling_url_value))
                if polling_url_value
                else None
            )
            save_current_provider_task(
                operation=operation,
                provider="openrouter",
                provider_origin=provider_origin,
                account_namespace=account_namespace,
                task_id=job_id,
                polling_url=polling_url,
                payload_fingerprint=payload_fingerprint,
            )
            logger.info(
                "OpenRouter video job submitted label=%s jobId=%s initialStatus=%s",
                label,
                job_id,
                submit_payload.get("status"),
            )
        else:
            job_id = receipt.task_id
            polling_url = (
                _trusted_openrouter_polling_url(settings, receipt.polling_url)
                if receipt.polling_url
                else None
            )
            logger.info(
                "OpenRouter video job resumed label=%s jobId=%s state=%s",
                label,
                job_id,
                receipt.state,
            )
        try:
            _poll_openrouter_video_job(settings, job_id, polling_url=polling_url)
            return _download_openrouter_video_bytes(settings, job_id)
        except OpenRouterVideoTaskError:
            mark_current_provider_task_failed(operation)
            raise
        except OpenRouterVideoHTTPError as exc:
            if exc.status_code in {404, 410}:
                mark_current_provider_task_failed(operation)
            raise


def generate_openrouter_video_from_image_bytes(
    image_bytes: bytes | None,
    *,
    label: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    input_references: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> bytes:
    return generate_openrouter_video_bytes(
        None,
        label=label,
        prompt=prompt,
        source_bytes=image_bytes,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        duration=duration,
        input_references=input_references,
        model=model,
    )


def _video_request(
    image_bytes: bytes | None,
    *,
    label: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    provider: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
    model: str | None,
) -> VideoRequest:
    return VideoRequest(
        prompt=prompt,
        source_image=image_bytes,
        task=label,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration,
        provider=provider,
        input_references=tuple(input_references or ()),
        model=model,
    )


def generate_video_from_image_bytes(
    image_bytes: bytes | None,
    *,
    label: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    provider: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> bytes:
    return get_media_gateway().generate_video(
        _video_request(
            image_bytes,
            label=label,
            prompt=prompt,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            provider=provider,
            input_references=input_references,
            model=model,
        )
    )


@contextmanager
def reserve_video_from_image_bytes(
    image_bytes: bytes | None,
    *,
    label: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    provider: str | None = None,
    input_references: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> Iterator[bytes]:
    with get_media_gateway().generate_video_reserved(
        _video_request(
            image_bytes,
            label=label,
            prompt=prompt,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            provider=provider,
            input_references=input_references,
            model=model,
        )
    ) as payload:
        yield payload


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


def generated_dir_for(pet_id: uuid.UUID) -> Path:
    return GENERATED_ASSET_ROOT / str(pet_id)


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


def make_character_foreground_image_bytes(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        foreground = image.convert("RGBA")
        transparent = (255, 255, 255, 0)
        corners = (
            (0, 0),
            (foreground.width - 1, 0),
            (0, foreground.height - 1),
            (foreground.width - 1, foreground.height - 1),
        )
        for corner in corners:
            if foreground.getpixel(corner)[3] > 0:
                ImageDraw.floodfill(foreground, corner, transparent, thresh=48)

        alpha = foreground.getchannel("A").filter(ImageFilter.GaussianBlur(radius=0.8))
        foreground.putalpha(alpha)
        buffer = BytesIO()
        foreground.save(buffer, format="PNG")
        return buffer.getvalue()


def align_sprite_to_reference_canvas(image_bytes: bytes, reference_path: Path) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image, Image.open(reference_path) as reference:
        sprite_image = image.convert("RGBA")
        reference_image = reference.convert("RGBA")
        reference_bbox = foreground_component_bbox(
            reference_image,
            (0, 0, reference_image.width, reference_image.height),
        )
        sprite_bbox = foreground_component_bbox(
            sprite_image,
            (0, 0, sprite_image.width, sprite_image.height),
        )
        if reference_bbox is None or sprite_bbox is None:
            return normalize_single_sprite_image(image_bytes)

        target_left, target_top, target_right, target_bottom = reference_bbox
        source_left, source_top, source_right, source_bottom = sprite_bbox
        target_width = max(1, target_right - target_left)
        target_height = max(1, target_bottom - target_top)
        sprite = sprite_image.crop((source_left, source_top, source_right, source_bottom))
        sprite = sprite.resize((target_width, target_height), Image.Resampling.LANCZOS)

        canvas = Image.new("RGBA", reference_image.size, (255, 255, 255, 0))
        canvas.alpha_composite(sprite, (target_left, target_top))
        buffer = BytesIO()
        canvas.save(buffer, format="PNG")
        return buffer.getvalue()


def generate_single_sprite_image_bytes(prompt: str, *, provider: str | None = None) -> bytes:
    return normalize_single_sprite_image(generate_image_bytes(prompt, provider=provider))


@contextmanager
def reserve_single_sprite_image_bytes(
    prompt: str,
    *,
    provider: str | None = None,
) -> Iterator[bytes]:
    with reserve_image_bytes(prompt, provider=provider) as payload:
        yield normalize_single_sprite_image(payload)
        mark_current_provider_task_media_saved(
            _provider_task_operation("image", "pet_creation/image")
        )


def generate_pet_scene_image_bytes(
    character_path: Path,
    *,
    provider: str | None = None,
) -> bytes:
    if not PET_SCENE_BACKGROUND_PATH.exists():
        raise RuntimeError(f"Pet scene background not found: {PET_SCENE_BACKGROUND_PATH}")
    return generate_multi_image_edit_bytes(
        PET_SCENE_COMPOSITION_PROMPT,
        [character_path, PET_SCENE_BACKGROUND_PATH],
        label="pet_creation/scene",
        size=PET_SCENE_IMAGE_SIZE,
        provider=provider,
    )


@contextmanager
def reserve_pet_scene_image_bytes(
    character_path: Path,
    *,
    provider: str | None = None,
) -> Iterator[bytes]:
    if not PET_SCENE_BACKGROUND_PATH.exists():
        raise RuntimeError(f"Pet scene background not found: {PET_SCENE_BACKGROUND_PATH}")
    with reserve_multi_image_edit_bytes(
        PET_SCENE_COMPOSITION_PROMPT,
        [character_path, PET_SCENE_BACKGROUND_PATH],
        label="pet_creation/scene",
        size=PET_SCENE_IMAGE_SIZE,
        provider=provider,
    ) as payload:
        yield payload


def generate_pet_scene_video_bytes(
    scene_path: Path,
    *,
    prompt: str = PET_SCENE_VIDEO_PROMPT,
    label: str = "pet_creation/scene_video",
    provider: str | None = None,
) -> bytes:
    provider_prompt = (
        KANDINSKY_PET_SCENE_VIDEO_PROMPT
        if provider == "kandinsky" and prompt == PET_SCENE_VIDEO_PROMPT
        else prompt
    )
    request = VideoRequest(
        prompt=provider_prompt,
        source_image=scene_path.read_bytes(),
        task=label,
        resolution=PET_SCENE_VIDEO_RESOLUTION,
        aspect_ratio=PET_SCENE_VIDEO_ASPECT_RATIO,
        duration_seconds=PET_SCENE_VIDEO_DURATION_SECONDS,
        provider=provider,
    )
    resolved_provider = get_media_router().resolve_video(request).provider
    video_bytes = get_media_gateway().generate_video(request)
    start_offset_seconds = (
        SEEDANCE_PET_SCENE_VIDEO_START_OFFSET_SECONDS
        if resolved_provider == "openrouter"
        and "seedance" in get_openrouter_video_model(get_settings()).lower()
        else PET_SCENE_VIDEO_START_OFFSET_SECONDS
    )
    return render_ping_pong_video_bytes(
        video_bytes,
        start_offset_seconds=start_offset_seconds,
    )


@contextmanager
def reserve_pet_scene_video_bytes(
    scene_path: Path,
    *,
    prompt: str = PET_SCENE_VIDEO_PROMPT,
    label: str = "pet_creation/scene_video",
    provider: str | None = None,
) -> Iterator[bytes]:
    provider_prompt = (
        KANDINSKY_PET_SCENE_VIDEO_PROMPT
        if provider == "kandinsky" and prompt == PET_SCENE_VIDEO_PROMPT
        else prompt
    )
    request = VideoRequest(
        prompt=provider_prompt,
        source_image=scene_path.read_bytes(),
        task=label,
        resolution=PET_SCENE_VIDEO_RESOLUTION,
        aspect_ratio=PET_SCENE_VIDEO_ASPECT_RATIO,
        duration_seconds=PET_SCENE_VIDEO_DURATION_SECONDS,
        provider=provider,
    )
    resolved_provider = get_media_router().resolve_video(request).provider
    with get_media_gateway().generate_video_reserved(request) as video_bytes:
        start_offset_seconds = (
            SEEDANCE_PET_SCENE_VIDEO_START_OFFSET_SECONDS
            if resolved_provider == "openrouter"
            and "seedance" in get_openrouter_video_model(get_settings()).lower()
            else PET_SCENE_VIDEO_START_OFFSET_SECONDS
        )
        yield render_ping_pong_video_bytes(
            video_bytes,
            start_offset_seconds=start_offset_seconds,
        )
        mark_current_provider_task_media_saved(_provider_task_operation("video", label))


def generate_individual_sprite_paths(
    asset_id: uuid.UUID,
    description: str,
    character_bible: str | dict[str, Any],
    *,
    image_provider: str | None = None,
) -> tuple[dict[tuple[str, str], tuple[Path, str]], Path]:
    output_paths = generate_individual_sprite_image_paths(
        asset_id,
        description,
        character_bible,
        image_provider=image_provider,
    )
    scene_path = output_paths[(FAST_GENERATION_STAGE, "idle")][0]
    video_path = generate_pet_scene_video_path(asset_id, scene_path)
    return output_paths, video_path


def generate_individual_sprite_image_paths(
    asset_id: uuid.UUID,
    description: str,
    character_bible: str | dict[str, Any],
    *,
    image_provider: str | None = None,
) -> dict[tuple[str, str], tuple[Path, str]]:
    output_dir = generated_dir_for(asset_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[tuple[str, str], tuple[Path, str]] = {}

    for stage, state in FAST_GENERATION_SKINS:
        character_path = output_dir / f"{stage}-{state}-character.png"
        foreground_path = output_dir / f"{stage}-{state}-foreground.png"
        path = output_dir / f"{stage}-{state}.png"
        if _is_valid_image_file(path):
            output_paths[(stage, state)] = (path, PET_SCENE_COMPOSITION_PROMPT)
            continue

        prompt = build_pet_single_sprite_prompt(
            description,
            character_bible,
            stage=stage,
            state=state,
        )
        if not _is_valid_image_file(character_path):
            try:
                with reserve_single_sprite_image_bytes(
                    prompt,
                    provider=image_provider,
                ) as sprite_bytes:
                    _atomic_write_nonempty(character_path, sprite_bytes)
            except Exception as exc:
                if generation_error_code(exc) != "IMAGE_PROMPT_REJECTED":
                    raise
                logger.info(
                    "Retrying image generation with safety-constrained single sprite prompt"
                )
                prompt = build_pet_single_sprite_safety_retry_prompt(
                    description,
                    character_bible,
                    stage=stage,
                    state=state,
                )
                with reserve_single_sprite_image_bytes(
                    prompt,
                    provider=image_provider,
                ) as sprite_bytes:
                    _atomic_write_nonempty(character_path, sprite_bytes)
        else:
            sprite_bytes = character_path.read_bytes()

        if not _is_valid_image_file(foreground_path):
            _atomic_write_nonempty(
                foreground_path,
                make_character_foreground_image_bytes(sprite_bytes),
            )

        with reserve_pet_scene_image_bytes(
            character_path,
            provider=image_provider,
        ) as scene_bytes:
            _atomic_write_nonempty(path, normalize_pet_scene_video_frame_bytes(scene_bytes))
        output_paths[(stage, state)] = (path, PET_SCENE_COMPOSITION_PROMPT)

    return output_paths


def generate_pet_scene_video_path(
    asset_id: uuid.UUID,
    scene_path: Path,
    *,
    provider: str | None = None,
) -> Path:
    video_path = generated_dir_for(asset_id) / f"{FAST_GENERATION_STAGE}-idle.mp4"
    if _is_nonempty_file(video_path):
        return video_path
    reservation = (
        reserve_pet_scene_video_bytes(scene_path, provider=provider)
        if provider is not None
        else reserve_pet_scene_video_bytes(scene_path)
    )
    with reservation as video_bytes:
        _atomic_write_nonempty(video_path, video_bytes)
    return video_path


def generate_pet_sad_scene_path(
    image_set: PetAssetImageSet,
    *,
    image_provider: str | None = None,
) -> Path:
    output_dir = generated_dir_for(image_set.asset_set_id)
    sad_scene_path = output_dir / f"{FAST_GENERATION_STAGE}-sad.png"
    if _is_valid_image_file(sad_scene_path):
        return sad_scene_path

    sad_pose_path = output_dir / f"{FAST_GENERATION_STAGE}-sad-pose.png"
    if not _is_valid_image_file(sad_pose_path):
        with reserve_image_edit_bytes(
            PET_SAD_SCENE_IMAGE_PROMPT,
            image_set.scene_path,
            label="pet_creation/sad_pose",
            provider=image_provider,
        ) as sad_pose_bytes:
            _atomic_write_nonempty(
                sad_pose_path,
                normalize_pet_scene_video_frame_bytes(sad_pose_bytes),
            )

    with reserve_multi_image_edit_bytes(
        PET_SAD_SCENE_COMPOSITION_REFINEMENT_PROMPT,
        [image_set.scene_path, sad_pose_path],
        label="pet_creation/sad_scene",
        size=PET_SCENE_IMAGE_SIZE,
        provider=image_provider,
    ) as sad_scene_bytes:
        _atomic_write_nonempty(
            sad_scene_path,
            normalize_pet_scene_video_frame_bytes(sad_scene_bytes),
        )
    sad_pose_path.unlink(missing_ok=True)
    return sad_scene_path


def generate_pet_sad_video_for_image_asset_set(
    image_set: PetAssetImageSet,
    sad_scene_path: Path,
) -> Path:
    video_path = generated_dir_for(image_set.asset_set_id) / f"{FAST_GENERATION_STAGE}-sad.mp4"
    if _is_nonempty_file(video_path):
        return video_path
    with reserve_pet_scene_video_bytes(
        sad_scene_path,
        prompt=PET_SAD_SCENE_VIDEO_PROMPT,
        label="pet_creation/sad_scene_video",
    ) as video_bytes:
        _atomic_write_nonempty(video_path, video_bytes)
    return video_path


def generate_pet_happy_scene_path(
    image_set: PetAssetImageSet,
    *,
    image_provider: str | None = None,
) -> Path:
    output_dir = generated_dir_for(image_set.asset_set_id)
    happy_scene_path = output_dir / f"{FAST_GENERATION_STAGE}-happy.png"
    if _is_valid_image_file(happy_scene_path):
        return happy_scene_path

    source_region_path = output_dir / f"{FAST_GENERATION_STAGE}-happy-source-region.png"
    if not _is_valid_image_file(source_region_path):
        _atomic_write_nonempty(
            source_region_path,
            extract_pet_character_region_bytes(image_set.scene_path),
        )
    with Image.open(source_region_path) as source_region:
        region_size = source_region.size

    happy_pose_path = output_dir / f"{FAST_GENERATION_STAGE}-happy-pose.png"
    if not _is_valid_image_file(happy_pose_path):
        with reserve_image_edit_bytes(
            PET_HAPPY_SCENE_IMAGE_PROMPT,
            source_region_path,
            label="pet_creation/happy_pose",
            provider=image_provider,
        ) as happy_pose_bytes:
            _atomic_write_nonempty(
                happy_pose_path,
                normalize_pet_character_region_bytes(happy_pose_bytes, region_size),
            )

    with reserve_multi_image_edit_bytes(
        PET_HAPPY_SCENE_COMPOSITION_REFINEMENT_PROMPT,
        [source_region_path, happy_pose_path],
        label="pet_creation/happy_scene",
        size=PET_SCENE_IMAGE_SIZE,
        provider=image_provider,
    ) as happy_scene_bytes:
        _atomic_write_nonempty(
            happy_scene_path,
            composite_pet_character_region_bytes(image_set.scene_path, happy_scene_bytes),
        )
    happy_pose_path.unlink(missing_ok=True)
    source_region_path.unlink(missing_ok=True)
    return happy_scene_path


def generate_pet_happy_video_for_image_asset_set(
    image_set: PetAssetImageSet,
    happy_scene_path: Path,
) -> Path:
    video_path = generated_dir_for(image_set.asset_set_id) / f"{FAST_GENERATION_STAGE}-happy.mp4"
    if _is_nonempty_file(video_path):
        return video_path
    with reserve_pet_scene_video_bytes(
        happy_scene_path,
        prompt=PET_SCENE_VIDEO_PROMPT,
        label="pet_creation/happy_scene_video",
    ) as video_bytes:
        _atomic_write_nonempty(video_path, video_bytes)
    return video_path


def crop_sprite_sheet(pet_id: uuid.UUID, sprite_path: Path) -> dict[tuple[str, str], Path]:
    output_paths: dict[tuple[str, str], Path] = {}
    with Image.open(sprite_path) as image:
        cell_images = extract_sprite_cells(image)

        for (stage, state), crop in cell_images.items():
            path = generated_dir_for(pet_id) / f"{stage}-{state}.png"
            crop.save(path, format="PNG")
            output_paths[(stage, state)] = path

    return output_paths


def _llm_provider_error(exc: Exception) -> LLMProviderError | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LLMProviderError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _error_chain_contains(exc: BaseException, error_types: Any) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, error_types):
            return True
        current = current.__cause__ or current.__context__
    return False


def generation_error_code(exc: Exception) -> str:
    if isinstance(exc, PromptRepairExhausted):
        return exc.code
    if isinstance(exc, StorageCapacityError):
        return exc.code
    if isinstance(exc, MediaResultError):
        return exc.code
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
        response_text = exc.response.text.casefold()
        if status_code in {400, 403, 422} and any(
            term in response_text
            for term in (
                "safety",
                "policy",
                "moderation",
                "content filter",
                "content_filter",
                "censor",
                "rejected by",
                "prompt rejected",
            )
        ):
            return "IMAGE_PROMPT_REJECTED"
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
    if isinstance(exc, KandinskyTaskError):
        message = str(exc).casefold()
        if any(
            term in message
            for term in (
                "safety",
                "policy",
                "moderation",
                "content filter",
                "content_filter",
                "censor",
                "prompt rejected",
            )
        ):
            return "IMAGE_PROMPT_REJECTED"
        return "KANDINSKY_TASK_FAILED"
    if isinstance(exc, APIStatusError):
        return f"OPENAI_STATUS_{exc.status_code}"
    if isinstance(exc, APIConnectionError | httpx.HTTPError):
        return "OPENAI_CONNECTION_FAILED"
    llm_error = _llm_provider_error(exc)
    if llm_error is not None:
        if llm_error.error_kind == "authentication":
            return "LLM_AUTH_FAILED"
        status_code = llm_error.status_code
        if status_code == 401:
            return "LLM_AUTH_FAILED"
        if status_code == 403:
            return "LLM_PERMISSION_DENIED"
        if status_code == 429:
            return "LLM_RATE_LIMIT"
        if status_code == 400:
            return "LLM_BAD_REQUEST"
        if status_code is not None:
            return f"LLM_STATUS_{status_code}"
        if _error_chain_contains(llm_error, httpx.TimeoutException):
            return "LLM_TIMEOUT"
        if _error_chain_contains(llm_error, httpx.HTTPError):
            return "LLM_CONNECTION_FAILED"
        return "LLM_FAILED"
    if isinstance(exc, OSError):
        return "IMAGE_SAVE_FAILED"
    return "GENERATION_FAILED"


def generate_pet_image_asset_set(
    description: str,
    *,
    image_provider: str | None = None,
    character_bible: str | dict[str, Any] | None = None,
    asset_set_id: uuid.UUID | None = None,
) -> PetAssetImageSet:
    durable_asset_set = asset_set_id is not None
    asset_set_id = asset_set_id or uuid.uuid4()
    output_dir = generated_dir_for(asset_set_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    if durable_asset_set:
        character_bible, generated_at, version = _load_or_create_pet_generation_metadata(
            asset_set_id,
            description,
            image_provider,
            character_bible,
        )
    else:
        character_bible = character_bible or create_character_bible(description)
        if not isinstance(character_bible, dict):
            raise ValueError("pet character bible must be an object")
        generated_at = datetime.now(UTC)
        version = int(generated_at.timestamp())
    generated_paths = generate_individual_sprite_image_paths(
        asset_set_id,
        description,
        character_bible,
        image_provider=image_provider,
    )
    return PetAssetImageSet(
        asset_set_id=asset_set_id,
        generated_paths=generated_paths,
        scene_path=generated_paths[(FAST_GENERATION_STAGE, "idle")][0],
        character_bible=character_bible,
        version=version,
        generated_at=generated_at,
    )


def generate_pet_video_for_image_asset_set(image_set: PetAssetImageSet) -> Path:
    return generate_pet_scene_video_path(image_set.asset_set_id, image_set.scene_path)


def build_pet_static_asset_set_response(
    image_set: PetAssetImageSet,
    sad_scene_path: Path,
    happy_scene_path: Path,
    video_path: Path | None = None,
) -> dict[str, Any]:
    asset_set_id = image_set.asset_set_id
    version = image_set.version
    idle_url = f"/static/generated/{asset_set_id}/{image_set.scene_path.name}?v={version}"
    sad_url = f"/static/generated/{asset_set_id}/{sad_scene_path.name}?v={version}"
    happy_url = f"/static/generated/{asset_set_id}/{happy_scene_path.name}?v={version}"
    images = {
        stage: {
            "idle": idle_url,
            "happy": happy_url,
            "hungry": idle_url,
            "sad": sad_url,
        }
        for stage in STAGE_ROWS
    }
    return {
        "assetSetId": str(asset_set_id),
        "generatedAt": image_set.generated_at,
        "images": images,
        "videoUrl": (
            f"/static/generated/{asset_set_id}/{video_path.name}?v={version}"
            if video_path is not None
            else None
        ),
    }


def generate_kandinsky_pet_comparison_assets(
    description: str,
    character_bible: str | dict[str, Any],
    *,
    asset_set_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    image_set = generate_pet_image_asset_set(
        description,
        image_provider="kandinsky",
        character_bible=character_bible,
        asset_set_id=asset_set_id,
    )
    sad_scene_path = generate_pet_sad_scene_path(
        image_set,
        image_provider="kandinsky",
    )
    happy_scene_path = generate_pet_happy_scene_path(
        image_set,
        image_provider="kandinsky",
    )
    video_path = generate_pet_scene_video_path(
        image_set.asset_set_id,
        image_set.scene_path,
        provider="kandinsky",
    )
    return build_pet_static_asset_set_response(
        image_set,
        sad_scene_path,
        happy_scene_path,
        video_path,
    )


def build_pet_asset_set_response(
    image_set: PetAssetImageSet,
    video_path: Path,
    sad_scene_path: Path | None = None,
    sad_video_path: Path | None = None,
    happy_scene_path: Path | None = None,
    happy_video_path: Path | None = None,
) -> dict[str, Any]:
    asset_set_id = image_set.asset_set_id
    generated_paths = image_set.generated_paths
    version = image_set.version

    generated_urls = {
        key: f"/static/generated/{asset_set_id}/{path.name}?v={version}"
        for key, (path, _prompt) in generated_paths.items()
    }
    sad_assets_ready = sad_scene_path is not None and sad_video_path is not None
    happy_assets_ready = happy_scene_path is not None and happy_video_path is not None
    sad_scene_url = (
        f"/static/generated/{asset_set_id}/{sad_scene_path.name}?v={version}"
        if sad_assets_ready
        else None
    )
    happy_scene_url = (
        f"/static/generated/{asset_set_id}/{happy_scene_path.name}?v={version}"
        if happy_assets_ready
        else None
    )
    images: dict[str, dict[str, str]] = {stage: {} for stage in STAGE_ROWS}
    for stage in STAGE_ROWS:
        for state in STATE_COLUMNS:
            source_key = FAST_GENERATION_STATE_FALLBACKS[state]
            images[stage][state] = generated_urls[source_key]
        if sad_scene_url:
            images[stage]["sad"] = sad_scene_url
        if happy_scene_url:
            images[stage]["happy"] = happy_scene_url

    return {
        "assetSetId": str(asset_set_id),
        "generatedAt": image_set.generated_at,
        "images": images,
        "videoUrl": f"/static/generated/{asset_set_id}/{video_path.name}?v={version}",
        "sadVideoUrl": (
            f"/static/generated/{asset_set_id}/{sad_video_path.name}?v={version}"
            if sad_assets_ready
            else None
        ),
        "happyVideoUrl": (
            f"/static/generated/{asset_set_id}/{happy_video_path.name}?v={version}"
            if happy_assets_ready
            else None
        ),
        "blinkImageUrl": generated_urls.get((FAST_GENERATION_STAGE, "blink")),
        "spriteSheetUrl": None,
        "characterBible": image_set.character_bible,
    }


def generate_pet_asset_set(description: str) -> dict[str, Any]:
    image_set = generate_pet_image_asset_set(description)
    video_path = generate_pet_video_for_image_asset_set(image_set)
    return build_pet_asset_set_response(image_set, video_path)
