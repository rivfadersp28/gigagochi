from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services.image_service import generate_image_bytes
from app.services.lite_overlay import (
    LITE_FACT_KINDS,
    LITE_FACT_SPHERES,
    overlay_patch_from_extracted_facts,
)
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
    get_openai_client,
)
from app.services.pet_reply_engine.context_plan import (
    CONTEXT_ROUTING_SOURCE_IDS,
    ContextPlan,
    ContextRoutingDecision,
    build_context_plan,
    router_sources_for_auto_modes,
)
from app.services.pet_reply_engine.speech_runtime import (
    CONTEXT_SOURCE_KEYS,
    background_story_aftermath_extraction_system_prompt,
    background_story_aftermath_extraction_user_prompt,
    background_story_default_event_type,
    background_story_max_rag_chars,
    background_story_max_story_chars,
    background_story_source_flags,
    background_story_system_prompt,
    background_story_user_prompt,
    context_routing_sources,
    context_routing_system_prompt,
    context_source_enabled,
    context_source_mode,
    state_param_labels,
    state_param_usage_rule,
)
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response
from app.services.story_library import search_story_library
from app.services.tone_runtime import tone_context_payload, tone_prompt_block, tone_visual_style

logger = logging.getLogger(__name__)

MAX_CHARACTER_DOSSIER_CHARS = 12000
MAX_DOSSIER_LIST_ITEMS = 12
MAX_AFTERMATH_CONTEXT_CHARS = 12000
AFTERMATH_CONFIDENCE_THRESHOLD = 0.7
BACKGROUND_ROUTING_SOURCE_IDS = CONTEXT_ROUTING_SOURCE_IDS
STORY_STAT_KEYS = {"hunger", "happiness", "energy"}
STORY_STAT_MAX_ITEMS = 2
STORY_STAT_MAX_SINGLE_DAMAGE = 25
STORY_STAT_MAX_TOTAL_DAMAGE = 35
STORY_STAT_NO_LOSS_RE = re.compile(
    r"\b("
    r"без\s+потерь|без\s+ущерба|не\s+пострадал\w*|"
    r"никто\s+не\s+пострадал\w*|ничего\s+не\s+потерял\w*|"
    r"ничего\s+не\s+случил\w*|без\s+последствий"
    r")\b",
    re.IGNORECASE,
)
STORY_STAT_NEGATIVE_EVIDENCE_RE = re.compile(
    r"\b("
    r"потерял\w*|потерян\w*|украл\w*|украден\w*|утащил\w*|"
    r"не\s+смог\w*|устал\w*|выдох\w*|ранен\w*|травм\w*|"
    r"повред\w*|груст\w*|испуг\w*|голод\w*|лишил\w*|сломал\w*"
    r")\b",
    re.IGNORECASE,
)
LOCAL_REFERENCE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS = 4200
BACKGROUND_STORY_IMAGE_SCENE_STORY_MAX_CHARS = 2400
BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS = 900
BACKGROUND_STORY_IMAGE_SCENE_INSTRUCTION = (
    "выдели из этого текста основную сцену, которая иллюстрирует сюжет лучше "
    "всего сюжет должен быть четким как тз для художника"
)
BACKGROUND_STORY_IMAGE_STYLE = """
Детальная фэнтези-манга, обложка ранобэ или ключевой арт японской ролевой игры.
Чистый контур, мягкая аниме-заливка, приглушённые природные цвета с резкими
акцентами. Выразительный свет, странная магическая повседневность, читаемый
центральный момент. Рисованные материалы, плоские цвета, мягкие тени. Фон с
бытовыми деталями, растениями, инструментами, украшениями и живыми
второстепенными персонажами. Настроение: приключение с лёгкой иронией,
любопытством и ощущением, что магия слегка плохо настроена.
""".strip()
BACKGROUND_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 360},
        "storyText": {"type": "string", "maxLength": 2000},
        "eventType": {"type": "string", "maxLength": 60},
        "valence": {
            "type": "string",
            "enum": ["negative", "neutral", "positive", "mixed"],
        },
        "tags": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 40},
        },
        "statImpacts": {
            "type": "array",
            "maxItems": STORY_STAT_MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stat": {
                        "type": "string",
                        "enum": ["hunger", "happiness", "energy"],
                    },
                    "amount": {
                        "type": "number",
                        "minimum": -STORY_STAT_MAX_SINGLE_DAMAGE,
                        "maximum": -1,
                    },
                    "reason": {"type": "string", "maxLength": 280},
                },
                "required": ["stat", "amount", "reason"],
            },
        },
        "ragText": {"type": "string", "maxLength": 900},
    },
    "required": [
        "title",
        "summary",
        "storyText",
        "eventType",
        "valence",
        "tags",
        "statImpacts",
        "ragText",
    ],
}
BACKGROUND_STORY_ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sources": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                source: {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "query": {"type": "string", "maxLength": 500},
                    },
                    "required": ["enabled", "query"],
                }
                for source in BACKGROUND_ROUTING_SOURCE_IDS
            },
            "required": list(BACKGROUND_ROUTING_SOURCE_IDS),
        },
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["sources", "reason"],
}
BACKGROUND_STORY_AFTERMATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sphere": {"type": "string", "enum": list(LITE_FACT_SPHERES)},
                    "kind": {"type": "string", "enum": list(LITE_FACT_KINDS)},
                    "text": {"type": "string", "maxLength": 500},
                    "pathHint": {"type": "string", "maxLength": 120},
                    "source": {"type": "string", "maxLength": 80},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "sphere",
                    "kind",
                    "text",
                    "pathHint",
                    "source",
                    "confidence",
                ],
            },
        },
        "recentEvent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "maxLength": 500},
                "eventType": {"type": "string", "maxLength": 60},
                "participants": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "actions": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "objects": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "location": {"type": "string", "maxLength": 160},
                "outcome": {"type": "string", "maxLength": 260},
                "compactText": {"type": "string", "maxLength": 500},
                "canonicalFacts": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string", "maxLength": 180},
                },
                "statusChanges": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "entity": {"type": "string", "maxLength": 120},
                            "state": {"type": "string", "maxLength": 80},
                            "owner": {"type": "string", "maxLength": 120},
                        },
                        "required": ["entity", "state", "owner"],
                    },
                },
            },
            "required": [
                "summary",
                "eventType",
                "participants",
                "actions",
                "objects",
                "location",
                "outcome",
                "compactText",
                "canonicalFacts",
                "statusChanges",
            ],
        },
    },
    "required": ["facts", "recentEvent"],
}
BACKGROUND_STORY_IMAGE_SCENE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene": {
            "type": "string",
            "maxLength": BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS,
        },
    },
    "required": ["scene"],
}


@dataclass(frozen=True)
class BackgroundStoryResult:
    title: str
    summary: str
    story_text: str
    event_type: str
    valence: str
    tags: tuple[str, ...]
    rag_text: str
    story_library_patch: dict[str, Any] | None
    lite_overlay_patch: dict[str, Any] | None
    recent_story_event: dict[str, Any] | None
    prompt_debug: list[dict[str, Any]]
    stat_impacts: tuple[dict[str, Any], ...] = ()
    stat_impact: dict[str, Any] | None = None
    stat_validation: dict[str, Any] | None = None

    def model_dump(self) -> dict[str, Any]:
        stat_impacts = list(self.stat_impacts)
        if not stat_impacts and self.stat_impact:
            stat_impacts = list(
                _normalize_story_stat_impacts(
                    None,
                    legacy=self.stat_impact,
                    valence=self.valence,
                )
            )
        legacy_stat_impact = self.stat_impact or (stat_impacts[0] if stat_impacts else None)
        return {
            "title": self.title,
            "summary": self.summary,
            "storyText": self.story_text,
            "eventType": self.event_type,
            "valence": self.valence,
            "tags": list(self.tags),
            "ragText": self.rag_text,
            "storyLibraryPatch": self.story_library_patch,
            "liteOverlayPatch": self.lite_overlay_patch,
            "recentStoryEvent": self.recent_story_event,
            "promptDebug": self.prompt_debug,
            "statImpacts": stat_impacts,
            "statImpact": legacy_stat_impact,
            "statValidation": self.stat_validation,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_value(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = _compact_spaces(str(value))
    return text[:limit].rstrip()


def _truncate_text(value: str, limit: int) -> str:
    text = _compact_spaces(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _string_list(value: Any, *, limit: int = MAX_DOSSIER_LIST_ITEMS) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=220)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _compact_json(value: Any, *, limit: int = 1400) -> str:
    if value in (None, "", [], {}):
        return ""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _truncate_text(text, limit)


def _select_record(value: Any, keys: tuple[str, ...], *, limit: int = 800) -> dict[str, Any]:
    if not _is_record(value):
        return {}
    result: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str):
            text = _text_value(item, limit=limit)
            if text:
                result[key] = text
        elif isinstance(item, list):
            values = _string_list(item)
            if values:
                result[key] = values
        elif isinstance(item, dict):
            nested = _clean_context_value(item)
            if nested not in (None, "", [], {}):
                result[key] = nested
        else:
            result[key] = item
    return result


def _stage_label(stage: str) -> str:
    return {
        "baby": "малыш",
        "teen": "подросток",
        "adult": "взрослый",
    }.get(stage, stage)


def _mood_label(mood: str) -> str:
    return {
        "idle": "спокойный",
        "happy": "радостный",
        "hungry": "голодный",
        "sad": "грустный",
    }.get(mood, mood)


def _valence_label(valence: str) -> str:
    return {
        "positive": "позитивный",
        "negative": "негативный",
        "neutral": "нейтральный",
        "mixed": "смешанный",
    }.get(valence, valence)


def _visual_phrase(value: Any, *, limit: int = 220) -> str:
    if isinstance(value, list):
        return ", ".join(_string_list(value, limit=5))[:limit].rstrip()
    if isinstance(value, dict):
        return _truncate_text(
            "; ".join(
                f"{_text_value(key, limit=40)}: {_text_value(item, limit=120)}"
                for key, item in value.items()
                if _text_value(item, limit=120)
            ),
            limit,
        )
    return _text_value(value, limit=limit)


def _background_story_image_identity(pet: LocalPetChatContext) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    identity = _select_record(
        bible.get("identity"),
        ("name", "nickname", "one_liner", "role", "species"),
    )
    visual = _select_record(
        bible.get("visual"),
        ("anchors", "colors", "features", "growth_forms", "materials", "proportions"),
    )
    legacy_visual = {
        key: bible.get(key)
        for key in (
            "species",
            "signature",
            "main_colors",
            "signature_features",
            "materials",
            "proportions",
        )
        if bible.get(key) not in (None, "", [], {})
    }
    name = _text_value(pet.name) or _text_value(identity.get("name")) or "безымянный питомец"
    visual_source = visual or legacy_visual
    details = [
        _visual_phrase(identity.get("species"), limit=120),
        _visual_phrase(identity.get("one_liner"), limit=140),
        _visual_phrase(visual_source.get("anchors"), limit=160),
        _visual_phrase(visual_source.get("colors"), limit=120),
        _visual_phrase(visual_source.get("features"), limit=180),
        _visual_phrase(visual_source.get("materials"), limit=120),
        _visual_phrase(visual_source.get("proportions"), limit=160),
    ]
    unique_details: list[str] = []
    seen: set[str] = set()
    for detail in details:
        normalized = _compact_spaces(detail).casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_details.append(detail)
    return _truncate_text(
        (
            f"{name}: {_text_value(pet.description, limit=120)}. "
            f"Стадия: {_stage_label(pet.stage)}, настроение: {_mood_label(pet.mood)}. "
            f"Опорный дизайн: {'; '.join(unique_details)}."
        ),
        360,
    )


def _current_asset_image_url(pet: LocalPetChatContext) -> str:
    asset_images = pet.assetImages
    if not isinstance(asset_images, dict):
        return ""
    stage_images = asset_images.get(pet.stage)
    if not isinstance(stage_images, dict):
        return ""
    return _text_value(stage_images.get(pet.mood), limit=1000)


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

    base_url = _text_value(getattr(settings, "backend_public_url", None)) or _text_value(
        getattr(settings, "webapp_url", None)
    )
    if not base_url:
        return ""

    absolute_url = f"{base_url.rstrip('/')}/{image_url.lstrip('/')}"
    return absolute_url if _is_public_reference_url(absolute_url) else ""


def _current_asset_reference_url(pet: LocalPetChatContext) -> str:
    return _absolute_reference_url(_current_asset_image_url(pet), get_settings())


def _asset_input_references_for_background_story(
    pet: LocalPetChatContext,
) -> list[dict[str, Any]]:
    image_url = _current_asset_reference_url(pet)
    if not image_url:
        return []
    return [{"type": "image_url", "image_url": {"url": image_url}}]


def _background_story_text_for_image_scene(story: BackgroundStoryResult) -> str:
    tags = ", ".join(story.tags)
    return _truncate_text(
        f"""
Название: {story.title}
Кратко: {story.summary}
Сюжет: {story.story_text}
Тип события: {story.event_type}
Тон: {_valence_label(story.valence)}
Теги: {tags or "нет"}
""",
        BACKGROUND_STORY_IMAGE_SCENE_STORY_MAX_CHARS,
    )


def extract_background_story_image_scene(
    story: BackgroundStoryResult,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
    prompt_debug: list[dict[str, Any]] | None = None,
) -> str:
    settings = get_settings()
    openai_client = client or get_openai_client()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты арт-директор для генерации иллюстраций. Не отвечай пользователю. "
                    "Верни только JSON по схеме. Сцена должна быть конкретной, визуальной "
                    "и пригодной как техническое задание художнику.\n\n"
                    f"{tone_prompt_block('imagePrompt')}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{BACKGROUND_STORY_IMAGE_SCENE_INSTRUCTION}\n\n"
                    f"Текст истории:\n{_background_story_text_for_image_scene(story)}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_image_scene",
                "schema": BACKGROUND_STORY_IMAGE_SCENE_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    debug_entry = log_chat_completion_prompt("background_story/image_scene", request_kwargs)
    if prompt_debug is not None:
        prompt_debug.append(debug_entry)
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/image_scene", completion)
    content = completion.choices[0].message.content or "{}"
    scene = _text_value(
        _json_record_from_text(content).get("scene"),
        limit=BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS,
    )
    if not scene:
        raise RuntimeError("BACKGROUND_STORY_IMAGE_SCENE_EMPTY")
    return scene


def build_background_story_image_prompt(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
    scene: str,
) -> str:
    tags = ", ".join(story.tags)
    prompt = f"""
Создай одну цельную законченную иллюстрацию к истории питомца для отправки в Телеграм.
Покажи центральный момент ясно в одном кадре.

История: «{_truncate_text(story.title, 90)}».
Сцена для иллюстрации: {_truncate_text(scene, BACKGROUND_STORY_IMAGE_SCENE_MAX_CHARS)}
Тип события: {_truncate_text(story.event_type, 60)}; тон: {_valence_label(story.valence)}.
Теги: {_truncate_text(tags or "нет", 120)}.

Дизайн персонажа: {_background_story_image_identity(pet)}

Tone style:
{tone_visual_style()}

Базовая визуальная рамка: {BACKGROUND_STORY_IMAGE_STYLE}

Правила: один законченный кадр, не раскадровка, не коллаж, не интерфейс.
Питомец хорошо виден как главный герой. Не превращай питомца в человека, если он
не гуманоидный. Аниме-пропорции только для гуманоидов фона. Сцена, свет и поза
не ломают дизайн персонажа. Без текста, подписей, речевых пузырей, водяных
знаков, логотипов, крови, оружия, хоррора, взрослых тем и чужих персонажей.
""".strip()
    return _truncate_text(prompt, BACKGROUND_STORY_IMAGE_PROMPT_MAX_CHARS)


def generate_background_story_image_bytes(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
) -> bytes:
    scene = extract_background_story_image_scene(story, prompt_debug=story.prompt_debug)
    return generate_image_bytes(
        build_background_story_image_prompt(pet=pet, story=story, scene=scene),
        label="background_story/image",
        input_references=_asset_input_references_for_background_story(pet),
    )


def _clean_context_value(value: Any) -> Any:
    if isinstance(value, str):
        return _text_value(value, limit=500)
    if isinstance(value, list):
        cleaned = [_clean_context_value(item) for item in value[:MAX_DOSSIER_LIST_ITEMS]]
        return [item for item in cleaned if item not in (None, "", [], {})]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"source_urls", "provenance", "dialogue_moves"}:
                continue
            cleaned = _clean_context_value(item)
            if cleaned not in (None, "", [], {}):
                result[str(key)] = cleaned
        return result
    return value


def _lite_overlay_facts(extensions: dict[str, Any]) -> list[str]:
    overlay = extensions.get("lite_overlay") if _is_record(extensions.get("lite_overlay")) else {}
    facts = overlay.get("facts") if _is_record(overlay) else []
    if not isinstance(facts, list):
        return []
    result: list[str] = []
    for fact in facts[-MAX_DOSSIER_LIST_ITEMS:]:
        if not _is_record(fact):
            continue
        text = _text_value(fact.get("text"), limit=360)
        if text:
            result.append(text)
    return result


def _global_story_briefs(
    *,
    pet: LocalPetChatContext,
    query: str | None = None,
) -> list[dict[str, str]]:
    query_text = _compact_spaces(
        query or " ".join([pet.name or "", pet.description, pet.stage, pet.mood])
    )
    result = search_story_library(
        query=query_text,
        pool_hints=[],
        limit=5,
        character_bible=pet.characterBible,
        include_global=True,
        include_overlay=False,
        include_patch=False,
        diverse_pools=True,
    )
    bricks = result.get("bricks") if isinstance(result.get("bricks"), list) else []
    briefs: list[dict[str, str]] = []
    for brick in bricks:
        if not _is_record(brick):
            continue
        name = _text_value(brick.get("name"), limit=120)
        text = _text_value(brick.get("text"), limit=360)
        if name or text:
            briefs.append({"name": name, "text": text})
    return briefs


def _memory_brief(memory_context: LocalPetMemoryContext | None) -> dict[str, Any] | None:
    if not memory_context:
        return None
    memories = [
        {
            "kind": item.kind,
            "text": _text_value(item.text, limit=260),
            "dueAt": item.dueAt,
        }
        for item in memory_context.relevantMemories[:5]
        if _text_value(item.text, limit=260)
    ]
    result = {
        "summary": _text_value(memory_context.summary, limit=600),
        "userProfile": _text_value(memory_context.userProfile, limit=600),
        "relevantMemories": memories,
    }
    return {key: value for key, value in result.items() if value not in ("", [], None)}


def _history_brief(history: list[LocalChatHistoryItem] | None) -> list[dict[str, str]]:
    if not history:
        return []
    return [
        {
            "role": item.role,
            "text": _text_value(item.text, limit=500),
        }
        for item in history[-6:]
        if _text_value(item.text, limit=500)
    ]


def _recent_replies_brief(recent_replies: list[str] | None) -> list[str]:
    if not recent_replies:
        return []
    return [text for text in (_text_value(item, limit=500) for item in recent_replies[-6:]) if text]


def _story_event_briefs(recent_story_events: list[dict[str, Any]] | None) -> list[str]:
    if not recent_story_events:
        return []
    briefs: list[str] = []
    for item in recent_story_events[-8:]:
        if not _is_record(item):
            continue
        parts: list[str] = []
        event_type = _text_value(item.get("eventType"), limit=60)
        if event_type:
            parts.append(f"тип: {event_type}")
        valence = _text_value(item.get("valence"), limit=40)
        if valence:
            parts.append(f"тон исхода: {_valence_label(valence)}")
        participants = _string_list(item.get("participants"), limit=4)
        if participants:
            parts.append(f"участники: {', '.join(participants)}")
        objects = _string_list(item.get("objects"), limit=4)
        if objects:
            parts.append(f"предметы: {', '.join(objects)}")
        location = _text_value(item.get("location"), limit=120)
        if location:
            parts.append(f"место: {location}")
        brief = "; ".join(parts)
        if brief:
            briefs.append(brief)
    return briefs


def _anti_repeat_block(recent_story_events: list[dict[str, Any]] | None) -> str:
    briefs = _story_event_briefs(recent_story_events)
    if not briefs:
        return ""
    lines = "\n".join(f"- {brief}" for brief in briefs)
    return (
        "ANTI_REPEAT: эти события уже происходили. "
        "Используй список только как запрет на повтор, "
        "не как источник новых деталей сюжета. "
        "Не повторяй по сути то же сочетание участник + "
        "тип события + предмет + место + тон исхода.\n"
        f"{lines}"
    )


def _state_params_brief(pet: LocalPetChatContext) -> dict[str, Any]:
    labels = state_param_labels(
        hunger=pet.stats.hunger,
        happiness=pet.stats.happiness,
        energy=pet.stats.energy,
    )
    return {
        "usageRule": state_param_usage_rule(),
        "голод": labels["hunger"],
        "настроение": labels["happiness"],
        "здоровье": labels["energy"],
    }


def _background_context_modes() -> dict[str, str]:
    modes = {
        source: context_source_mode("backgroundStory", source) for source in CONTEXT_SOURCE_KEYS
    }
    # Previous generated pet stories are conversation memory only. Feeding them
    # back into /story makes the story generator repeat its own past outputs.
    modes["storyOverlay"] = "disabled"
    return modes


def _background_context_source_enabled(
    surface: str,
    source: str,
    *,
    router_enabled: bool | None = None,
    auto_default: bool = False,
) -> bool:
    if surface == "backgroundStory" and source == "storyOverlay":
        return False
    return context_source_enabled(
        surface,
        source,
        router_enabled=router_enabled,
        auto_default=auto_default,
    )


def _background_context_plan_from_routing(
    *,
    modes: dict[str, str] | None = None,
    routing: ContextRoutingDecision | None,
) -> ContextPlan:
    return build_context_plan(
        surface="backgroundStory",
        modes=modes or _background_context_modes(),
        routing=routing,
        source_enabled=_background_context_source_enabled,
    )


def _background_routing_payload(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None,
    history: list[LocalChatHistoryItem] | None,
    recent_replies: list[str] | None,
    now_iso: str | None,
    timezone: str | None,
) -> dict[str, Any]:
    pet_payload: dict[str, Any] = {
        "name": pet.name,
        "stage": pet.stage,
    }
    if context_source_enabled("backgroundStory", "stateParams", auto_default=True):
        pet_payload["params"] = _state_params_brief(pet)
    return {
        "surface": "backgroundStory",
        "task": "generate_background_story",
        "eventType": background_story_default_event_type(),
        "toneProfile": tone_context_payload("contextRouting"),
        "now": now_iso or _now_iso(),
        "timezone": timezone,
        "pet": pet_payload,
        "sources": context_routing_sources(),
        "memoryBrief": _memory_brief(memory_context) or {},
        "recentChatHistory": _history_brief(history),
        "recentReplies": _recent_replies_brief(recent_replies),
    }


def _parse_background_routing_payload(value: str) -> ContextRoutingDecision:
    parsed = _json_record_from_text(value)
    sources = parsed.get("sources") if _is_record(parsed.get("sources")) else {}
    enabled: set[str] = set()
    queries: dict[str, str] = {}
    for source in BACKGROUND_ROUTING_SOURCE_IDS:
        item = sources.get(source)
        source_enabled = False
        query = ""
        if isinstance(item, bool):
            source_enabled = item
        elif _is_record(item):
            source_enabled = bool(item.get("enabled"))
            query = _text_value(item.get("query"), limit=500)
        if source_enabled:
            enabled.add(source)
        if query:
            queries[source] = query
    reason = parsed.get("reason")
    return ContextRoutingDecision(
        surface="backgroundStory",
        enabled_sources=frozenset(enabled),
        queries=queries,
        reason=_text_value(reason) if isinstance(reason, str) else "",
        raw=parsed or {"parseError": True, "raw": value[:1000]},
    )


def _plan_background_story_context(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None,
    history: list[LocalChatHistoryItem] | None,
    recent_replies: list[str] | None,
    now_iso: str | None,
    timezone: str | None,
    client: Any,
    model: str,
    timeout: float,
) -> tuple[ContextPlan, dict[str, Any] | None]:
    modes = _background_context_modes()
    if not router_sources_for_auto_modes(modes):
        return (
            _background_context_plan_from_routing(
                modes=modes,
                routing=ContextRoutingDecision(
                    surface="backgroundStory",
                    reason="no_auto_context_sources",
                    raw={"skipped": True, "sourceModes": modes},
                ),
            ),
            None,
        )
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": context_routing_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    _background_routing_payload(
                        pet=pet,
                        memory_context=memory_context,
                        history=history,
                        recent_replies=recent_replies,
                        now_iso=now_iso,
                        timezone=timezone,
                    ),
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_context_routing",
                "schema": BACKGROUND_STORY_ROUTING_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("none"),
    }
    prompt_debug = log_chat_completion_prompt("background_story/context_routing", request_kwargs)
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/context_routing", completion)
    return (
        _background_context_plan_from_routing(
            modes=modes,
            routing=_parse_background_routing_payload(
                completion.choices[0].message.content or "{}"
            ),
        ),
        prompt_debug,
    )


def character_dossier_for_background_story(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None = None,
    history: list[LocalChatHistoryItem] | None = None,
    recent_replies: list[str] | None = None,
    now_iso: str | None = None,
    timezone: str | None = None,
    context_plan: ContextPlan | None = None,
    source_flags: dict[str, bool] | None = None,
    include_story_library: bool | None = None,
    story_library_query: str | None = None,
) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    if context_plan is not None:
        sources = {source: context_plan.includes(source) for source in CONTEXT_SOURCE_KEYS}
        if include_story_library is None:
            include_story_library = context_plan.includes("storyLibrary")
        if story_library_query is None:
            story_library_query = context_plan.query("worldContext")
    else:
        sources = source_flags if source_flags is not None else background_story_source_flags()

    def enabled(source: str) -> bool:
        return sources.get(source, True)

    current_state: dict[str, Any] = {
        "name": pet.name,
        "stage": pet.stage,
    }
    if enabled("stateParams"):
        current_state["params"] = _state_params_brief(pet)
    dossier: dict[str, Any] = {
        "now": now_iso or _now_iso(),
        "timezone": timezone,
        "currentState": current_state,
    }

    if enabled("characterProfile"):
        dossier.update(
            {
                "description": pet.description,
                "identity": _select_record(
                    bible.get("identity"),
                    ("name", "nickname", "one_liner", "role", "species"),
                ),
                "signature": _text_value(bible.get("signature"), limit=300),
                "species": _text_value(bible.get("species"), limit=200),
                "visual": _select_record(
                    bible.get("visual"),
                    (
                        "anchors",
                        "colors",
                        "features",
                        "growth_forms",
                        "materials",
                        "proportions",
                    ),
                ),
                "innerState": _select_record(
                    bible.get("inner_state"),
                    (
                        "core_want",
                        "inner_conflict",
                        "fears",
                        "comfort_actions",
                        "drives",
                    ),
                ),
                "lore": _select_record(
                    lore,
                    (
                        "origin",
                        "home",
                        "world",
                        "relationships",
                        "inner_life",
                        "story_seeds",
                        "growth_arc",
                    ),
                ),
                "world": _select_record(
                    bible.get("world"),
                    (
                        "habitat",
                        "home",
                        "objects",
                        "relationships",
                        "routines",
                        "story_seeds",
                    ),
                ),
            }
        )
    if enabled("liteOverlay"):
        dossier["liteFacts"] = _lite_overlay_facts(extensions)
    if include_story_library is None:
        include_story_library = context_source_enabled(
            "backgroundStory",
            "storyLibrary",
            auto_default=False,
        )
    if include_story_library:
        dossier["globalStoryBricks"] = _global_story_briefs(
            pet=pet,
            query=story_library_query,
        )
    memory = _memory_brief(memory_context) if enabled("userMemory") else None
    if memory:
        dossier["userMemory"] = memory
    recent_history = _history_brief(history) if enabled("chatHistory") else []
    if recent_history:
        dossier["recentChatHistory"] = recent_history
    recent_reply_brief = _recent_replies_brief(recent_replies) if enabled("recentReplies") else []
    if recent_reply_brief:
        dossier["recentReplies"] = recent_reply_brief

    compact = {key: value for key, value in dossier.items() if value not in (None, "", [], {})}
    return _truncate_text(
        json.dumps(compact, ensure_ascii=False, indent=2, default=str),
        MAX_CHARACTER_DOSSIER_CHARS,
    )


def _json_record_from_text(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=40)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= 8:
            break
    return tuple(result)


def _story_stat_damage(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0
    if amount == 0:
        return 0
    return max(1, min(STORY_STAT_MAX_SINGLE_DAMAGE, round(abs(amount))))


def _iter_raw_story_stat_impacts(
    value: Any,
    *,
    legacy: Any = None,
) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    items = [item for item in raw_items if _is_record(item)]
    if items:
        return items
    if not _is_record(legacy):
        return []
    applies = legacy.get("applies") is True and legacy.get("isNegativeOutcome") is True
    stat = _text_value(legacy.get("stat"), limit=40)
    if not applies or stat not in STORY_STAT_KEYS:
        return []
    return [
        {
            "stat": stat,
            "amount": -_story_stat_damage(legacy.get("amount")),
            "reason": legacy.get("reason"),
        }
    ]


def _normalize_story_stat_impacts(
    value: Any,
    *,
    legacy: Any = None,
    valence: str,
) -> tuple[dict[str, Any], ...]:
    if valence not in {"negative", "mixed"}:
        return ()

    result: list[dict[str, Any]] = []
    seen_stats: set[str] = set()
    total_damage = 0
    for raw in _iter_raw_story_stat_impacts(value, legacy=legacy):
        stat = _text_value(raw.get("stat"), limit=40)
        if stat not in STORY_STAT_KEYS or stat in seen_stats:
            continue
        damage = _story_stat_damage(raw.get("amount"))
        if damage <= 0:
            continue
        remaining_total = STORY_STAT_MAX_TOTAL_DAMAGE - total_damage
        if remaining_total <= 0:
            break
        applied_damage = min(damage, remaining_total)
        result.append(
            {
                "stat": stat,
                "amount": -applied_damage,
                "reason": _text_value(raw.get("reason"), limit=280),
            }
        )
        seen_stats.add(stat)
        total_damage += applied_damage
        if len(result) >= STORY_STAT_MAX_ITEMS:
            break
    return tuple(result)


def _validate_story_stat_impacts_against_text(
    stat_impacts: tuple[dict[str, Any], ...],
    *,
    summary: str,
    story_text: str,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    validation = {
        "dropped": False,
        "reason": "",
    }
    if not stat_impacts:
        return stat_impacts, validation
    combined_text = f"{summary}\n{story_text}"
    if STORY_STAT_NO_LOSS_RE.search(combined_text) and not STORY_STAT_NEGATIVE_EVIDENCE_RE.search(
        combined_text
    ):
        validation = {
            "dropped": True,
            "reason": "explicit_no_loss_text_without_negative_evidence",
        }
        logger.debug("background_story_stat_impacts_dropped: %s", validation["reason"])
        return (), validation
    return stat_impacts, validation


def _normalize_story_payload(payload: dict[str, Any]) -> BackgroundStoryResult:
    max_story_chars = max(200, background_story_max_story_chars())
    max_rag_chars = max(120, background_story_max_rag_chars())
    fallback_event_type = background_story_default_event_type()

    title = _text_value(payload.get("title"), limit=120) or "Фоновое событие"
    summary = _text_value(payload.get("summary"), limit=360)
    story_text = _truncate_text(_text_value(payload.get("storyText"), limit=2200), max_story_chars)
    event_type = _text_value(payload.get("eventType"), limit=60) or fallback_event_type
    valence = _text_value(payload.get("valence"), limit=20) or "mixed"
    if valence not in {"negative", "neutral", "positive", "mixed"}:
        valence = "mixed"
    tags = _clean_tags(payload.get("tags"))
    rag_text = _truncate_text(_text_value(payload.get("ragText"), limit=1000), max_rag_chars)

    if not summary:
        summary = _truncate_text(story_text, 260) if story_text else title
    if not story_text:
        story_text = summary
    if not rag_text:
        rag_text = summary
    stat_impacts = _normalize_story_stat_impacts(
        payload.get("statImpacts"),
        legacy=payload.get("statImpact"),
        valence=valence,
    )
    stat_impacts, stat_validation = _validate_story_stat_impacts_against_text(
        stat_impacts,
        summary=summary,
        story_text=story_text,
    )

    return BackgroundStoryResult(
        title=title,
        summary=summary,
        story_text=story_text,
        event_type=event_type,
        valence=valence,
        tags=tags,
        rag_text=rag_text,
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
        stat_impacts=stat_impacts,
        stat_impact=stat_impacts[0] if stat_impacts else None,
        stat_validation=stat_validation,
    )


def _clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _aftermath_character_context(pet: LocalPetChatContext) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    payload = {
        "name": pet.name,
        "description": pet.description,
        "stage": pet.stage,
        "currentState": {
            "name": pet.name,
            "stage": pet.stage,
            "params": _state_params_brief(pet),
        },
        "identity": _select_record(
            bible.get("identity"),
            ("name", "nickname", "one_liner", "role", "species"),
        ),
        "signature": _text_value(bible.get("signature"), limit=300),
        "species": _text_value(bible.get("species"), limit=200),
        "visual": _select_record(
            bible.get("visual"),
            ("anchors", "colors", "features", "growth_forms", "materials", "proportions"),
        ),
        "innerState": _select_record(
            bible.get("inner_state"),
            ("core_want", "inner_conflict", "fears", "comfort_actions", "drives"),
        ),
        "lore": _select_record(
            lore,
            ("origin", "home", "world", "relationships", "inner_life", "growth_arc"),
        ),
        "world": _select_record(
            bible.get("world"),
            ("habitat", "home", "objects", "relationships", "routines"),
        ),
        "liteOverlay": _clean_context_value(extensions.get("lite_overlay"))
        if _is_record(extensions.get("lite_overlay"))
        else {},
    }
    compact = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return _truncate_text(
        json.dumps(compact, ensure_ascii=False, indent=2, default=str),
        MAX_AFTERMATH_CONTEXT_CHARS,
    )


def _aftermath_story_payload(result: BackgroundStoryResult) -> str:
    return _truncate_text(
        json.dumps(
            {
                "title": result.title,
                "summary": result.summary,
                "storyText": result.story_text,
                "eventType": result.event_type,
                "valence": result.valence,
                "tags": list(result.tags),
                "statImpacts": list(result.stat_impacts),
                "ragText": result.rag_text,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        MAX_AFTERMATH_CONTEXT_CHARS,
    )


def _status_change_list(value: Any, *, limit: int = 5) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not _is_record(item):
            continue
        entity = _text_value(item.get("entity"), limit=120)
        state = _text_value(item.get("state"), limit=80)
        owner = _text_value(item.get("owner"), limit=120)
        if not entity or not state:
            continue
        result.append({"entity": entity, "state": state, "owner": owner})
        if len(result) >= limit:
            break
    return result


def _recent_story_event_id(*, created_at: str, story: BackgroundStoryResult) -> str:
    seed = f"{created_at}:{story.title}:{story.summary}"
    suffix = re.sub(r"[^0-9a-z]+", "", seed.casefold())[:48]
    return f"evt_{suffix or 'story'}"


def _normalize_recent_story_event(
    value: Any,
    *,
    story: BackgroundStoryResult,
) -> dict[str, Any]:
    item = value if _is_record(value) else {}
    summary = _text_value(item.get("summary"), limit=500) or story.summary
    compact_text = (
        _text_value(item.get("compactText"), limit=500)
        or summary
        or _truncate_text(story.story_text, 500)
    )
    canonical_facts = _string_list(item.get("canonicalFacts"), limit=5)
    actions = _string_list(item.get("actions"), limit=6)
    outcome = _text_value(item.get("outcome"), limit=260)
    if not canonical_facts:
        canonical_facts = [*actions[:4], outcome][:5]
        canonical_facts = [fact for fact in canonical_facts if fact]
    created_at = _now_iso()
    event = {
        "id": _text_value(item.get("id"), limit=120)
        or _recent_story_event_id(created_at=created_at, story=story),
        "title": story.title,
        "summary": summary,
        "compactText": compact_text,
        "eventType": _text_value(item.get("eventType"), limit=60) or story.event_type,
        "valence": story.valence,
        "participants": _string_list(item.get("participants"), limit=6),
        "actions": actions,
        "objects": _string_list(item.get("objects"), limit=6),
        "location": _text_value(item.get("location"), limit=160),
        "outcome": outcome,
        "canonicalFacts": canonical_facts,
        "statusChanges": _status_change_list(item.get("statusChanges"), limit=5),
        "statImpacts": list(story.stat_impacts),
        "tags": list(story.tags),
        "createdAt": created_at,
        "source": "background_story",
    }
    if not event["summary"]:
        event["summary"] = _truncate_text(story.story_text, 500)
    if not event["compactText"]:
        event["compactText"] = event["summary"]
    return {
        key: item_value for key, item_value in event.items() if item_value not in (None, "", [], {})
    }


def _parse_aftermath_extraction_payload(
    raw_content: str,
    *,
    story: BackgroundStoryResult,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    parsed = _json_record_from_text(raw_content)
    raw_facts = parsed.get("facts")

    facts: list[dict[str, Any]] = []
    if isinstance(raw_facts, list):
        for raw_fact in raw_facts:
            if not _is_record(raw_fact):
                continue
            if _clamp_float(raw_fact.get("confidence"), 0.0) < AFTERMATH_CONFIDENCE_THRESHOLD:
                continue
            fact = dict(raw_fact)
            fact["source"] = "background_story_aftermath"
            facts.append(fact)
    patch = overlay_patch_from_extracted_facts(
        facts,
        default_source="background_story_aftermath",
    )
    recent_event = _normalize_recent_story_event(parsed.get("recentEvent"), story=story)
    return patch, recent_event


def _extract_background_story_aftermath_patch(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": background_story_aftermath_extraction_system_prompt(),
            },
            {
                "role": "user",
                "content": background_story_aftermath_extraction_user_prompt(
                    {
                        "character_context": _aftermath_character_context(pet),
                        "story_payload": _aftermath_story_payload(story),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_aftermath_extraction",
                "schema": BACKGROUND_STORY_AFTERMATH_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(get_settings().openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("background_story/aftermath_extraction", request_kwargs)
    )
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/aftermath_extraction", completion)
    return _parse_aftermath_extraction_payload(
        completion.choices[0].message.content or "{}",
        story=story,
    )


def generate_background_story(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None = None,
    history: list[LocalChatHistoryItem] | None = None,
    recent_replies: list[str] | None = None,
    recent_story_events: list[dict[str, Any]] | None = None,
    now_iso: str | None = None,
    timezone: str | None = None,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> BackgroundStoryResult:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    context_plan, routing_debug = _plan_background_story_context(
        pet=pet,
        memory_context=memory_context,
        history=history,
        recent_replies=recent_replies,
        now_iso=now_iso,
        timezone=timezone,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    character = character_dossier_for_background_story(
        pet=pet,
        memory_context=memory_context,
        history=history,
        recent_replies=recent_replies,
        now_iso=now_iso,
        timezone=timezone,
        context_plan=context_plan,
    )
    user_content = background_story_user_prompt(
        {
            "character": character,
            "event_type": background_story_default_event_type(),
        }
    )
    anti_repeat = _anti_repeat_block(recent_story_events)
    if anti_repeat:
        user_content = f"{user_content}\n\n{anti_repeat}"
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{background_story_system_prompt()}\n\n"
                    f"{tone_prompt_block('backgroundStory')}"
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story",
                "schema": BACKGROUND_STORY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug = [item for item in (routing_debug,) if item is not None]
    prompt_debug.append(log_chat_completion_prompt("background_story/generate", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/generate", completion)
    content = completion.choices[0].message.content or "{}"
    raw_story_payload = _json_record_from_text(content)
    result = _normalize_story_payload(raw_story_payload)
    lite_overlay_patch: dict[str, Any] | None = None
    recent_story_event: dict[str, Any] | None = None
    prompt_debug.append(
        {
            "event": "background_story_stat_impacts",
            "rawStatImpacts": raw_story_payload.get("statImpacts"),
            "legacyStatImpact": raw_story_payload.get("statImpact"),
            "appliedStatImpacts": list(result.stat_impacts),
            "statValidation": result.stat_validation,
            "valence": result.valence,
        }
    )
    try:
        lite_overlay_patch, recent_story_event = _extract_background_story_aftermath_patch(
            pet=pet,
            story=result,
            client=openai_client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    except Exception:
        logger.exception("background_story_after_extraction failed")
        recent_story_event = _normalize_recent_story_event(None, story=result)
    return BackgroundStoryResult(
        title=result.title,
        summary=result.summary,
        story_text=result.story_text,
        event_type=result.event_type,
        valence=result.valence,
        tags=result.tags,
        rag_text=result.rag_text,
        story_library_patch=result.story_library_patch,
        lite_overlay_patch=lite_overlay_patch,
        recent_story_event=recent_story_event,
        prompt_debug=prompt_debug,
        stat_impacts=result.stat_impacts,
        stat_impact=result.stat_impact,
        stat_validation=result.stat_validation,
    )
