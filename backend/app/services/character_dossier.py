from __future__ import annotations

import re
from typing import Any

MAX_DURABLE_FACTS = 8


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 320) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit].rstrip()


def _texts(value: Any, *, limit: int = 6, item_limit: int = 180) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    result: list[str] = []
    for item in values:
        text = _text(item, item_limit)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _pet_value(pet: Any, name: str) -> Any:
    return pet.get(name) if isinstance(pet, dict) else getattr(pet, name, None)


def _durable_facts(extensions: dict[str, Any]) -> list[dict[str, str]]:
    overlay = _record(extensions.get("lite_overlay"))
    raw_facts = overlay.get("facts") if isinstance(overlay.get("facts"), list) else []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in reversed(raw_facts):
        fact = _record(raw)
        text = _text(fact.get("text"), 360)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(
            {
                "sphere": _text(fact.get("sphere"), 40) or "character",
                "text": text,
                "source": _text(fact.get("source"), 80) or "unknown",
            }
        )
        if len(result) >= MAX_DURABLE_FACTS:
            break
    return list(reversed(result))


def effective_character_data(pet: Any) -> dict[str, Any]:
    bible = _record(_pet_value(pet, "characterBible"))
    identity = _record(bible.get("identity"))
    genesis = _record(bible.get("genesis"))
    roleplay = _record(bible.get("roleplay_contract"))
    inner = _record(bible.get("inner_state"))
    world = _record(bible.get("world"))
    lore = _record(bible.get("lore"))
    lore_home = _record(lore.get("home"))
    lore_world = _record(lore.get("world"))
    lore_inner = _record(lore.get("inner_life"))
    voice = _record(bible.get("voice"))
    extensions = _record(bible.get("extensions"))

    name = _text(_pet_value(pet, "name"), 80) or _text(identity.get("name"), 80)
    description = _text(_pet_value(pet, "description"), 260)
    result: dict[str, Any] = {
        "identity": {
            "name": name,
            "species": _text(identity.get("species"), 180) or description,
            "role": _text(identity.get("role"), 180),
            "oneLiner": _text(identity.get("one_liner"), 260),
        },
        "genesis": {
            "description": _text(genesis.get("description"), 360),
            "characterTrait": _text(genesis.get("character_trait"), 240),
            "likes": _texts(genesis.get("likes")),
            "usualActions": _texts(genesis.get("does")),
            "appetite": _text(genesis.get("appetite"), 300),
            "conflict": _text(genesis.get("conflict"), 300),
            "storyEngine": _text(genesis.get("story_engine"), 320),
        },
        "innerState": {
            "coreWant": _text(inner.get("core_want") or lore_inner.get("core_want"), 280),
            "innerConflict": _text(
                inner.get("inner_conflict") or lore_inner.get("inner_conflict"),
                300,
            ),
            "fears": _texts(inner.get("fears") or lore_inner.get("fears"), limit=5),
            "comfortActions": _texts(
                inner.get("comfort_actions") or lore_inner.get("comfort_actions"),
                limit=5,
            ),
        },
        "world": {
            "home": _text(
                world.get("home")
                or lore_home.get("place")
                or lore_home.get("room")
                or lore_home.get("story"),
                320,
            ),
            "habitat": _text(
                world.get("habitat") or lore_world.get("environment") or lore_world.get("story"),
                320,
            ),
            "objects": _texts(
                world.get("objects")
                or lore_home.get("objects")
                or lore_world.get("sensory_details")
            ),
            "routines": _texts(world.get("routines") or lore_inner.get("habits")),
            "relationships": _texts(
                world.get("relationships") or _record(lore.get("relationships")).get("friends")
            ),
            "storySeeds": _texts(world.get("story_seeds") or lore.get("story_seeds")),
        },
        "roleplay": {
            "selfIntro": _text(roleplay.get("self_intro"), 220),
            "who": _text(roleplay.get("how_to_answer_who_are_you"), 220),
            "food": _text(roleplay.get("how_to_answer_what_do_you_eat"), 200),
            "home": _text(roleplay.get("how_to_answer_where_do_you_live"), 220),
            "voiceRules": _texts(roleplay.get("voice_rules"), limit=6),
        },
        "voice": {
            "rules": _texts(voice.get("voice_rules") or voice.get("rules"), limit=6),
            "rhythm": _text(voice.get("sentence_rhythm") or voice.get("rhythm"), 180),
            "catchphrases": _texts(voice.get("catchphrases"), limit=4, item_limit=80),
            "avoid": _texts(voice.get("avoid_patterns") or voice.get("avoid"), limit=6),
        },
        "lorebook": [
            {
                "keys": _texts(item.get("keys"), limit=5, item_limit=60),
                "content": _text(item.get("content"), 320),
            }
            for item in bible.get("lorebook_entries", [])[:5]
            if isinstance(item, dict) and _text(item.get("content"), 320)
        ],
        "durableFacts": _durable_facts(extensions),
    }

    def compact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cleaned
                for key, item in value.items()
                if (cleaned := compact(item)) not in (None, "", [], {})
            }
        if isinstance(value, list):
            return [
                cleaned for item in value if (cleaned := compact(item)) not in (None, "", [], {})
            ]
        return value

    return compact(result)


def build_character_capsule(pet: Any, *, include_durable_facts: bool = True) -> str | None:
    data = effective_character_data(pet)
    if not data:
        return None
    identity = _record(data.get("identity"))
    genesis = _record(data.get("genesis"))
    inner = _record(data.get("innerState"))
    world = _record(data.get("world"))
    roleplay = _record(data.get("roleplay"))
    voice = _record(data.get("voice"))

    lines = [
        "КАНОН ПЕРСОНАЖА:",
        "Используй только релевантные детали; не пересказывай капсулу списком.",
    ]

    def add(label: str, value: Any) -> None:
        values = _texts(value, limit=8, item_limit=220)
        if values:
            lines.append(f"{label}: {'; '.join(values)}")

    add(
        "Кто я",
        [
            identity.get("name"),
            identity.get("species"),
            identity.get("role"),
            identity.get("oneLiner"),
        ],
    )
    add("Описание", genesis.get("description"))
    add("Характер", genesis.get("characterTrait"))
    add("Люблю", genesis.get("likes"))
    add("Обычно делаю", genesis.get("usualActions"))
    add("Еда и желания", genesis.get("appetite"))
    add("Внутреннее напряжение", [genesis.get("conflict"), inner.get("innerConflict")])
    add("Главное желание", inner.get("coreWant"))
    add("Страхи", inner.get("fears"))
    add("Успокаивает", inner.get("comfortActions"))
    add("Дом и среда", [world.get("home"), world.get("habitat")])
    add("Мои предметы", world.get("objects"))
    add("Рутины", world.get("routines"))
    add("Устойчивые отношения", world.get("relationships"))
    add("Сюжетные зерна", world.get("storySeeds"))
    add(
        "Манера речи",
        [*roleplay.get("voiceRules", []), *voice.get("rules", []), voice.get("rhythm")],
    )
    add("Ответ о себе", roleplay.get("who"))
    add("Ответ о еде", roleplay.get("food"))
    add("Ответ о доме", roleplay.get("home"))
    lorebook = data.get("lorebook") if isinstance(data.get("lorebook"), list) else []
    add("Факты лора", [item.get("content") for item in lorebook if isinstance(item, dict)])
    if include_durable_facts:
        durable = data.get("durableFacts") if isinstance(data.get("durableFacts"), list) else []
        add(
            "Устойчивые изменения",
            [item.get("text") for item in durable if isinstance(item, dict)],
        )
    return "\n".join(lines)
