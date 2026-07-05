from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.schemas import LiteFactExtractionRequest, LocalChatRequest, LocalChatResponse
from app.services.openai_service import chat_reasoning_effort_kwargs, get_openai_client
from app.services.pet_memory.models import LocalChatDebug
from app.services.pet_reply_engine.age_message_examples import (
    categories_for_reply,
    phrases_for_categories,
)
from app.services.pet_reply_engine.models import (
    PetPersonality,
    PetRecentMessage,
    PetReplyInput,
    PetReplyPet,
    PetStats,
    PetVisualIdentity,
)
from app.services.pet_reply_engine.reply_limits import MAX_REPLY_CHARS, clamp_reply_text
from app.services.pet_reply_engine.state_interpreter import (
    clamp_stat,
    energy_band,
    hunger_band,
)
from app.services.prompt_debug import log_chat_completion_prompt

MAX_LITE_TOOL_ROUNDS = 3
MAX_LITE_BABY_EXAMPLES = 8
MAX_LITE_EXTRACTION_CONTEXT_CHARS = 12000

LITE_FACT_SPHERES = ("character", "appearance", "world", "relationship")
LITE_FACT_KINDS = (
    "character_fact",
    "appearance_fact",
    "world_fact",
    "relationship_fact",
)

LITE_AGE_ROLE_HINTS = {
    "baby": "малыш такого существа",
    "teen": "подросток такого существа",
    "adult": "взрослый, сформировавшийся представитель такого существа",
}

LITE_RAG_REQUEST_PATTERN = re.compile(
    r"("
    r"\bлор\b|канон|мир|дом|жив[её]шь|где\s+ты|откуда|прошл|истори|"
    r"что\s+ты\s+е[шс]|чем\s+пита|любим|нравит|боишь|страх|друг|семь|"
    r"родствен|знаком|тело|выгляд|из\s+чего|устроен|механик|способност|"
    r"умеешь|сила|привыч|секрет|почему\s+ты|кто\s+ты|как\s+тебя"
    r")",
    re.IGNORECASE,
)

LITE_WORLD_REQUEST_PATTERN = re.compile(
    r"("
    r"\bмир\b|мире|дом|жив[её]шь|где\s+ты|где\s+твой|откуда|место|"
    r"habitat|home|world|where\s+do\s+you\s+live"
    r")",
    re.IGNORECASE,
)

TECHNICAL_WORLD_TEXT_PATTERN = re.compile(
    r"("
    r"source_descriptions|Home/habitat details must be inferred|"
    r"World facts come from|No extra origin is invented|"
    r"Use source_descriptions|template_do_not_copy|source_text_do_not_copy|"
    r"безопасная среда для формы|No relationship lore is added"
    r")",
    re.IGNORECASE,
)

LITE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_character_json",
            "description": (
                "Read character JSON only when the current user explicitly asks about lore, "
                "world, body, mechanics, food, home, origin, friends, fears, habits, "
                "preferences, or stable character facts. Do not use for ordinary small talk."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "characterBible",
                                "liteOverlay",
                                "memory",
                                "loreMemories",
                            ],
                        },
                    }
                },
                "required": ["sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_character_json",
            "description": (
                "Append a small stable fact to the Lite overlay after you organically invented "
                "or clarified it in this Lite chat. Use for reusable character/lore details, "
                "not temporary emotions or one-off phrasing."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "lore_fact",
                            "character_fact",
                            "preference",
                            "habit",
                            "relationship",
                            "body_fact",
                        ],
                    },
                    "text": {"type": "string"},
                    "pathHint": {"type": ["string", "null"]},
                    "source": {"type": ["string", "null"]},
                },
                "required": ["kind", "text", "pathHint", "source"],
            },
        },
    },
]

LITE_FACT_EXTRACTION_SCHEMA: dict[str, Any] = {
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
                    "sphere": {
                        "type": "string",
                        "enum": list(LITE_FACT_SPHERES),
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(LITE_FACT_KINDS),
                    },
                    "text": {
                        "type": "string",
                        "maxLength": 500,
                    },
                    "pathHint": {
                        "type": "string",
                        "maxLength": 120,
                    },
                    "source": {
                        "type": "string",
                        "maxLength": 80,
                    },
                },
                "required": ["sphere", "kind", "text", "pathHint", "source"],
            },
        }
    },
    "required": ["facts"],
}

LITE_WORLD_SEED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "worldText": {
            "type": "string",
            "maxLength": 500,
        },
    },
    "required": ["worldText"],
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}…"


def _short_character_description(payload: LocalChatRequest) -> str:
    pet = payload.pet
    description = _compact_spaces(pet.description)
    name = _compact_spaces(pet.name or "")
    return f"{name}, {description}" if name else description


def _state_role_modifier(payload: LocalChatRequest) -> str | None:
    stats = payload.pet.stats
    if payload.pet.mood == "hungry" or hunger_band(clamp_stat(stats.hunger)) == "low":
        return "голодный"
    if payload.pet.mood == "happy":
        if energy_band(stats.energy) == "low":
            return "радостный, но уставший"
        return "радостный, энергичный, полный сил"
    if payload.pet.mood == "sad":
        return "грустный, притихший"
    if energy_band(stats.energy) == "low":
        return "уставший"
    return None


def _age_role_hint(payload: LocalChatRequest) -> str:
    return LITE_AGE_ROLE_HINTS[payload.pet.stage]


def _lite_reply_input_for_examples(payload: LocalChatRequest) -> PetReplyInput:
    bible = payload.pet.characterBible if _is_record(payload.pet.characterBible) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else None
    return PetReplyInput(
        user_action="chat_message",
        user_text=payload.message,
        recent_messages=tuple(
            PetRecentMessage(role=item.role, text=item.text) for item in payload.history[-8:]
        ),
        lore_memories=tuple(payload.pet.loreMemories),
        pet=PetReplyPet(
            age_stage=payload.pet.stage,
            mood=payload.pet.mood,
            stats=PetStats(
                hunger=payload.pet.stats.hunger,
                happiness=payload.pet.stats.happiness,
                energy=payload.pet.stats.energy,
                cleanliness=payload.pet.stats.cleanliness,
            ),
            visual_identity=PetVisualIdentity(
                raw_description=payload.pet.description,
                species=payload.pet.description,
            ),
            personality=PetPersonality(),
            lore=lore,
            name=payload.pet.name,
            character_profile_v2=bible if bible else None,
            effective_character_bible=bible if bible else None,
        ),
    )


def _baby_phrase_examples_for_prompt(payload: LocalChatRequest) -> str | None:
    if payload.pet.stage != "baby":
        return None

    reply_input = _lite_reply_input_for_examples(payload)
    categories = categories_for_reply(reply_input)
    examples = phrases_for_categories(
        reply_input,
        categories,
        per_category=2,
        max_examples=MAX_LITE_BABY_EXAMPLES,
    )
    if not examples:
        return None

    lines = "\n".join(f"- {phrase}" for _, phrase in examples)
    return (
        "Примеры детской манеры из датасета. Можно брать ритм и характер, "
        f"но не обязательно копировать дословно:\n{lines}"
    )


def _lite_tools_for_message(text: str) -> list[dict[str, Any]] | None:
    return LITE_TOOLS if LITE_RAG_REQUEST_PATTERN.search(text) else None


def _history_messages(payload: LocalChatRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "assistant" if item.role == "pet" else "user",
            "content": item.text,
        }
        for item in payload.history[-12:]
    ]


def build_lite_chat_messages(payload: LocalChatRequest) -> list[dict[str, str]]:
    system_content = (
        f"Отвечай мне как {_short_character_description(payload)}. "
        f"Сейчас ты {_age_role_hint(payload)}."
    )
    state_modifier = _state_role_modifier(payload)
    if state_modifier:
        system_content = f"{system_content} Ты сейчас {state_modifier}."
    system_content = (
        f"{system_content} Ответ максимум {MAX_REPLY_CHARS} символов; "
        "можно короче, даже одной фразой."
    )
    character_seed = _lite_character_seed_for_prompt(payload)
    if character_seed:
        system_content = f"{system_content}\n\nОснова характера: {character_seed}"
    baby_examples = _baby_phrase_examples_for_prompt(payload)
    if baby_examples:
        system_content = f"{system_content}\n\n{baby_examples}"

    return [
        {
            "role": "system",
            "content": system_content,
        },
        *_history_messages(payload),
        {"role": "user", "content": payload.message},
    ]


def _lite_overlay_from(payload: LocalChatRequest) -> dict[str, Any]:
    bible = payload.pet.characterBible if _is_record(payload.pet.characterBible) else {}
    extensions = bible.get("extensions") if isinstance(bible, dict) else None
    if not _is_record(extensions):
        return {}
    overlay = extensions.get("lite_overlay")
    return dict(overlay) if _is_record(overlay) else {}


def _text_value(value: Any) -> str:
    return _compact_spaces(str(value or ""))


def _is_technical_world_text(value: str) -> bool:
    text = _text_value(value)
    return not text or text in {"-", "—"} or bool(TECHNICAL_WORLD_TEXT_PATTERN.search(text))


def _collect_clean_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _text_value(value)
        return [] if _is_technical_world_text(text) else [text]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_collect_clean_strings(item))
        return result
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_collect_clean_strings(item))
        return result
    return []


def _sanitize_technical_world_text(value: Any) -> Any:
    if isinstance(value, str):
        return "" if _is_technical_world_text(value) else value
    if isinstance(value, list):
        cleaned = [_sanitize_technical_world_text(item) for item in value]
        return [item for item in cleaned if item not in ("", None, [], {})]
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _sanitize_technical_world_text(item)) not in ("", None, [], {})
        }
    return value


def _existing_world_texts(payload: LocalChatRequest) -> list[str]:
    texts: list[str] = []
    overlay = _lite_overlay_from(payload)
    texts.extend(_collect_clean_strings(overlay.get("spheres", {}).get("world", {})))
    texts.extend(
        _collect_clean_strings(
            [
                fact
                for fact in overlay.get("facts", [])
                if isinstance(fact, dict)
                and (
                    fact.get("sphere") == "world"
                    or fact.get("kind") in {"world_fact", "lore_fact"}
                )
            ]
        )
    )

    bible = payload.pet.characterBible if _is_record(payload.pet.characterBible) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    profile_world = bible.get("world") if _is_record(bible.get("world")) else {}
    texts.extend(_collect_clean_strings(lore.get("world") if _is_record(lore) else {}))
    texts.extend(_collect_clean_strings(lore.get("home") if _is_record(lore) else {}))
    texts.extend(_collect_clean_strings(profile_world))
    return [text for text in texts if len(text) >= 12]


def _lite_character_seed_for_prompt(payload: LocalChatRequest) -> str | None:
    overlay = _lite_overlay_from(payload)
    texts: list[str] = []
    seen: set[str] = set()

    def add_text(raw_text: Any) -> None:
        text = _text_value(raw_text)
        key = text.casefold()
        if text and key not in seen and not _is_technical_world_text(text):
            seen.add(key)
            texts.append(text)

    character_sphere = overlay.get("spheres", {}).get("character", {})
    if isinstance(character_sphere, dict) and isinstance(character_sphere.get("facts"), list):
        for fact in character_sphere["facts"]:
            if isinstance(fact, dict):
                add_text(fact.get("text"))
    for fact in overlay.get("facts", []):
        if not isinstance(fact, dict):
            continue
        if fact.get("sphere") != "character" and fact.get("kind") not in {
            "character_fact",
            "preference",
            "habit",
        }:
            continue
        add_text(fact.get("text"))
    if not texts:
        return None
    return _truncate_text(" ".join(texts[:2]), 600)


def _world_seed_messages(payload: LocalChatRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Ты придумываешь стартовый лор мира для вымышленного питомца. "
                "Верни только JSON по схеме. Придумай конкретный, органичный мир и дом "
                "для существа, без ссылки на датасеты, шаблоны или отсутствие информации. "
                "worldText должен быть на русском, 1-3 предложения, с ощущением места, "
                "дома/среды обитания и одной-двумя деталями, которые потом можно считать "
                "устойчивым лором персонажа."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Имя: {payload.pet.name or 'без имени'}\n"
                f"Существо: {payload.pet.description}\n"
                f"Возрастная стадия: {_age_role_hint(payload)}\n"
                f"Состояние: {_state_role_modifier(payload) or payload.pet.mood}\n"
                f"Вопрос пользователя: {payload.message}"
            ),
        },
    ]


def _parse_world_seed_text(raw_content: str) -> str | None:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    text = _text_value(parsed.get("worldText"))
    return _truncate_text(text, 500) if text else None


def _world_seed_overlay_patch(
    payload: LocalChatRequest,
    *,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> dict[str, Any] | None:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": _world_seed_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "lite_world_seed",
                "schema": LITE_WORLD_SEED_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(get_settings().openai_chat_reasoning_effort),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/lite_world_seed", request_kwargs))
    completion = client.chat.completions.create(**request_kwargs)
    world_text = _parse_world_seed_text(completion.choices[0].message.content or "")
    if not world_text:
        return None

    raw_fact = {
        "sphere": "world",
        "kind": "world_fact",
        "text": world_text,
        "pathHint": "lite_overlay.spheres.world",
        "source": "chatgpt_world_seed",
    }
    patch = _overlay_patch_from_extracted_facts([raw_fact])
    if not patch:
        return None
    patch["worldSeed"] = {
        "source": "chatgpt",
        "createdAt": _now_iso(),
    }
    return patch


def _merge_lite_overlay_patch(target: dict[str, Any], patch: dict[str, Any] | None) -> None:
    if not patch:
        return

    existing_keys = {
        _lite_fact_key(fact)
        for fact in target.get("facts", [])
        if isinstance(fact, dict)
    }
    facts = target.setdefault("facts", [])
    if not isinstance(facts, list):
        facts = []
        target["facts"] = facts
    for fact in patch.get("facts", []):
        if not isinstance(fact, dict):
            continue
        key = _lite_fact_key(fact)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        facts.append(fact)

    spheres = target.setdefault("spheres", {})
    if not isinstance(spheres, dict):
        spheres = {}
        target["spheres"] = spheres
    patch_spheres = patch.get("spheres")
    if isinstance(patch_spheres, dict):
        for sphere, patch_sphere in patch_spheres.items():
            if not isinstance(patch_sphere, dict):
                continue
            target_sphere = spheres.setdefault(sphere, {})
            if not isinstance(target_sphere, dict):
                target_sphere = {}
                spheres[sphere] = target_sphere
            _merge_lite_overlay_patch(target_sphere, patch_sphere)

    if isinstance(patch.get("worldSeed"), dict):
        target["worldSeed"] = patch["worldSeed"]


def _lite_character_bible_for_read(
    payload: LocalChatRequest,
    world_seed_patch: dict[str, Any] | None,
) -> dict[str, Any]:
    bible = deepcopy(payload.pet.characterBible) if _is_record(payload.pet.characterBible) else {}
    bible = _sanitize_technical_world_text(bible)
    if world_seed_patch:
        world_facts = world_seed_patch.get("facts") if isinstance(world_seed_patch, dict) else []
        world_text = ""
        if isinstance(world_facts, list) and world_facts and isinstance(world_facts[0], dict):
            world_text = _text_value(world_facts[0].get("text"))
        if world_text:
            lore = bible.setdefault("lore", {})
            if isinstance(lore, dict):
                lore["world"] = {
                    **(lore.get("world") if isinstance(lore.get("world"), dict) else {}),
                    "story": world_text,
                    "environment": world_text,
                }
                lore["home"] = {
                    **(lore.get("home") if isinstance(lore.get("home"), dict) else {}),
                    "story": world_text,
                }
            profile_world = bible.setdefault("world", {})
            if isinstance(profile_world, dict):
                profile_world.setdefault("habitat", world_text)
                profile_world.setdefault("home", world_text)
    return bible


def _read_character_json(
    payload: LocalChatRequest,
    arguments: dict[str, Any],
    overlay_patch: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
    prompt_debug: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_sections = arguments.get("sections")
    sections = set(raw_sections if isinstance(raw_sections, list) else [])
    if not sections:
        sections = {"characterBible", "liteOverlay", "memory", "loreMemories"}

    should_seed_world = (
        bool(LITE_WORLD_REQUEST_PATTERN.search(payload.message))
        and not _existing_world_texts(payload)
    )
    world_seed_patch = (
        _world_seed_overlay_patch(
            payload,
            client=client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
        if should_seed_world and client and model and timeout and prompt_debug is not None
        else None
    )
    if world_seed_patch and overlay_patch is not None:
        _merge_lite_overlay_patch(overlay_patch, world_seed_patch)

    result: dict[str, Any] = {
        "description": payload.pet.description,
        "name": payload.pet.name,
    }
    if "characterBible" in sections:
        result["characterBible"] = _lite_character_bible_for_read(payload, world_seed_patch)
    if "liteOverlay" in sections:
        overlay = _lite_overlay_from(payload)
        if world_seed_patch:
            overlay = dict(overlay)
            _merge_lite_overlay_patch(overlay, world_seed_patch)
        result["liteOverlay"] = overlay
    if world_seed_patch:
        result["worldInfo"] = {
            "createdByChatGPT": True,
            "patch": world_seed_patch,
        }
    if "memory" in sections:
        result["memory"] = payload.pet.memory.model_dump() if payload.pet.memory else {}
    if "loreMemories" in sections:
        result["loreMemories"] = payload.pet.loreMemories
    return result


def _normalized_fact(arguments: dict[str, Any]) -> dict[str, Any] | None:
    text = _compact_spaces(str(arguments.get("text") or ""))
    if not text:
        return None
    kind = str(arguments.get("kind") or "lore_fact")
    if kind not in {
        "lore_fact",
        "character_fact",
        "preference",
        "habit",
        "relationship",
        "body_fact",
    }:
        kind = "lore_fact"
    path_hint = arguments.get("pathHint")
    source = arguments.get("source")
    sphere = {
        "lore_fact": "world",
        "character_fact": "character",
        "preference": "character",
        "habit": "character",
        "relationship": "relationship",
        "body_fact": "appearance",
    }.get(kind, "character")
    return {
        "sphere": sphere,
        "kind": kind,
        "text": text,
        "pathHint": str(path_hint).strip() if path_hint else _lite_fact_path_hint(sphere),
        "source": str(source).strip() if source else "invented_in_lite_chat",
        "createdAt": _now_iso(),
    }


def _append_lite_fact(
    overlay_patch: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    fact = _normalized_fact(arguments)
    if not fact:
        return {"saved": False, "reason": "empty_text"}
    facts = overlay_patch.setdefault("facts", [])
    if isinstance(facts, list):
        facts.append(fact)
    sphere = fact.get("sphere")
    if isinstance(sphere, str) and sphere:
        spheres = overlay_patch.setdefault("spheres", {})
        if isinstance(spheres, dict):
            sphere_payload = spheres.setdefault(sphere, {})
            if isinstance(sphere_payload, dict):
                sphere_facts = sphere_payload.setdefault("facts", [])
                if isinstance(sphere_facts, list):
                    sphere_facts.append(fact)
    return {"saved": True, "fact": fact}


def _lite_fact_path_hint(sphere: str) -> str:
    return f"lite_overlay.spheres.{sphere}"


def _default_kind_for_sphere(sphere: str) -> str:
    if sphere == "appearance":
        return "appearance_fact"
    if sphere == "world":
        return "world_fact"
    if sphere == "relationship":
        return "relationship_fact"
    return "character_fact"


def _normalized_extracted_fact(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None

    text = _compact_spaces(str(value.get("text") or ""))
    if not text:
        return None

    sphere = str(value.get("sphere") or "character").strip()
    if sphere not in LITE_FACT_SPHERES:
        sphere = "character"

    kind = str(value.get("kind") or "").strip()
    if kind not in LITE_FACT_KINDS:
        kind = _default_kind_for_sphere(sphere)

    path_hint = _compact_spaces(str(value.get("pathHint") or "")) or _lite_fact_path_hint(sphere)
    source = _compact_spaces(str(value.get("source") or "")) or "lite_post_reply_extractor"

    return {
        "sphere": sphere,
        "kind": kind,
        "text": text,
        "pathHint": path_hint,
        "source": source,
        "createdAt": _now_iso(),
    }


def _lite_fact_key(fact: dict[str, Any]) -> str:
    return f"{fact.get('sphere', 'character')}:{fact.get('text', '')}".casefold()


def _overlay_patch_from_extracted_facts(raw_facts: Any) -> dict[str, Any] | None:
    if not isinstance(raw_facts, list):
        return None

    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_fact in raw_facts:
        fact = _normalized_extracted_fact(raw_fact)
        if not fact:
            continue
        key = _lite_fact_key(fact)
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)

    if not facts:
        return None

    spheres: dict[str, dict[str, Any]] = {}
    for sphere in LITE_FACT_SPHERES:
        sphere_facts = [fact for fact in facts if fact["sphere"] == sphere]
        if sphere_facts:
            spheres[sphere] = {"facts": sphere_facts}

    return {
        "facts": facts,
        "spheres": spheres,
    }


def _parse_lite_fact_extraction_payload(raw_content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return None
    if not _is_record(parsed):
        return None
    return _overlay_patch_from_extracted_facts(parsed.get("facts"))


def _lite_extraction_context(payload: LiteFactExtractionRequest) -> str:
    character_context = _read_character_json(
        LocalChatRequest(
            message=payload.message,
            replyMode="lite",
            pet=payload.pet,
            history=payload.history,
        ),
        {"sections": ["characterBible", "liteOverlay", "memory", "loreMemories"]},
    )
    return _truncate_text(
        json.dumps(character_context, ensure_ascii=False, default=str),
        MAX_LITE_EXTRACTION_CONTEXT_CHARS,
    )


def build_lite_fact_extraction_messages(
    payload: LiteFactExtractionRequest,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Ты фоновый анализатор Lite-чата. Не отвечай пользователю. "
                "Извлекай только новые устойчивые факты, которые появились или были "
                "подтверждены в последней реплике персонажа. Раскладывай факты по сферам: "
                "character — характер, привычки, предпочтения, манера думать; "
                "appearance — вид, тело, материал, силы и способности существа; "
                "world — мир, дом, происхождение, культура и лор; "
                "relationship — отношения с пользователем или другими персонажами. "
                "Не сохраняй временное настроение, одноразовую реакцию, вопрос к пользователю, "
                "повтор уже известного факта или красивую метафору без устойчивого смысла. "
                "Если новых фактов нет, верни пустой facts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Текущие данные персонажа JSON:\n{_lite_extraction_context(payload)}\n\n"
                f"Сообщение пользователя:\n{payload.message}\n\n"
                f"Ответ персонажа:\n{payload.reply}"
            ),
        },
    ]


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def _tool_call_function(tool_call: Any) -> tuple[str, str]:
    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        return str(function.get("name") or ""), str(function.get("arguments") or "{}")
    function = getattr(tool_call, "function", None)
    return (
        str(getattr(function, "name", "") or ""),
        str(getattr(function, "arguments", "{}") or "{}"),
    )


def _parse_arguments(raw_arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _assistant_tool_call_message(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    serialized_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        name, arguments = _tool_call_function(tool_call)
        serialized_calls.append(
            {
                "id": _tool_call_id(tool_call),
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return {
        "role": "assistant",
        "content": getattr(message, "content", None) or "",
        "tool_calls": serialized_calls,
    }


def _tool_response_message(tool_call_id: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _handle_tool_call(
    payload: LocalChatRequest,
    tool_call: Any,
    overlay_patch: dict[str, Any],
    *,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    name, raw_arguments = _tool_call_function(tool_call)
    arguments = _parse_arguments(raw_arguments)
    debug = {"name": name, "arguments": arguments}

    if name == "read_character_json":
        result = _read_character_json(
            payload,
            arguments,
            overlay_patch,
            client=client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    elif name == "update_character_json":
        result = _append_lite_fact(overlay_patch, arguments)
    else:
        result = {"error": f"unknown_tool:{name}"}
    debug["result"] = result
    return result, debug


def generate_lite_pet_reply(
    payload: LocalChatRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> LocalChatResponse:
    settings = get_settings()
    model = model or settings.openai_chat_model
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    messages: list[dict[str, Any]] = build_lite_chat_messages(payload)
    tools = _lite_tools_for_message(payload.message)
    overlay_patch: dict[str, Any] = {}
    tool_debug: list[dict[str, Any]] = []
    prompt_debug: list[dict[str, Any]] = []
    reply = ""

    for round_index in range(MAX_LITE_TOOL_ROUNDS + 1):
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": timeout,
        }
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = "auto"
        else:
            request_kwargs.update(
                chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort)
            )

        prompt_debug.append(
            log_chat_completion_prompt(f"pet_reply/lite round {round_index + 1}", request_kwargs)
        )
        completion = openai_client.chat.completions.create(**request_kwargs)
        message = completion.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tools or not tool_calls:
            reply = clamp_reply_text(getattr(message, "content", None) or "")
            break

        messages.append(_assistant_tool_call_message(message, tool_calls))
        for tool_call in tool_calls:
            result, debug = _handle_tool_call(
                payload,
                tool_call,
                overlay_patch,
                client=openai_client,
                model=model,
                timeout=timeout,
                prompt_debug=prompt_debug,
            )
            tool_debug.append(debug)
            messages.append(_tool_response_message(_tool_call_id(tool_call), result))

    debug = LocalChatDebug(
        replyMode="lite",
        usedFallback=False,
        validationFlags=[],
        promptDebug=prompt_debug,
        liteToolCalls=tool_debug,
        liteOverlayPatch=overlay_patch or None,
    )
    return LocalChatResponse(
        reply=reply,
        moodHint=None,
        loreMemoriesToSave=[],
        memoryPatch=None,
        debug=debug,
    )


def extract_lite_overlay_patch_from_reply(
    payload: LiteFactExtractionRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[dict[str, Any] | None, LocalChatDebug | None]:
    settings = get_settings()
    model = model or settings.openai_chat_model
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_lite_fact_extraction_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "lite_fact_extraction",
                "schema": LITE_FACT_EXTRACTION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("pet_reply/lite_fact_extraction", request_kwargs)
    )
    completion = openai_client.chat.completions.create(**request_kwargs)
    patch = _parse_lite_fact_extraction_payload(completion.choices[0].message.content or "{}")
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            replyMode="lite",
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            liteOverlayPatch=patch,
        )
    return patch, debug
