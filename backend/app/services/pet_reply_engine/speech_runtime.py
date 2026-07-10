from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.services.tone_runtime import tone_prompt_block

DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "speech_runtime.json"

VisibleSurface = Literal["chat", "proactive", "ambient", "push"]
ContextSurface = Literal["chat", "proactive", "ambient", "push", "backgroundStory"]
ContextSourceMode = Literal["disabled", "auto", "always"]

SURFACES: tuple[VisibleSurface, ...] = ("chat", "proactive", "ambient", "push")
CONTEXT_SURFACES: tuple[ContextSurface, ...] = (
    "chat",
    "proactive",
    "ambient",
    "push",
    "backgroundStory",
)
SURFACE_PROMPT_KEYS: dict[VisibleSurface, str] = {
    "chat": "chat",
    "proactive": "proactive",
    "ambient": "idle",
    "push": "push",
}
STATE_FLAGS = ("age",)
AGE_STAGES = ("baby", "teen", "adult")
STATE_MODIFIER_KEYS = ("hungry", "happy", "happyLowEnergy", "sad", "lowEnergy")
STATE_PARAM_KEYS = ("hunger", "happiness", "energy")
STATE_PARAM_BANDS = ("low", "normal", "high")
CONTEXT_ROUTING_SOURCE_KEYS = (
    "worldContext",
    "characterProfile",
    "userMemory",
    "chatHistory",
    "recentReplies",
)
CONTEXT_SOURCE_KEYS = (
    "characterProfile",
    "stateParams",
    "liteOverlay",
    "storyLibrary",
    "storyOverlay",
    "recentEvents",
    "userMemory",
    "chatHistory",
    "recentReplies",
)
CONTEXT_SOURCE_MODES: tuple[ContextSourceMode, ...] = ("disabled", "auto", "always")
STATE_PARAM_CONTEXT_SOURCE_MODES: tuple[ContextSourceMode, ...] = ("disabled", "always")

REQUIRED_STRING_PATHS: tuple[tuple[str, ...], ...] = (
    ("surfacePrompts", "chat"),
    ("surfacePrompts", "idle"),
    ("surfacePrompts", "proactive"),
    ("surfacePrompts", "push"),
    ("contextRouting", "systemPrompt"),
    ("identityTemplate",),
    ("memoryUsageRule",),
    ("visibleReply", "transientContextRule"),
    ("stateLayer", "stateParamUsageRule"),
    ("worldContext", "template"),
    ("characterMemory", "worldSeedSystem"),
    ("characterMemory", "factExtractionSystem"),
    ("userMemory", "extractionSystem"),
    ("userMemory", "consolidationSystem"),
    ("phraseTemplates", "memoryProfileLine"),
    ("phraseTemplates", "memorySummaryLine"),
    ("phraseTemplates", "memoryItemsHeader"),
    ("phraseTemplates", "unnamedPet"),
    ("phraseTemplates", "emptyValue"),
    ("phraseTemplates", "worldSeedUserMessage"),
    ("phraseTemplates", "liteFactExtractionUserMessage"),
    ("phraseTemplates", "userMemoryExtractionUserMessage"),
    ("phraseTemplates", "userMemoryConsolidationUserMessage"),
    ("storyContext", "defaultQuery"),
    ("backgroundStory", "systemPrompt"),
    ("backgroundStory", "userTemplate"),
    ("backgroundStory", "aftermathExtractionSystem"),
    ("backgroundStory", "aftermathExtractionUserTemplate"),
    ("backgroundStory", "defaultEventType"),
    ("ageExamplePlaceholders", "petName"),
    ("ageExamplePlaceholders", "food"),
    ("ageExamplePlaceholders", "fear"),
    ("ageExamplePlaceholders", "secondPerson"),
    ("ageExamplePlaceholders", "ability"),
)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _path_label(path: tuple[str, ...] | list[str]) -> str:
    return ".".join(path)


def _read_path(config: dict[str, Any], path: tuple[str, ...] | list[str]) -> Any:
    current: Any = config
    for key in path:
        if not _is_record(current) or key not in current:
            raise ValueError(f"missing required key: {_path_label(path)}")
        current = current[key]
    return current


def _required_string(
    config: dict[str, Any],
    path: tuple[str, ...] | list[str],
    *,
    allow_empty: bool = False,
) -> str:
    value = _read_path(config, path)
    if not isinstance(value, str):
        raise ValueError(f"{_path_label(path)} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise ValueError(f"{_path_label(path)} must not be empty")
    return result


def _required_bool(config: dict[str, Any], path: tuple[str, ...] | list[str]) -> bool:
    value = _read_path(config, path)
    if not isinstance(value, bool):
        raise ValueError(f"{_path_label(path)} must be a boolean")
    return value


def _required_int(config: dict[str, Any], path: tuple[str, ...] | list[str]) -> int:
    value = _read_path(config, path)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"{_path_label(path)} must be an integer")


def _required_string_list(
    config: dict[str, Any],
    path: tuple[str, ...] | list[str],
) -> list[str]:
    value = _read_path(config, path)
    if not isinstance(value, list):
        raise ValueError(f"{_path_label(path)} must be a list")
    result = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if not result or len(result) != len(value):
        raise ValueError(f"{_path_label(path)} must contain non-empty strings")
    return result


def validate_speech_runtime_config(config: Any) -> None:
    if not _is_record(config):
        raise ValueError("speech_runtime must be a JSON object")
    for path in REQUIRED_STRING_PATHS:
        _required_string(config, path)
    _required_string_list(config, ("ambientDialogueImpulses",))
    for surface in SURFACES:
        for flag in STATE_FLAGS:
            _required_bool(config, ("stateLayer", "surfaces", surface, flag))
    for stage in AGE_STAGES:
        _required_string(config, ("stateLayer", "ageRoleHints", stage))
    _required_int(config, ("stateLayer", "thresholds", "hungerLowMax"))
    _required_int(config, ("stateLayer", "thresholds", "hungerHighMin"))
    _required_int(config, ("stateLayer", "thresholds", "happinessLowMax"))
    _required_int(config, ("stateLayer", "thresholds", "happinessHighMin"))
    _required_int(config, ("stateLayer", "thresholds", "energyLowMax"))
    _required_int(config, ("stateLayer", "thresholds", "energyHighMin"))
    _required_int(config, ("backgroundStory", "maxStoryChars"))
    _required_int(config, ("backgroundStory", "maxRagChars"))
    visible_reply_max_chars = _required_int(config, ("visibleReply", "maxChars"))
    if not 1 <= visible_reply_max_chars <= 300:
        raise ValueError("visibleReply.maxChars must be between 1 and 300")
    _required_string(config, ("visibleReply", "model"))
    visible_reply_reasoning_effort = _required_string(
        config,
        ("visibleReply", "reasoningEffort"),
    )
    if visible_reply_reasoning_effort not in {"none", "low", "medium", "high", "xhigh"}:
        raise ValueError(
            "visibleReply.reasoningEffort must be one of none, low, medium, high, xhigh"
        )
    background_reasoning_effort = _required_string(
        config,
        ("backgroundStory", "reasoningEffort"),
    )
    if background_reasoning_effort not in {"none", "low", "medium", "high"}:
        raise ValueError(
            "backgroundStory.reasoningEffort must be one of none, low, medium, high"
        )
    background_template = _required_string(config, ("backgroundStory", "userTemplate"))
    if "{character}" not in background_template:
        raise ValueError("backgroundStory.userTemplate must include {character}")
    aftermath_template = _required_string(
        config,
        ("backgroundStory", "aftermathExtractionUserTemplate"),
    )
    for placeholder in ("{character_context}", "{story_payload}"):
        if placeholder not in aftermath_template:
            raise ValueError(
                f"backgroundStory.aftermathExtractionUserTemplate must include {placeholder}"
            )
    for surface in CONTEXT_SURFACES:
        for source in CONTEXT_SOURCE_KEYS:
            mode = _required_string(config, ("contextSources", "surfaces", surface, source))
            allowed_modes = (
                STATE_PARAM_CONTEXT_SOURCE_MODES
                if source == "stateParams"
                else CONTEXT_SOURCE_MODES
            )
            if mode not in allowed_modes:
                raise ValueError(
                    f"contextSources.surfaces.{surface}.{source} must be one of "
                    f"{', '.join(allowed_modes)}"
                )
    for key in STATE_MODIFIER_KEYS:
        _required_string(config, ("stateLayer", "stateModifiers", key))
    for key in STATE_PARAM_KEYS:
        for band in STATE_PARAM_BANDS:
            _required_string(config, ("stateLayer", "stateParamLabels", key, band))
    for source in CONTEXT_ROUTING_SOURCE_KEYS:
        _required_string(config, ("contextRouting", "sources", source, "description"))
        _required_string(config, ("contextRouting", "sources", source, "criteria"))


@lru_cache(maxsize=1)
def speech_runtime_config() -> dict[str, Any]:
    if not DATA_PATH.exists():
        raise RuntimeError(f"{DATA_PATH} is missing")
    try:
        parsed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid speech_runtime.json: {exc.msg}") from exc
    try:
        validate_speech_runtime_config(parsed)
    except ValueError as exc:
        raise RuntimeError(f"Invalid speech_runtime.json: {exc}") from exc
    return parsed


def _template_replace(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"{{{key}}}", value)
    return result


def speech_template(
    name: str,
    values: dict[str, str] | None = None,
    *,
    allow_empty: bool = False,
) -> str:
    template = _required_string(
        speech_runtime_config(),
        ("phraseTemplates", name),
        allow_empty=allow_empty,
    )
    return _template_replace(template, values or {})


def surface_prompt(surface: VisibleSurface, values: dict[str, str] | None = None) -> str:
    key = SURFACE_PROMPT_KEYS[surface]
    template = _required_string(speech_runtime_config(), ("surfacePrompts", key))
    return _template_replace(template, values or {})


def ambient_dialogue_impulse() -> str:
    impulses = _required_string_list(speech_runtime_config(), ("ambientDialogueImpulses",))
    return random.choice(impulses)


def context_routing_system_prompt() -> str:
    return _required_string(speech_runtime_config(), ("contextRouting", "systemPrompt"))


def context_routing_sources() -> dict[str, dict[str, str]]:
    config = speech_runtime_config()
    return {
        source: {
            "description": _required_string(
                config,
                ("contextRouting", "sources", source, "description"),
            ),
            "criteria": _required_string(
                config,
                ("contextRouting", "sources", source, "criteria"),
            ),
        }
        for source in CONTEXT_ROUTING_SOURCE_KEYS
    }


def context_source_mode(surface: ContextSurface, source: str) -> ContextSourceMode:
    if surface not in CONTEXT_SURFACES:
        raise RuntimeError(f"Unknown context surface: {surface}")
    if source not in CONTEXT_SOURCE_KEYS:
        raise RuntimeError(f"Unknown context source: {source}")
    mode = _required_string(
        speech_runtime_config(),
        ("contextSources", "surfaces", surface, source),
    )
    if mode not in CONTEXT_SOURCE_MODES:
        raise RuntimeError(f"Unknown context source mode: {surface}.{source}={mode}")
    return mode  # type: ignore[return-value]


def context_source_modes(surface: ContextSurface) -> dict[str, ContextSourceMode]:
    return {source: context_source_mode(surface, source) for source in CONTEXT_SOURCE_KEYS}


def context_source_enabled(
    surface: ContextSurface,
    source: str,
    *,
    router_enabled: bool | None = None,
    auto_default: bool = False,
) -> bool:
    mode = context_source_mode(surface, source)
    if mode == "disabled":
        return False
    if mode == "always":
        return True
    return router_enabled if router_enabled is not None else auto_default


def identity_prompt(values: dict[str, str]) -> str:
    template = _required_string(speech_runtime_config(), ("identityTemplate",))
    return _template_replace(template, values)


def memory_usage_rule() -> str:
    return _required_string(speech_runtime_config(), ("memoryUsageRule",))


def transient_context_rule() -> str:
    return _required_string(speech_runtime_config(), ("visibleReply", "transientContextRule"))


def visible_reply_limit(requested: int | None = None) -> int:
    configured = _required_int(speech_runtime_config(), ("visibleReply", "maxChars"))
    if requested is None:
        return configured
    return max(1, min(requested, configured))


def visible_reply_model() -> str:
    return _required_string(speech_runtime_config(), ("visibleReply", "model"))


def visible_reply_reasoning_effort() -> str:
    return _required_string(speech_runtime_config(), ("visibleReply", "reasoningEffort"))


def state_layer_surface_flags(surface: VisibleSurface) -> dict[str, bool]:
    config = speech_runtime_config()
    state_params_enabled = context_source_enabled(surface, "stateParams", auto_default=True)
    return {
        "age": _required_bool(config, ("stateLayer", "surfaces", surface, "age")),
        "mood": state_params_enabled,
        "hunger": state_params_enabled,
        "energy": state_params_enabled,
    }


def age_role_hint(stage: str) -> str:
    if stage not in AGE_STAGES:
        raise RuntimeError(f"Unknown age stage for speech runtime: {stage}")
    return _required_string(speech_runtime_config(), ("stateLayer", "ageRoleHints", stage))


def dialogue_state_modifier(
    *,
    mood: str,
    hunger: int | None,
    energy: int | None,
    include_mood: bool,
    include_hunger: bool,
    include_energy: bool,
) -> str | None:
    config = speech_runtime_config()
    hunger_low_max = _required_int(config, ("stateLayer", "thresholds", "hungerLowMax"))
    energy_low_max = _required_int(config, ("stateLayer", "thresholds", "energyLowMax"))

    def modifier(key: str) -> str:
        return _required_string(config, ("stateLayer", "stateModifiers", key))

    if include_hunger and (mood == "hungry" or (hunger is not None and hunger <= hunger_low_max)):
        return modifier("hungry")
    if (
        include_mood
        and mood == "happy"
        and include_energy
        and energy is not None
        and energy <= energy_low_max
    ):
        return modifier("happyLowEnergy")
    if include_mood and mood == "sad":
        return modifier("sad")
    if include_mood and mood == "happy":
        return modifier("happy")
    if include_energy and energy is not None and energy <= energy_low_max:
        return modifier("lowEnergy")
    return None


def state_param_usage_rule() -> str:
    return _required_string(speech_runtime_config(), ("stateLayer", "stateParamUsageRule"))


def _state_param_band(value: int | None, *, low_max: int, high_min: int) -> str:
    if value is None:
        return "normal"
    if value <= low_max:
        return "low"
    if value >= high_min:
        return "high"
    return "normal"


def state_param_labels(
    *,
    hunger: int | None,
    happiness: int | None,
    energy: int | None,
) -> dict[str, str]:
    config = speech_runtime_config()
    bands = {
        "hunger": _state_param_band(
            hunger,
            low_max=_required_int(config, ("stateLayer", "thresholds", "hungerLowMax")),
            high_min=_required_int(config, ("stateLayer", "thresholds", "hungerHighMin")),
        ),
        "happiness": _state_param_band(
            happiness,
            low_max=_required_int(config, ("stateLayer", "thresholds", "happinessLowMax")),
            high_min=_required_int(config, ("stateLayer", "thresholds", "happinessHighMin")),
        ),
        "energy": _state_param_band(
            energy,
            low_max=_required_int(config, ("stateLayer", "thresholds", "energyLowMax")),
            high_min=_required_int(config, ("stateLayer", "thresholds", "energyHighMin")),
        ),
    }
    return {
        key: _required_string(config, ("stateLayer", "stateParamLabels", key, band))
        for key, band in bands.items()
    }


def format_world_context_block(*, lines: str) -> str:
    template = _required_string(speech_runtime_config(), ("worldContext", "template"))
    return f"{tone_prompt_block('worldContext')}\n\n{_template_replace(template, {'lines': lines})}"


def world_seed_system_prompt() -> str:
    return _required_string(speech_runtime_config(), ("characterMemory", "worldSeedSystem"))


def character_fact_extraction_system_prompt() -> str:
    return _required_string(
        speech_runtime_config(),
        ("characterMemory", "factExtractionSystem"),
    )


def user_memory_extraction_system_prompt() -> str:
    return _required_string(speech_runtime_config(), ("userMemory", "extractionSystem"))


def user_memory_consolidation_system_prompt() -> str:
    return _required_string(speech_runtime_config(), ("userMemory", "consolidationSystem"))


def story_context_default_query(_mode: str) -> str:
    return _required_string(speech_runtime_config(), ("storyContext", "defaultQuery"))


def background_story_system_prompt() -> str:
    return _required_string(speech_runtime_config(), ("backgroundStory", "systemPrompt"))


def background_story_user_prompt(values: dict[str, str]) -> str:
    template = _required_string(speech_runtime_config(), ("backgroundStory", "userTemplate"))
    return _template_replace(template, values)


def background_story_aftermath_extraction_system_prompt() -> str:
    return _required_string(
        speech_runtime_config(),
        ("backgroundStory", "aftermathExtractionSystem"),
    )


def background_story_aftermath_extraction_user_prompt(values: dict[str, str]) -> str:
    template = _required_string(
        speech_runtime_config(),
        ("backgroundStory", "aftermathExtractionUserTemplate"),
    )
    return _template_replace(template, values)


def background_story_default_event_type() -> str:
    return _required_string(speech_runtime_config(), ("backgroundStory", "defaultEventType"))


def background_story_max_story_chars() -> int:
    return _required_int(speech_runtime_config(), ("backgroundStory", "maxStoryChars"))


def background_story_max_rag_chars() -> int:
    return _required_int(speech_runtime_config(), ("backgroundStory", "maxRagChars"))


def background_story_reasoning_effort() -> str:
    return _required_string(speech_runtime_config(), ("backgroundStory", "reasoningEffort"))


def background_story_source_flags() -> dict[str, bool]:
    return {
        "characterProfile": context_source_enabled(
            "backgroundStory",
            "characterProfile",
            auto_default=True,
        ),
        "stateParams": context_source_enabled(
            "backgroundStory",
            "stateParams",
            auto_default=True,
        ),
        "liteOverlay": context_source_enabled(
            "backgroundStory",
            "liteOverlay",
            auto_default=True,
        ),
        "storyOverlay": False,
        "userMemory": context_source_enabled(
            "backgroundStory",
            "userMemory",
            auto_default=True,
        ),
    }


def age_example_placeholder_defaults() -> dict[str, Any]:
    config = speech_runtime_config()
    return {
        "petName": _required_string(config, ("ageExamplePlaceholders", "petName")),
        "food": _required_string(config, ("ageExamplePlaceholders", "food")),
        "fear": _required_string(config, ("ageExamplePlaceholders", "fear")),
        "secondPerson": _required_string(config, ("ageExamplePlaceholders", "secondPerson")),
        "ability": _required_string(config, ("ageExamplePlaceholders", "ability")),
    }
