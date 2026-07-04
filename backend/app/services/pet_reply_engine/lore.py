from __future__ import annotations

import re
from typing import Any

LoreDict = dict[str, Any]


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(value: Any, limit: int = 4) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        text = _string(item)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _clean_fragment(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(".", "").strip(" ,;:")).strip()


def short_words(text: str | None, limit: int) -> str:
    if not text:
        return ""
    words = _clean_fragment(text).split()
    return " ".join(words[:limit])


def _join(items: tuple[str, ...], limit: int = 3) -> str | None:
    selected = tuple(item for item in items[:limit] if item)
    return ", ".join(selected) if selected else None


def _append_line(lines: list[str], label: str, value: str | None) -> None:
    if value:
        lines.append(f"{label}: {value}")


def _friend_label(friend: Any) -> str | None:
    data = _object(friend)
    name = _string(data.get("name"))
    role = _string(data.get("role"))
    form = _string(data.get("species_or_form"))
    dynamic = _string(data.get("relationship_dynamic"))
    shared_history = _string(data.get("shared_history"))
    relationship = "; ".join(
        part for part in (dynamic, shared_history) if part
    )
    parts = [part for part in (name, role or form, relationship) if part]
    return " - ".join(parts[:3]) if parts else None


def extract_lore(character_bible: dict[str, Any] | None) -> LoreDict | None:
    if not isinstance(character_bible, dict):
        return None
    lore = character_bible.get("lore")
    return lore if isinstance(lore, dict) else None


def lore_text_for_legacy_profile(
    raw_description: str,
    character_bible: dict[str, Any] | None,
) -> str:
    species = ""
    if isinstance(character_bible, dict):
        species = str(character_bible.get("species") or "").strip()
    basis = species or raw_description.strip()
    return f"лора нет; опирайся на визуальную идею: {basis}" if basis else "лора нет"


def compact_lore_lines(lore: LoreDict | None, *, age_stage: str) -> tuple[str, ...]:
    if not lore:
        return ()

    world = _object(lore.get("world"))
    home = _object(lore.get("home"))
    origin = _object(lore.get("origin"))
    relationships = _object(lore.get("relationships"))
    inner_life = _object(lore.get("inner_life"))
    voice = _object(lore.get("voice"))
    growth_arc = _object(lore.get("growth_arc"))
    story_seeds = _join(_strings(lore.get("story_seeds"), limit=4), limit=4)

    lines: list[str] = []
    world_name = _string(world.get("name"))
    environment = _string(world.get("environment"))
    world_story = _string(world.get("story"))
    if world_name and (world_story or environment):
        lines.append(f"мир: {world_name} - {world_story or environment}")
    elif world_story:
        lines.append(f"мир: {world_story}")
    elif environment:
        lines.append(f"мир: {environment}")
    _append_line(lines, "повседневность мира", _string(world.get("daily_life")))

    rules = _join(_strings(world.get("rules"), limit=2), limit=2)
    if rules:
        lines.append(f"правила мира: {rules}")

    home_place = _string(home.get("place"))
    home_room = _string(home.get("room"))
    favorite_spot = _string(home.get("favorite_spot"))
    home_story = _string(home.get("story"))
    if home_story:
        lines.append(f"дом: {home_story}")
    elif home_place or home_room:
        lines.append(f"дом: {home_room or home_place}")
    _append_line(lines, "домашний ритуал", _string(home.get("daily_routine")))
    _append_line(lines, "почему дом важен", _string(home.get("emotional_meaning")))
    if favorite_spot:
        lines.append(f"любимое место: {favorite_spot}")

    objects = _join(_strings(home.get("objects"), limit=4))
    if objects:
        lines.append(f"важные предметы: {objects}")

    origin_story = _string(origin.get("story")) or _string(origin.get("formative_event"))
    _append_line(lines, "происхождение", origin_story)
    _append_line(lines, "поворот прошлого", _string(origin.get("turning_point")))

    caretakers = _join(_strings(origin.get("caretakers"), limit=3))
    family = _join(_strings(relationships.get("family"), limit=3))
    if caretakers or family:
        lines.append(f"близкие: {caretakers or family}")

    _append_line(lines, "история отношений", _string(relationships.get("story")))

    friends = tuple(
        label
        for label in (_friend_label(item) for item in relationships.get("friends", []))
        if label
    )
    friends_text = _join(friends, limit=2)
    if friends_text:
        lines.append(f"друзья: {friends_text}")

    attitude = _string(relationships.get("attitude_to_user"))
    if attitude:
        lines.append(f"отношение к собеседнику: {attitude}")
    if story_seeds:
        lines.append(f"открытые темы для раскрытия: {story_seeds}")

    _append_line(lines, "главное желание", _string(inner_life.get("core_want")))
    _append_line(lines, "внутренний конфликт", _string(inner_life.get("inner_conflict")))
    _append_line(lines, "личное воспоминание", _string(inner_life.get("private_memory")))

    preference = preference_fragment(lore)
    fears = _join(_strings(inner_life.get("fears"), limit=3))
    dreams = _join(_strings(inner_life.get("dreams"), limit=3))
    habits = _join(_strings(inner_life.get("habits"), limit=3))
    flaws = _join(_strings(inner_life.get("flaws"), limit=3))
    if preference:
        lines.append(f"любимое с причиной: {preference}")
    if fears:
        lines.append(f"боится: {fears}")
    if dreams:
        lines.append(f"мечта: {dreams}")
    if habits:
        lines.append(f"привычки: {habits}")
    if flaws:
        lines.append(f"слабость характера: {flaws}")

    _append_line(lines, "манера речи", _string(voice.get("speech_pattern")))

    hooks = _join(_strings(voice.get("topic_hooks"), limit=3))
    if hooks:
        lines.append(f"темы для редкого упоминания: {hooks}")

    age_arc = _string(growth_arc.get(age_stage))
    if age_arc:
        lines.append(f"возрастная дуга: {age_arc}")

    return tuple(lines[:20])


def home_fragment(lore: LoreDict | None) -> str | None:
    if not lore:
        return None
    home = _object(lore.get("home"))
    world = _object(lore.get("world"))
    return (
        _string(home.get("story"))
        or _string(home.get("emotional_meaning"))
        or _string(home.get("favorite_spot"))
        or _string(home.get("room"))
        or _string(home.get("place"))
        or _string(world.get("story"))
        or _string(world.get("environment"))
    )


_WEAK_PREFERENCE_PATTERNS = (
    re.compile(r"\bкоротк\w*\s+просьб", re.IGNORECASE),
    re.compile(r"\bпросьб[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bпохвал[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bкомплимент[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bвнимани[еяю]\b", re.IGNORECASE),
)
_DECORATIVE_PREFERENCE_PATTERNS = (
    re.compile(r"\bтуман[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bлейк[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bсвет[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bрос[а-я]*\b", re.IGNORECASE),
    re.compile(r"\bголос[а-я]*\b", re.IGNORECASE),
)
_GROUNDING_WORD_PATTERN = re.compile(r"[А-Яа-яЁёA-Za-z0-9]{4,}")


def _all_lore_text(lore: LoreDict) -> str:
    parts: list[str] = []

    def collect(value: Any, key: str | None = None) -> None:
        if key == "likes":
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for child_key, item in value.items():
                collect(item, str(child_key))

    collect(lore)
    return " ".join(parts).casefold()


def _is_story_grounded(item: str, lore_text: str) -> bool:
    words = {
        word.casefold()
        for word in _GROUNDING_WORD_PATTERN.findall(item)
        if len(word) >= 4
    }
    return bool(words and any(word in lore_text for word in words))


def _is_weak_preference(item: str, lore_text: str) -> bool:
    if any(pattern.search(item) for pattern in _WEAK_PREFERENCE_PATTERNS):
        return True
    if any(pattern.search(item) for pattern in _DECORATIVE_PREFERENCE_PATTERNS):
        return not _is_story_grounded(item, lore_text)
    return False


def _grounded_preferences(lore: LoreDict | None, limit: int = 2) -> tuple[str, ...]:
    if not lore:
        return ()
    inner_life = _object(lore.get("inner_life"))
    lore_text = _all_lore_text(lore)
    result: list[str] = []
    for item in _strings(inner_life.get("likes"), limit=6):
        if _is_weak_preference(item, lore_text):
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return tuple(result)


def _preference_reason(lore: LoreDict) -> str | None:
    home = _object(lore.get("home"))
    origin = _object(lore.get("origin"))
    relationships = _object(lore.get("relationships"))
    return (
        _string(origin.get("formative_event"))
        or _string(origin.get("story"))
        or _string(relationships.get("story"))
        or _string(home.get("story"))
    )


def _home_preference_fragment(lore: LoreDict) -> str | None:
    home = _object(lore.get("home"))
    spot = _string(home.get("favorite_spot")) or _string(home.get("room")) or _string(
        home.get("place")
    )
    story = _string(home.get("story"))
    if spot and story:
        return f"мне спокойнее всего там: {short_words(story, 18)}"
    if spot:
        return f"мне спокойнее всего в месте: {spot}"
    return home_fragment(lore)


def preference_fragment(lore: LoreDict | None) -> str | None:
    if not lore:
        return None
    likes = _grounded_preferences(lore, limit=1)
    if likes:
        reason = _preference_reason(lore)
        if reason:
            return f"мне нравится {likes[0]}: {short_words(reason, 18)}"
        return f"мне нравится {likes[0]}"
    return _home_preference_fragment(lore)


def relationship_fragment(lore: LoreDict | None) -> str | None:
    if not lore:
        return None
    relationships = _object(lore.get("relationships"))
    story = _string(relationships.get("story"))
    friends = tuple(
        label
        for label in (_friend_label(item) for item in relationships.get("friends", []))
        if label
    )
    if friends:
        return friends[0]
    if story:
        return story
    family = _strings(relationships.get("family"), limit=1)
    return family[0] if family else None


def origin_fragment(lore: LoreDict | None) -> str | None:
    if not lore:
        return None
    origin = _object(lore.get("origin"))
    return (
        _string(origin.get("story"))
        or _string(origin.get("turning_point"))
        or _string(origin.get("birthplace"))
        or _string(origin.get("formative_event"))
    )
