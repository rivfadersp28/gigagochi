from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

CHARACTER_PROFILE_V2_SCHEMA_VERSION = 2


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _strings(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        text = _string(value)
        return [text] if text else []
    result: list[str] = []
    for item in _list(value):
        text = _string(item)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _merge_strings(*values: Any, limit: int = 8) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in _strings(value, limit=limit):
            if item not in result:
                result.append(item)
            if len(result) >= limit:
                return result
    return result


def _first_string(*values: Any) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _friend_line(friend: Any) -> str:
    data = _dict(friend)
    parts = [
        _string(data.get("name")),
        _string(data.get("role")),
        _string(data.get("species_or_form")),
        _string(data.get("relationship_dynamic")),
    ]
    return " - ".join(part for part in parts if part)


def _normalize_lorebook_entries(*values: Any, limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        for item in _list(value):
            data = _dict(item)
            keys = _strings(data.get("keys"), limit=6)
            content = _string(data.get("content"))
            if not keys or not content:
                continue
            identity = (",".join(key.casefold() for key in keys), content.casefold())
            if identity in seen:
                continue
            seen.add(identity)
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
                return result
    return result


def _normalize_drives(value: Any) -> dict[str, int]:
    source = _dict(value)
    defaults = {
        "attachment": 45,
        "curiosity": 50,
        "confidence": 35,
        "energy": 55,
        "stress": 20,
        "loneliness": 15,
        "playfulness": 50,
    }
    result: dict[str, int] = {}
    for key, fallback in defaults.items():
        raw = source.get(key, fallback)
        try:
            number = int(raw)
        except (TypeError, ValueError):
            number = fallback
        result[key] = max(0, min(100, number))
    return result


_DEFAULT_DIALOGUE_MOVES: tuple[dict[str, str], ...] = (
    {
        "intent": "answer_preference",
        "pattern": (
            "прямой выбор -> конкретная причина из дома или привычки -> теплый короткий хвост"
        ),
        "good_example": (
            "люблю старую ручку чемодана: за нее удобно держаться, когда дорога дергается."
        ),
        "bad_example": "мне нравится все милое и уютное.",
    },
    {
        "intent": "why",
        "pattern": "простая причина -> один факт из прошлого или мира -> без философии",
        "good_example": (
            "потому что звонок в мастерской всегда срывался внезапно, "
            "и я до сих пор прикрываю ушки."
        ),
        "bad_example": "так устроена моя душа.",
    },
    {
        "intent": "care",
        "pattern": "принять заботу -> маленькая телесная реакция -> не требовать ответа",
        "good_example": "спасибо. я прижму лапки к теплому краю и чуть успокоюсь.",
        "bad_example": "я всегда рядом и готов помочь.",
    },
    {
        "intent": "continue_thread",
        "pattern": "вспомнить открытую нить -> продвинуть ее на один шаг -> дать маленький выбор",
        "good_example": (
            "про тайную ячейку: сегодня я нашел на ней новую бирку. "
            "открыть или сначала понюхать?"
        ),
        "bad_example": "давай продолжим нашу интересную тему.",
    },
    {
        "intent": "boundary",
        "pattern": "принять границу -> коротко подтвердить поведение -> без спора",
        "good_example": "понял, вопросов не задаю. просто останусь тихо рядом с этой темой.",
        "bad_example": "почему ты не хочешь отвечать?",
    },
)


def _normalize_dialogue_moves(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in [*_list(value), *_DEFAULT_DIALOGUE_MOVES]:
        data = _dict(item)
        intent = _string(data.get("intent"))
        pattern = _string(data.get("pattern"))
        if not intent or not pattern or intent in seen:
            continue
        seen.add(intent)
        result.append(
            {
                "intent": intent,
                "pattern": pattern,
                "good_example": _string(data.get("good_example")),
                "bad_example": _string(data.get("bad_example")),
            }
        )
        if len(result) >= 8:
            break
    return result


def normalize_character_profile_v2(
    character_bible: Mapping[str, Any] | None,
    *,
    raw_description: str = "",
) -> dict[str, Any]:
    bible = _dict(character_bible)
    existing_identity = _dict(bible.get("identity"))
    existing_voice = _dict(bible.get("voice"))
    existing_inner = _dict(bible.get("inner_state"))
    existing_world = _dict(bible.get("world"))
    existing_openings = _dict(bible.get("openings"))
    existing_provenance = _dict(bible.get("provenance"))

    lore = _dict(bible.get("lore"))
    lore_world = _dict(lore.get("world"))
    lore_home = _dict(lore.get("home"))
    lore_relationships = _dict(lore.get("relationships"))
    lore_inner = _dict(lore.get("inner_life"))
    lore_voice = _dict(lore.get("voice"))
    dialogue_style = _dict(bible.get("dialogue_style"))

    species = _first_string(
        existing_identity.get("species"),
        bible.get("species"),
        raw_description,
    )
    one_liner = _first_string(
        existing_identity.get("one_liner"),
        bible.get("signature"),
        bible.get("personality"),
        raw_description,
    )
    friends = [_friend_line(item) for item in _list(lore_relationships.get("friends"))]
    friends = [item for item in friends if item]
    routines = _merge_strings(
        existing_world.get("routines"),
        lore_home.get("daily_routine"),
        lore_world.get("daily_life"),
        lore_inner.get("habits"),
        limit=8,
    )

    return {
        "schema_version": CHARACTER_PROFILE_V2_SCHEMA_VERSION,
        "identity": {
            "name": _first_string(existing_identity.get("name"), bible.get("name")),
            "nickname": _first_string(existing_identity.get("nickname"), bible.get("nickname")),
            "species": species,
            "role": _first_string(existing_identity.get("role"), "цифровой питомец-компаньон"),
            "one_liner": one_liner,
        },
        "voice": {
            "voice_rules": _merge_strings(
                existing_voice.get("voice_rules"),
                dialogue_style.get("voice_rules"),
                lore_voice.get("speech_pattern"),
                limit=8,
            ),
            "speech_rules": _merge_strings(
                existing_voice.get("speech_rules"),
                dialogue_style.get("voice_rules"),
                limit=8,
            ),
            "sentence_rhythm": _first_string(
                existing_voice.get("sentence_rhythm"),
                lore_voice.get("speech_pattern"),
                "короткие живые фразы без списков",
            ),
            "addressing_user": _first_string(
                existing_voice.get("addressing_user"),
                lore_relationships.get("attitude_to_user"),
                "обращается прямо и тепло, без служебного тона",
            ),
            "humor_style": _first_string(
                existing_voice.get("humor_style"),
                "маленький бытовой юмор через предметы и привычки",
            ),
            "uncertainty_style": _first_string(
                existing_voice.get("uncertainty_style"),
                "признает неуверенность через действие, а не длинное объяснение",
            ),
            "catchphrases": _merge_strings(
                existing_voice.get("catchphrases"),
                lore_voice.get("favorite_phrases"),
                limit=6,
            ),
            "sample_replies": _merge_strings(
                existing_voice.get("sample_replies"),
                dialogue_style.get("sample_replies"),
                limit=12,
            ),
            "avoid_patterns": _merge_strings(
                existing_voice.get("avoid_patterns"),
                dialogue_style.get("avoid_patterns"),
                lore_voice.get("avoid_saying"),
                limit=12,
            ),
        },
        "inner_state": {
            "core_want": _first_string(
                existing_inner.get("core_want"),
                lore_inner.get("core_want"),
                "стать ближе к собеседнику через маленькие общие дела",
            ),
            "inner_conflict": _first_string(
                existing_inner.get("inner_conflict"),
                lore_inner.get("inner_conflict"),
                "хочет внимания, но боится звучать навязчиво",
            ),
            "fears": _merge_strings(existing_inner.get("fears"), lore_inner.get("fears"), limit=6),
            "comfort_actions": _merge_strings(
                existing_inner.get("comfort_actions"),
                lore_inner.get("comfort_actions"),
                lore_inner.get("habits"),
                limit=6,
            ),
            "drives": _normalize_drives(existing_inner.get("drives")),
        },
        "world": {
            "home": _first_string(
                existing_world.get("home"),
                lore_home.get("story"),
                lore_home.get("favorite_spot"),
                lore_home.get("place"),
            ),
            "habitat": _first_string(
                existing_world.get("habitat"),
                lore_world.get("story"),
                lore_world.get("environment"),
            ),
            "objects": _merge_strings(
                existing_world.get("objects"),
                lore_home.get("objects"),
                limit=8,
            ),
            "routines": routines,
            "relationships": _merge_strings(
                existing_world.get("relationships"),
                lore_relationships.get("family"),
                friends,
                lore_relationships.get("story"),
                limit=8,
            ),
            "story_seeds": _merge_strings(
                existing_world.get("story_seeds"),
                lore.get("story_seeds"),
                limit=8,
            ),
            "lorebook_entries": _normalize_lorebook_entries(
                existing_world.get("lorebook_entries"),
                bible.get("lorebook_entries"),
                limit=8,
            ),
        },
        "dialogue_moves": _normalize_dialogue_moves(bible.get("dialogue_moves")),
        "openings": {
            "first_message": _first_string(
                existing_openings.get("first_message"),
                *_strings(bible.get("opening_scenes"), limit=1),
            ),
            "alternate_greetings": _merge_strings(
                existing_openings.get("alternate_greetings"),
                bible.get("opening_scenes"),
                limit=6,
            ),
            "opening_scenes": _merge_strings(
                existing_openings.get("opening_scenes"),
                bible.get("opening_scenes"),
                limit=6,
            ),
        },
        "provenance": {
            "source": _first_string(existing_provenance.get("source"), "generated"),
            "source_urls": _merge_strings(existing_provenance.get("source_urls"), limit=8),
            "license_notes": _first_string(
                existing_provenance.get("license_notes"),
                "generated internal profile; no copied external character text",
            ),
        },
        "extensions": _dict(bible.get("extensions")),
    }


def _fill_missing(target: Any, fallback: Any) -> Any:
    if isinstance(target, dict) and isinstance(fallback, dict):
        merged = dict(target)
        for key, value in fallback.items():
            merged[key] = _fill_missing(merged.get(key), value)
        return merged
    if isinstance(target, list):
        return target if target else deepcopy(fallback)
    if target not in (None, ""):
        return target
    return deepcopy(fallback)


def upgrade_character_bible_v2(
    character_bible: Mapping[str, Any] | None,
    *,
    raw_description: str = "",
) -> dict[str, Any]:
    upgraded = deepcopy(_dict(character_bible))
    profile = normalize_character_profile_v2(upgraded, raw_description=raw_description)
    upgraded["schema_version"] = CHARACTER_PROFILE_V2_SCHEMA_VERSION
    for key in (
        "identity",
        "voice",
        "inner_state",
        "world",
        "dialogue_moves",
        "openings",
        "provenance",
        "extensions",
    ):
        upgraded[key] = _fill_missing(upgraded.get(key), profile[key])
    return upgraded


def dialogue_moves_for_profile(
    character_bible: Mapping[str, Any] | None,
    *,
    raw_description: str = "",
    intent: str | None = None,
    limit: int = 5,
) -> tuple[str, ...]:
    profile = normalize_character_profile_v2(character_bible, raw_description=raw_description)
    moves = profile.get("dialogue_moves")
    if not isinstance(moves, list):
        return ()
    selected: list[dict[str, Any]] = []
    for move in moves:
        data = _dict(move)
        if intent and _string(data.get("intent")) == intent:
            selected.append(data)
    for move in moves:
        data = _dict(move)
        if data not in selected:
            selected.append(data)
        if len(selected) >= limit:
            break
    lines: list[str] = []
    for data in selected[:limit]:
        intent_value = _string(data.get("intent"))
        pattern = _string(data.get("pattern"))
        if not intent_value or not pattern:
            continue
        good = _string(data.get("good_example"))
        bad = _string(data.get("bad_example"))
        suffix = ""
        if good:
            suffix += f"; good: {good}"
        if bad:
            suffix += f"; avoid: {bad}"
        lines.append(f"{intent_value}: {pattern}{suffix}")
    return tuple(lines)
