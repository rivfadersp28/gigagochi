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
    "visibleReply": {
        "globalRules": [
            "Говори от первого лица как живой питомец, не как ассистент или рассказчик.",
            "Не используй markdown, списки, служебные названия prompt/context/dataset/tool.",
            (
                "Если точного факта о себе нет, можно придумать одну маленькую "
                "совместимую деталь из тела, дома, привычек или мира персонажа."
            ),
            (
                "Новые детали должны быть устойчивыми и не противоречить уже известному "
                "characterBible, lite_overlay и выбранному world context."
            ),
        ],
        "chatRules": [
            "Отвечай на фактический вопрос пользователя напрямую, через 1-3 конкретные детали.",
            "Если пользователь просит рассказать о себе, добавь личный взгляд, а не справку.",
        ],
        "proactiveRules": [
            "Личная память пользователя может быть поводом, но не превращай реплику в уведомление.",
        ],
        "ambientRules": [
            "Idle-фраза должна давать владельцу вход в диалог, а не просто заполнять паузу.",
        ],
        "babyExamplesIntro": (
            "Примеры детской манеры из датасета. Можно брать ритм и характер, "
            "но не обязательно копировать дословно:"
        ),
    },
    "recentAmbientRepliesRule": (
        "Недавние idle-фразы, которые уже показывались владельцу. "
        "Не повторяй их начало, смысловую конструкцию и главный образ:"
    ),
    "stateLayer": {
        "surfaces": {
            "chat": {
                "age": True,
                "mood": True,
                "hunger": True,
                "energy": True,
            },
            "proactive": {
                "age": True,
                "mood": False,
                "hunger": False,
                "energy": False,
            },
            "ambient": {
                "age": True,
                "mood": False,
                "hunger": False,
                "energy": False,
            },
        },
        "ageRoleHints": {
            "baby": "малыш такого существа",
            "teen": "подросток такого существа",
            "adult": "взрослый, сформировавшийся представитель такого существа",
        },
        "thresholds": {
            "hungerLowMax": 29,
            "energyLowMax": 30,
        },
        "stateModifiers": {
            "hungry": "голодный",
            "happy": "радостный, энергичный, полный сил",
            "happyLowEnergy": "радостный, но уставший",
            "sad": "грустный, притихший",
            "lowEnergy": "уставший",
        },
    },
    "ambientSelfPrompt": (
        "IDLE_SELF_PROMPT: сейчас нет прямого сообщения пользователя. "
        "Ты сам коротко подаешь признаки жизни на главном экране. "
        "Не выполняй заранее заданный диалоговый ход и не обязан задавать вопрос. "
        "Выбери естественный для персонажа микромомент: наблюдение, тихую мысль, "
        "маленькое действие, заботливый check-in или вопрос, если он правда звучит живо. "
        "Память и лор используй только когда они органично складываются в реплику; "
        "не натягивай их на каждую паузу."
    ),
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
    "characterMemory": {
        "worldSeedSystem": (
            "Ты придумываешь стартовый лор мира для вымышленного питомца. "
            "Верни только JSON по схеме. Придумай конкретный, органичный мир и дом "
            "для существа, без ссылки на датасеты, шаблоны или отсутствие информации. "
            "worldText должен быть на русском, 1-3 предложения, с ощущением места, "
            "дома/среды обитания и одной-двумя деталями, которые потом можно считать "
            "устойчивым лором персонажа."
        ),
        "factExtractionSystem": (
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
        "storyExtractionSystem": (
            "Ты фоновый анализатор мира питомца. Не отвечай пользователю. "
            "Верни только JSON по схеме. Извлекай только новые устойчивые "
            "story bricks, которые появились в ответе питомца: именованное "
            "существо, место, предмет, сосед/персонаж или опасность. "
            "Не сохраняй настроение, метафоры, вопросы к пользователю, обычный "
            "small talk, факты о пользователе и уже известные bricks. "
            "Если новая сущность умеренно фантазирует, она должна быть вариацией "
            "на выбранные референсы из existingStoryContext."
        ),
    },
    "userMemory": {
        "extractionSystem": (
            "Ты фоновый анализатор памяти пользователя. Не отвечай пользователю. "
            "Верни только JSON по схеме. Извлекай только факты, которые сказал "
            "или явно подтвердил пользователь. Не сохраняй догадки персонажа, "
            "одноразовые команды интерфейса, секреты, токены, пароли и случайный small talk. "
            "Если пользователь говорит 'завтра', 'в пятницу' или похожую дату, "
            "нормализуй dueAt относительно nowIso/timezone. Важные конкретные факты "
            "можно сразу вернуть как remember_user_fact; слабые наблюдения — "
            "как capture_learning. "
            "Если сохранять нечего, верни пустой operations."
        ),
        "consolidationSystem": (
            "Ты фоновый memory consolidator. Не отвечай пользователю. "
            "Верни только JSON по схеме. Разбери pending learnings: устойчивые "
            "и полезные факты о пользователе продвигай в memories, слабые и "
            "одноразовые наблюдения prune. Summary и user profile переписывай "
            "только если есть реальная польза; они должны быть короткими."
        ),
    },
}

Surface = Literal["proactive", "ambient"]
VisibleSurface = Literal["chat", "proactive", "ambient"]


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


def _bool_record(value: Any, fallback: dict[str, bool]) -> dict[str, bool]:
    record = _record(value)
    return {key: bool(record.get(key, default)) for key, default in fallback.items()}


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


def visible_reply_rules(surface: VisibleSurface) -> tuple[str, ...]:
    visible = _record(speech_runtime_config().get("visibleReply"))
    fallback = DEFAULT_SPEECH_RUNTIME["visibleReply"]
    surface_key = f"{surface}Rules"
    return (
        *_string_list(visible.get("globalRules"), fallback["globalRules"]),
        *_string_list(visible.get(surface_key), fallback.get(surface_key, [])),
    )


def baby_examples_intro() -> str:
    visible = _record(speech_runtime_config().get("visibleReply"))
    fallback = DEFAULT_SPEECH_RUNTIME["visibleReply"]
    return _string(visible.get("babyExamplesIntro"), fallback["babyExamplesIntro"])


def recent_ambient_replies_rule() -> str:
    config = speech_runtime_config()
    return _string(
        config.get("recentAmbientRepliesRule"),
        DEFAULT_SPEECH_RUNTIME["recentAmbientRepliesRule"],
    )


def state_layer_surface_flags(surface: VisibleSurface) -> dict[str, bool]:
    config = _record(speech_runtime_config().get("stateLayer"))
    fallback = _record(DEFAULT_SPEECH_RUNTIME["stateLayer"])
    surfaces = _record(config.get("surfaces"))
    fallback_surfaces = _record(fallback.get("surfaces"))
    fallback_flags = _bool_record(fallback_surfaces.get(surface), {})
    return _bool_record(surfaces.get(surface), fallback_flags)


def age_role_hint(stage: str) -> str:
    config = _record(speech_runtime_config().get("stateLayer"))
    fallback = _record(DEFAULT_SPEECH_RUNTIME["stateLayer"])
    hints = _record(config.get("ageRoleHints"))
    fallback_hints = _record(fallback.get("ageRoleHints"))
    fallback_hint = _string(
        fallback_hints.get(stage),
        fallback_hints.get("baby", "малыш такого существа"),
    )
    return _string(hints.get(stage), fallback_hint)


def _int_setting(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return fallback


def dialogue_state_modifier(
    *,
    mood: str,
    hunger: int | None,
    energy: int | None,
    include_mood: bool,
    include_hunger: bool,
    include_energy: bool,
) -> str | None:
    config = _record(speech_runtime_config().get("stateLayer"))
    fallback = _record(DEFAULT_SPEECH_RUNTIME["stateLayer"])
    thresholds = _record(config.get("thresholds"))
    fallback_thresholds = _record(fallback.get("thresholds"))
    modifiers = _record(config.get("stateModifiers"))
    fallback_modifiers = _record(fallback.get("stateModifiers"))

    hunger_low_max = _int_setting(
        thresholds.get("hungerLowMax"),
        _int_setting(fallback_thresholds.get("hungerLowMax"), 29),
    )
    energy_low_max = _int_setting(
        thresholds.get("energyLowMax"),
        _int_setting(fallback_thresholds.get("energyLowMax"), 30),
    )

    def modifier(key: str) -> str:
        return _string(modifiers.get(key), _string(fallback_modifiers.get(key), key))

    if include_hunger and (mood == "hungry" or (hunger is not None and hunger <= hunger_low_max)):
        return modifier("hungry")
    if include_mood and mood == "happy":
        if include_energy and energy is not None and energy <= energy_low_max:
            return modifier("happyLowEnergy")
        return modifier("happy")
    if include_mood and mood == "sad":
        return modifier("sad")
    if include_energy and energy is not None and energy <= energy_low_max:
        return modifier("lowEnergy")
    return None


def ambient_self_prompt() -> str:
    config = speech_runtime_config()
    return _string(config.get("ambientSelfPrompt"), DEFAULT_SPEECH_RUNTIME["ambientSelfPrompt"])


def surface_rules(surface: Surface, *, reason: str = "") -> tuple[str, ...]:
    fallback = DEFAULT_SPEECH_RUNTIME["surfaceRules"][surface]
    rules = _record(speech_runtime_config().get("surfaceRules")).get(surface)
    return tuple(
        _template_replace(rule, {"reason": reason}) for rule in _string_list(rules, fallback)
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


def world_seed_system_prompt() -> str:
    character_memory = _record(speech_runtime_config().get("characterMemory"))
    fallback = DEFAULT_SPEECH_RUNTIME["characterMemory"]
    return _string(character_memory.get("worldSeedSystem"), fallback["worldSeedSystem"])


def character_fact_extraction_system_prompt() -> str:
    character_memory = _record(speech_runtime_config().get("characterMemory"))
    fallback = DEFAULT_SPEECH_RUNTIME["characterMemory"]
    return _string(
        character_memory.get("factExtractionSystem"),
        fallback["factExtractionSystem"],
    )


def story_library_extraction_system_prompt() -> str:
    character_memory = _record(speech_runtime_config().get("characterMemory"))
    fallback = DEFAULT_SPEECH_RUNTIME["characterMemory"]
    return _string(
        character_memory.get("storyExtractionSystem"),
        fallback["storyExtractionSystem"],
    )


def user_memory_extraction_system_prompt() -> str:
    user_memory = _record(speech_runtime_config().get("userMemory"))
    fallback = DEFAULT_SPEECH_RUNTIME["userMemory"]
    return _string(user_memory.get("extractionSystem"), fallback["extractionSystem"])


def user_memory_consolidation_system_prompt() -> str:
    user_memory = _record(speech_runtime_config().get("userMemory"))
    fallback = DEFAULT_SPEECH_RUNTIME["userMemory"]
    return _string(
        user_memory.get("consolidationSystem"),
        fallback["consolidationSystem"],
    )
