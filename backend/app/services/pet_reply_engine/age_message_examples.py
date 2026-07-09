from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from app.services.pet_reply_engine.models import PetAgeStage, PetReplyInput
from app.services.pet_reply_engine.speech_runtime import age_example_placeholder_defaults

DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "age_speech_examples"
    / "creature_phrases_dataset.json"
)

MOOD_CATEGORIES = {
    "idle": ("greeting", "curious"),
    "happy": ("happy", "playful", "loving"),
    "sad": ("sad", "loving", "tired"),
    "hungry": ("hungry", "angry", "tired"),
}
ACTION_CATEGORIES = {
    "feed": ("hungry", "happy", "loving"),
    "play": ("playful", "happy"),
    "clean": ("loving", "tired"),
    "pet": ("loving", "happy"),
    "idle_return": ("greeting", "loving"),
    "creation_intro": ("greeting", "curious"),
    "system_nudge": ("greeting", "loving"),
}
INTENT_CATEGORIES = {
    "answer_lore": ("curious", "loving"),
    "answer_preference": ("curious", "happy"),
    "why": ("curious",),
    "care": ("loving",),
    "playful_offer": ("playful", "happy"),
    "boundary": ("angry", "sad"),
    "appearance": ("curious",),
    "status": (),
    "smalltalk": (),
    "continue_thread": ("curious",),
}
FALLBACK_CATEGORIES = ("curious", "loving", "greeting")
WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9-]+")


@lru_cache(maxsize=1)
def _dataset() -> dict[str, Any]:
    with DATA_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Age speech examples dataset must be a JSON object")
    return data


def _stage_data(age_stage: PetAgeStage) -> dict[str, Any]:
    stages = _dataset().get("stages")
    if not isinstance(stages, dict):
        return {}
    value = stages.get(age_stage)
    return value if isinstance(value, dict) else {}


def stage_label(age_stage: PetAgeStage) -> str:
    value = _stage_data(age_stage).get("label")
    return value.strip() if isinstance(value, str) and value.strip() else age_stage


def stage_speech_rules(age_stage: PetAgeStage, *, limit: int = 12) -> tuple[str, ...]:
    value = _stage_data(age_stage).get("speech_rules")
    if not isinstance(value, list):
        return ()
    rules = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return rules[:limit]


def _phrases_by_category(age_stage: PetAgeStage) -> dict[str, tuple[str, ...]]:
    value = _stage_data(age_stage).get("phrases")
    if not isinstance(value, dict):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for category, items in value.items():
        if not isinstance(category, str) or not isinstance(items, list):
            continue
        phrases = tuple(item.strip() for item in items if isinstance(item, str) and item.strip())
        if phrases:
            result[category] = phrases
    return result


def all_stage_phrases(age_stage: PetAgeStage) -> tuple[str, ...]:
    phrases: list[str] = []
    for items in _phrases_by_category(age_stage).values():
        phrases.extend(items)
    return tuple(phrases)


def _first_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            found = _first_string(item)
            if found:
                return found
    if isinstance(value, dict):
        for item in value.values():
            found = _first_string(item)
            if found:
                return found
    return None


def _lore_inner_life_value(reply_input: PetReplyInput, key: str) -> str | None:
    lore = reply_input.pet.lore
    if not isinstance(lore, dict):
        return None
    inner_life = lore.get("inner_life")
    if not isinstance(inner_life, dict):
        return None
    return _first_string(inner_life.get(key))


def _short_phrase(value: str | None, *, max_words: int = 3) -> str | None:
    words = WORD_PATTERN.findall(value or "")
    if not words:
        return None
    return " ".join(words[:max_words])


def placeholder_values(reply_input: PetReplyInput) -> dict[str, str]:
    pet = reply_input.pet
    visual = pet.visual_identity
    personality = pet.personality
    defaults = age_example_placeholder_defaults()

    sound = _short_phrase(_first_string(visual.chat_cues.sound_words), max_words=1)
    if not sound:
        sound = _short_phrase(_first_string(personality.favorite_words), max_words=1)

    body_part = _short_phrase(_first_string(visual.chat_cues.body_words), max_words=2)
    if not body_part:
        body_part = _short_phrase(_first_string(visual.signature_features), max_words=2)

    food = _short_phrase(_lore_inner_life_value(reply_input, "likes"), max_words=3)
    fear = _short_phrase(_lore_inner_life_value(reply_input, "fears"), max_words=3)
    ability = (
        _short_phrase(_first_string(visual.chat_cues.metaphor_words), max_words=2)
        or _short_phrase(_first_string(visual.signature_features), max_words=3)
        or _short_phrase(visual.species, max_words=3)
    )

    return {
        "[звук]": sound or "",
        "[имя]": pet.name or defaults["petName"],
        "[часть тела]": body_part or "",
        "[часть_тела]": body_part or "",
        "[еда]": food or defaults["food"],
        "[страх]": fear or defaults["fear"],
        "[Болшой]": defaults["secondPerson"],
        "[способность]": ability or defaults["ability"],
    }


def adapt_template(text: str, reply_input: PetReplyInput) -> str:
    values = placeholder_values(reply_input)
    result = text
    for token, replacement in values.items():
        result = result.replace(token, replacement)
    return re.sub(r"[ \t]{2,}", " ", result).strip()


def categories_for_reply(
    reply_input: PetReplyInput,
    detected_intent: str | None = None,
) -> tuple[str, ...]:
    selected: list[str] = []
    action_categories = ACTION_CATEGORIES.get(reply_input.user_action, ())
    intent_categories = INTENT_CATEGORIES.get(detected_intent or "", ())
    mood_categories = MOOD_CATEGORIES.get(reply_input.pet.mood, ())

    if reply_input.pet.stats.energy is not None and reply_input.pet.stats.energy <= 30:
        selected.append("tired")
    if reply_input.pet.stats.hunger <= 29:
        selected.append("hungry")

    category_candidates = (
        *action_categories,
        *intent_categories,
        *mood_categories,
        *FALLBACK_CATEGORIES,
    )
    for category in category_candidates:
        if category not in selected:
            selected.append(category)

    available = _phrases_by_category(reply_input.pet.age_stage)
    return tuple(category for category in selected if category in available)


def phrases_for_categories(
    reply_input: PetReplyInput,
    categories: tuple[str, ...],
    *,
    per_category: int = 2,
    max_examples: int = 12,
) -> tuple[tuple[str, str], ...]:
    by_category = _phrases_by_category(reply_input.pet.age_stage)
    selected: list[tuple[str, str]] = []
    for category in categories:
        for phrase in by_category.get(category, ())[:per_category]:
            selected.append((category, adapt_template(phrase, reply_input)))
            if len(selected) >= max_examples:
                return tuple(selected)
    return tuple(selected)


def format_age_message_examples_for_prompt(
    reply_input: PetReplyInput,
    detected_intent: str | None = None,
) -> str:
    age_stage = reply_input.pet.age_stage
    rules = tuple(
        adapt_template(rule, reply_input) for rule in stage_speech_rules(age_stage, limit=11)
    )
    categories = categories_for_reply(reply_input, detected_intent)
    examples = phrases_for_categories(reply_input, categories, per_category=2, max_examples=12)

    rule_lines = "\n".join(f"- {rule}" for rule in rules) if rules else "- нет"
    category_line = ", ".join(categories[:6]) if categories else "нет"
    example_lines = "\n".join(f"- {category}: {phrase}" for category, phrase in examples) or "- нет"

    return f"""
- selected_stage: {age_stage}
- label: {stage_label(age_stage)}
- selected_categories: {category_line}

Speech rules from dataset:
{rule_lines}

Use these examples as style references, not as fixed replies:
- keep facts, name, body, food, fears, home and background from Character Bible;
- copy rhythm, length, emotion and age manner, but do not copy examples literally;
- adapt sounds and placeholders to this exact pet;
- placeholders like [способность] are optional color, not a requirement to mention the same
  ability in every answer;
- if an example conflicts with the pet's Character Bible, keep only the age manner.

Examples:
{example_lines}
""".strip()


def normalize_age_stage(value: str | None) -> PetAgeStage:
    return cast(PetAgeStage, value) if value in ("baby", "teen", "adult") else "baby"
