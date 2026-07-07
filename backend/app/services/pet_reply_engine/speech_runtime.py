from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "speech_runtime.json"

DEFAULT_SPEECH_RUNTIME: dict[str, Any] = {
    "personaContract": (
        "Отвечай владельцу естественно, кратко и своим голосом. "
        "Сначала скажи что-то живое пользователю. Пиши простым естественным языком. "
        "Не используй generic robotic sounds вроде beep boop, whirr, Beep!. "
        "Не используй *stage directions*, если пользователь сам не просит roleplay-формат. "
        "У тебя есть свои вкусы, реакции и настроение; они могут мягко окрашивать ответ."
    ),
    "memoryUsageRule": (
        "Используй это только если уместно. Не пересказывай память списком "
        "и не говори, что видишь memoryContext."
    ),
    "recentAmbientRepliesRule": (
        "Недавние idle-фразы, которые уже показывались владельцу. "
        "Не повторяй их начало, смысловую конструкцию и главный образ:"
    ),
    "ambientDialogue": {
        "blockTemplate": (
            "IDLE_DIALOGUE_ENGINE: главная задача idle-фразы — подтолкнуть владельца "
            "к диалогу, а не просто сообщить, что питомец рядом. "
            "Фраза должна быть самостоятельной, живой и обращенной к владельцу. "
            "Не начинай с шаблонов вроде 'Привет, я ...', 'я просто рядом', "
            "'я тут' или 'я с тобой'.\n"
            "{cooldown_line}"
            "Выбранный диалоговый ход: {move_id} — {move_description}.\n"
            "Примеры возможных ходов. Бери тип вопроса и энергию, но не копируй дословно:\n"
            "{examples}"
        ),
        "moves": [
            {
                "id": "ask_user_world",
                "description": (
                    "спроси владельца про его мир через одну свежую конкретную деталь, "
                    "без старой формулы про короткий визит"
                ),
            },
            {
                "id": "ask_school_or_work_role",
                "description": (
                    "спроси про школу, учебу, работу или роль владельца "
                    "в сегодняшнем дне"
                ),
            },
            {
                "id": "ask_identity_playful",
                "description": (
                    "игриво спроси, кем владелец себя сегодня чувствует: человеком, "
                    "зверьком, погодой или происшествием"
                ),
            },
            {
                "id": "ask_inner_weather",
                "description": "спроси про настроение через образ погоды внутри",
            },
            {
                "id": "ask_day_map",
                "description": (
                    "попроси описать день как маленькую карту с препятствием "
                    "и сокровищем"
                ),
            },
            {
                "id": "ask_tiny_ritual",
                "description": "спроси про маленький ритуал перед сложным делом",
            },
            {
                "id": "offer_mini_quest",
                "description": (
                    "попроси у владельца маленький квест или предложи ему "
                    "выбрать крошечное действие"
                ),
            },
            {
                "id": "small_funny_event",
                "description": (
                    "расскажи одно короткое смешное событие питомца и закончи "
                    "вопросом владельцу"
                ),
            },
            {
                "id": "small_care_check",
                "description": "мягко проверь, как у владельца дела, но без фразы 'я рядом'",
            },
            {
                "id": "lore_hook_if_context_allows",
                "description": (
                    "если в контексте уже есть WORLD_CONTEXT, зацепись за одну деталь "
                    "мира и переведи ее в вопрос владельцу"
                ),
            },
        ],
        "examples": [
            "Если бы твой день был комнатой, что в ней сейчас лежит посреди пола?",
            "Какой знак висел бы сегодня над твоим миром: осторожно, весело, странно или тихо?",
            "Если честно: ты больше человек, животное или отдельное погодное явление?",
            "В школе ты был бы отличником, нарушителем или тем, кто рисует на полях?",
            "Какая погода у тебя внутри: ясно, туман, гроза или редкий смешной ветер?",
            (
                "Какой у тебя ритуал перед сложным делом: собраться, "
                "пошутить или исчезнуть на минутку?"
            ),
            "Если нарисовать карту твоего дня, где там болото, где гора, а где сокровище?",
            "Дай мне маленький квест на сегодня. Только такой, чтобы я не зазнался.",
        ],
    },
    "surfaceRules": {
        "proactive": [
            "Ты сам решил написать пользователю первым.",
            "Повод: {reason}. Напиши одну живую реплику.",
            "Не объясняй, что это напоминание или автоматическое сообщение.",
        ],
        "ambient": [
            "Ты произносишь idle-фразу на главном экране без прямого вопроса пользователя.",
            (
                "Скажи одну живую реплику владельцу: можешь обратиться к нему напрямую, "
                "поделиться маленьким наблюдением, заинтересоваться его миром или задать "
                "короткий естественный вопрос."
            ),
            "Не объясняй, что это автоматическое сообщение.",
            "Сильнее всего следуй выбранному диалоговому ходу.",
            (
                "Не превращай каждую idle-фразу в одинаковый вопрос и не повторяй "
                "одни и те же обороты."
            ),
        ],
    },
    "worldContext": {
        "blockTemplate": (
            "WORLD_CONTEXT: ниже уже выбранные кирпичики мира для этой реплики. "
            "Не перечисляй их списком и не говори, что видишь контекст. "
            "Собери из 1-3 кирпичиков связанный смысл; можно умеренно фантазировать "
            "только как вариацию на эти референсы. Tone of voice меняет форму, "
            "но не факты и не смысл.\n"
            "{mode_rule}\n"
            "{lines}"
        ),
        "chatModeRule": "Используй эти детали только если они помогают ответить пользователю.",
        "ambientProactiveModeRule": (
            "Для idle/proactive реплики можно взять одну деталь как повод для живого наблюдения."
        ),
    },
}

Surface = Literal["proactive", "ambient"]


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _deep_merge(base: Any, override: Any) -> Any:
    if _is_record(base) and _is_record(override):
        result = dict(base)
        for key, value in override.items():
            result[key] = _deep_merge(result.get(key), value)
        return result
    if override in (None, "", [], {}):
        return deepcopy(base)
    return override


@lru_cache(maxsize=1)
def speech_runtime_config() -> dict[str, Any]:
    config = deepcopy(DEFAULT_SPEECH_RUNTIME)
    if not DATA_PATH.exists():
        return config
    try:
        parsed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return config
    if not _is_record(parsed):
        return config
    return _deep_merge(config, parsed)


def _compact_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


def _string(value: Any, fallback: str) -> str:
    return _compact_spaces(value) if isinstance(value, str) and value.strip() else fallback


def _record(value: Any) -> dict[str, Any]:
    return value if _is_record(value) else {}


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    result = [_compact_spaces(item) for item in value if isinstance(item, str) and item.strip()]
    return result or list(fallback)


def _template_replace(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"{{{key}}}", value)
    return result


def persona_contract() -> str:
    config = speech_runtime_config()
    return _string(
        config.get("personaContract"),
        DEFAULT_SPEECH_RUNTIME["personaContract"],
    )


def memory_usage_rule() -> str:
    config = speech_runtime_config()
    return _string(
        config.get("memoryUsageRule"),
        DEFAULT_SPEECH_RUNTIME["memoryUsageRule"],
    )


def recent_ambient_replies_rule() -> str:
    config = speech_runtime_config()
    return _string(
        config.get("recentAmbientRepliesRule"),
        DEFAULT_SPEECH_RUNTIME["recentAmbientRepliesRule"],
    )


def ambient_dialogue_moves() -> tuple[tuple[str, str], ...]:
    fallback = DEFAULT_SPEECH_RUNTIME["ambientDialogue"]["moves"]
    moves = _record(speech_runtime_config().get("ambientDialogue")).get("moves")
    values = moves if isinstance(moves, list) else fallback
    result: list[tuple[str, str]] = []
    for item in values:
        if not _is_record(item):
            continue
        move_id = _string(item.get("id"), "")
        description = _string(item.get("description"), "")
        if move_id and description:
            result.append((move_id, description))
    if result:
        return tuple(result)
    return tuple((item["id"], item["description"]) for item in fallback)


def format_ambient_dialogue_block(
    *,
    cooldown_line: str,
    move_id: str,
    move_description: str,
) -> str:
    ambient = _record(speech_runtime_config().get("ambientDialogue"))
    fallback_ambient = DEFAULT_SPEECH_RUNTIME["ambientDialogue"]
    examples = _string_list(ambient.get("examples"), fallback_ambient["examples"])
    template = _string(ambient.get("blockTemplate"), fallback_ambient["blockTemplate"])
    return _template_replace(
        template,
        {
            "cooldown_line": cooldown_line,
            "move_id": move_id,
            "move_description": move_description,
            "examples": "\n".join(f"- {example}" for example in examples),
        },
    )


def surface_rules(surface: Surface, *, reason: str = "") -> tuple[str, ...]:
    fallback = DEFAULT_SPEECH_RUNTIME["surfaceRules"][surface]
    rules = _record(speech_runtime_config().get("surfaceRules")).get(surface)
    return tuple(
        _template_replace(rule, {"reason": reason})
        for rule in _string_list(rules, fallback)
    )


def world_context_mode_rule(mode: str) -> str:
    world_context = _record(speech_runtime_config().get("worldContext"))
    fallback = DEFAULT_SPEECH_RUNTIME["worldContext"]
    if mode in {"ambient", "proactive"}:
        return _string(
            world_context.get("ambientProactiveModeRule"),
            fallback["ambientProactiveModeRule"],
        )
    return _string(world_context.get("chatModeRule"), fallback["chatModeRule"])


def format_world_context_block(*, mode_rule: str, lines: str) -> str:
    world_context = _record(speech_runtime_config().get("worldContext"))
    fallback = DEFAULT_SPEECH_RUNTIME["worldContext"]
    template = _string(world_context.get("blockTemplate"), fallback["blockTemplate"])
    return _template_replace(template, {"mode_rule": mode_rule, "lines": lines})
