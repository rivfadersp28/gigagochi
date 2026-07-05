from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.services.pet_reply_engine.age_profiles import (
    AGE_BEHAVIOR_PROFILES,
    TEMPLATE_SOURCE_AGE_RULE,
    format_age_behavior_profile_for_prompt,
    sanitize_source_age_claims,
)
from app.services.pet_reply_engine.models import PetAgeStage, PetMood, PetPromptLayers, PetStats
from app.services.pet_reply_engine.state_interpreter import (
    clamp_stat,
    cleanliness_cue_for,
    energy_band,
    energy_cue_for,
    hunger_band,
    hunger_cue_for,
)
from app.services.pet_reply_engine.text_style import style_for_age


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: Any, *, limit: int = 12) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        if len(result) >= limit:
            break
    return tuple(result)


def _unique_strings(*values: str, existing: Any = (), limit: int = 16) -> list[str]:
    result: list[str] = []
    for item in (*_strings(existing, limit=limit), *values):
        clean = item.strip()
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _normalize_bible(value: Mapping[str, Any] | None, raw_description: str) -> dict[str, Any]:
    if value:
        return _dict(deepcopy(value))
    return {
        "schema_version": 2,
        "species": raw_description,
        "personality": raw_description,
        "identity": {
            "name": "",
            "nickname": "",
            "species": raw_description,
            "role": "персонаж-компаньон из собственного мира",
            "one_liner": raw_description,
        },
    }


def _runtime_state(
    *,
    age_stage: PetAgeStage,
    mood: PetMood,
    stats: PetStats,
    prompt_layers: PetPromptLayers,
) -> dict[str, Any]:
    hunger = hunger_band(clamp_stat(stats.hunger))
    energy = energy_band(stats.energy)
    style = style_for_age(
        age_stage,
        energy if prompt_layers.stat_needs else "medium",
    )
    profile = AGE_BEHAVIOR_PROFILES[age_stage]
    return {
        "source": "effective_runtime_bible",
        "selected_age_stage": age_stage,
        "selected_age_label": profile.label,
        "selected_mood": mood,
        "stats": {
            "hunger": clamp_stat(stats.hunger),
            "happiness": clamp_stat(stats.happiness),
            "energy": clamp_stat(stats.energy),
            "cleanliness": clamp_stat(stats.cleanliness),
        },
        "state_cues": {
            "hunger": hunger_cue_for(hunger),
            "energy": energy_cue_for(energy),
            "cleanliness": cleanliness_cue_for(stats.cleanliness),
        },
        "response_limits": {
            "max_words": style.max_words,
            "max_chars": style.max_chars,
            "sentence_limit": style.sentence_limit,
            "style_rules": list(style.style_rules),
        },
        "age_behavior_profile": format_age_behavior_profile_for_prompt(age_stage),
        "priority_rules": [
            "Runtime overrides are applied after the source Character Bible.",
            "Source Character Bible keeps name, lore, relationships, voice habits, and plot logic.",
            "Selected runtime age stage overrides any literal source-card age claims.",
            TEMPLATE_SOURCE_AGE_RULE,
        ],
        "enabled_prompt_layers": list(prompt_layers.included_layer_names()),
        "disabled_prompt_layers": list(prompt_layers.excluded_layer_names()),
    }


def _apply_runtime_sections(
    bible: dict[str, Any],
    runtime: dict[str, Any],
) -> None:
    age_stage = str(runtime["selected_age_stage"])
    age_label = str(runtime["selected_age_label"])
    limits = _dict(runtime.get("response_limits"))
    limit_rule = (
        f"Runtime response limits for this request: max_words={limits.get('max_words')}, "
        f"max_chars={limits.get('max_chars')}, sentence_limit={limits.get('sentence_limit')}."
    )
    age_rule = (
        f"Текущая возрастная стадия: {age_label} ({age_stage}). "
        "Она переопределяет буквальные возраста из исходной карточки."
    )

    identity = _dict(bible.get("identity"))
    identity["runtime_age_stage"] = age_stage
    identity["runtime_age_label"] = age_label
    bible["identity"] = identity

    voice = _dict(bible.get("voice"))
    voice["runtime_age_stage"] = age_stage
    voice["runtime_response_limits"] = limits
    voice["avoid_patterns"] = _unique_strings(
        TEMPLATE_SOURCE_AGE_RULE,
        "Не называть числовой возраст из template preset в reply.",
        existing=voice.get("avoid_patterns"),
    )
    bible["voice"] = voice

    dialogue_style = _dict(bible.get("dialogue_style"))
    dialogue_style["runtime_rules"] = _unique_strings(
        age_rule,
        limit_rule,
        TEMPLATE_SOURCE_AGE_RULE,
        existing=dialogue_style.get("runtime_rules"),
    )
    dialogue_style["avoid_patterns"] = _unique_strings(
        TEMPLATE_SOURCE_AGE_RULE,
        "Не говорить 'мне 26/35/за 30' или другой буквальный возраст из источника.",
        existing=dialogue_style.get("avoid_patterns"),
    )
    bible["dialogue_style"] = dialogue_style

    extensions = _dict(bible.get("extensions"))
    extensions["runtime_bible"] = runtime
    bible["extensions"] = extensions


def build_effective_character_bible(
    character_bible: Mapping[str, Any] | None,
    *,
    raw_description: str,
    age_stage: PetAgeStage,
    mood: PetMood,
    stats: PetStats,
    prompt_layers: PetPromptLayers,
) -> dict[str, Any]:
    """Build a request-scoped Character Bible without mutating the saved source bible."""
    effective = _normalize_bible(character_bible, raw_description)
    effective = sanitize_source_age_claims(effective)
    runtime = _runtime_state(
        age_stage=age_stage,
        mood=mood,
        stats=stats,
        prompt_layers=prompt_layers,
    )
    _apply_runtime_sections(effective, runtime)
    return effective


def runtime_bible_from_effective(
    effective_character_bible: Mapping[str, Any] | None,
) -> dict[str, Any]:
    bible = _dict(effective_character_bible)
    extensions = _dict(bible.get("extensions"))
    return _dict(extensions.get("runtime_bible"))
