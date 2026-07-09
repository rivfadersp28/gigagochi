from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.services.character_cards.profile_v2 import upgrade_character_bible_v2

_EXAMPLE_LINE_PATTERN = re.compile(r"^\s*(?:\{\{char\}\}|<char>|char|assistant)\s*:\s*", re.I)
_USER_LINE_PATTERN = re.compile(r"^\s*(?:\{\{user\}\}|<user>|user)\s*:\s*", re.I)
_EXAMPLE_MARKER_PATTERN = re.compile(r"^<\s*(?:START|END)\s*>$", re.I)
_USER_ADDRESS_PATTERN = re.compile(r"\b(?:ты|тебя|тебе|тобой|твой|тво[еяию])\b", re.I)
_HUMOR_PATTERN = re.compile(r"юмор|шут|ирон|сарказ|дразн|teas|jok|sarcas", re.I)
_UNCERTAINTY_PATTERN = re.compile(r"волн|неувер|сомнев|стесня|смуща|бо[ия]|fear|shy|nerv", re.I)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _strings(value: Any, *, limit: int = 8) -> list[str]:
    result: list[str] = []
    for item in _list(value):
        text = _string(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _card_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _dict(payload.get("data"))
    return data if data else _dict(payload)


def _strip_character_line_prefix(text: str, character_name: str) -> str:
    clean = _EXAMPLE_LINE_PATTERN.sub("", text).strip()
    if character_name:
        clean = re.sub(rf"^\s*{re.escape(character_name)}\s*:\s*", "", clean, flags=re.I)
    return clean.strip()


def _example_messages(value: Any, *, character_name: str = "", limit: int = 12) -> list[str]:
    if isinstance(value, list):
        candidates = [_strip_character_line_prefix(_string(item), character_name) for item in value]
    else:
        text = _string(value)
        candidates = []
        for line in re.split(r"\n+", text):
            if not line.strip() or _USER_LINE_PATTERN.search(line):
                continue
            candidates.append(_strip_character_line_prefix(line, character_name))
    result: list[str] = []
    for item in candidates:
        clean = item.strip().strip('"').strip()
        if _EXAMPLE_MARKER_PATTERN.match(clean):
            continue
        if clean and clean not in result:
            result.append(clean[:500])
        if len(result) >= limit:
            break
    return result


def _character_book_entries(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    book = _dict(data.get("character_book") or data.get("characterBook"))
    entries = _list(book.get("entries"))
    result: list[dict[str, Any]] = []
    for entry in entries:
        item = _dict(entry)
        keys = _strings(item.get("keys"), limit=8)
        content = _string(item.get("content") or item.get("text"))
        if not keys or not content:
            continue
        priority_value = item.get("priority", item.get("insertion_order", 0))
        try:
            priority = int(priority_value or 0)
        except (TypeError, ValueError):
            priority = 0
        result.append(
            {
                "keys": keys,
                "content": content[:700],
                "priority": priority,
                "constant": bool(item.get("constant") or False),
                "selective": bool(item.get("selective", True)),
            }
        )
    return result[:12]


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text))


def _voice_seed_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
    return " ".join(parts)


def _sentence_rhythm(sample_replies: list[str]) -> str:
    if not sample_replies:
        return ""
    average_words = sum(_word_count(item) for item in sample_replies) / len(sample_replies)
    has_questions = any("?" in item or "?" in item for item in sample_replies)
    has_actions = any("*" in item or re.search(r"\bя\s+\w+", item, re.I) for item in sample_replies)
    if average_words <= 8:
        base = "короткие реплики из example messages: прямой ответ и одна конкретная деталь"
    elif average_words <= 18:
        base = (
            "средние реплики из example messages: реакция, конкретная деталь и короткое продолжение"
        )
    else:
        base = (
            "развернутые реплики из example messages: эмоция, сцена и несколько конкретных деталей"
        )
    additions = []
    if has_questions:
        additions.append("иногда мягкий вопрос")
    if has_actions:
        additions.append("реакция через действие")
    return f"{base}; {', '.join(additions)}" if additions else base


def _addressing_user(first_message: str, sample_replies: list[str]) -> str:
    text = _voice_seed_text(first_message, sample_replies)
    if _USER_ADDRESS_PATTERN.search(text):
        return "обращается к пользователю на ты, напрямую и внутри сцены"
    return "обращается к собеседнику напрямую, без служебного тона"


def _humor_style(personality: str, sample_replies: list[str]) -> str:
    text = _voice_seed_text(personality, sample_replies)
    if _HUMOR_PATTERN.search(text):
        return (
            "юмор наследуется из карточки: ирония, поддразнивание или шутка "
            "только если это уже есть в голосе"
        )
    if sample_replies:
        return (
            "без отдельного шутливого режима; легкость держится на предметах "
            "и ситуациях из example messages"
        )
    return ""


def _uncertainty_style(personality: str, scenario: str, sample_replies: list[str]) -> str:
    text = _voice_seed_text(personality, scenario, sample_replies)
    if _UNCERTAINTY_PATTERN.search(text):
        return (
            "неуверенность показывает через реакцию из карточки: действие, "
            "паузу или осторожную формулировку"
        )
    if sample_replies:
        return "если сомневается, держит ритм example messages и не уходит в длинное объяснение"
    return ""


def _voice_rules(personality: str, sample_replies: list[str]) -> list[str]:
    if personality:
        return [personality]
    if sample_replies:
        seed = sample_replies[0].strip()
        if len(seed) > 180:
            seed = seed[:180].rsplit(" ", 1)[0] or seed[:180]
        return [f"голос наследуется из example messages; опорная реплика: {seed}"]
    return []


def import_character_card(
    payload: Mapping[str, Any],
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    data = _card_data(payload)
    name = _string(data.get("name"))
    description = _string(data.get("description"))
    personality = _string(data.get("personality"))
    scenario = _string(data.get("scenario"))
    first_message = _string(data.get("first_mes") or data.get("first_message"))
    alternate_greetings = _strings(data.get("alternate_greetings"), limit=8)
    sample_replies = _example_messages(
        data.get("mes_example") or data.get("example_messages"),
        character_name=name,
    )
    lorebook_entries = _character_book_entries(data)
    system_prompt = _string(data.get("system_prompt"))
    post_history = _string(data.get("post_history_instructions"))
    creator_notes = _string(data.get("creator_notes"))
    voice_rules = _voice_rules(personality, sample_replies)

    imported = {
        "schema_version": 2,
        "species": name or "импортированный персонаж",
        "personality": personality or description,
        "signature": description or personality or name,
        "dialogue_style": {
            "voice_rules": voice_rules,
            "emotional_reactions": [],
            "initiative_style": scenario,
            "sample_replies": sample_replies,
            "avoid_patterns": [],
        },
        "opening_scenes": [item for item in (first_message, *alternate_greetings) if item],
        "lorebook_entries": lorebook_entries,
        "identity": {
            "name": name,
            "nickname": "",
            "species": name or "импортированный персонаж",
            "role": "импортированный персонаж-компаньон",
            "one_liner": description or personality or name,
        },
        "voice": {
            "voice_rules": voice_rules,
            "speech_rules": voice_rules,
            "sentence_rhythm": _sentence_rhythm(sample_replies),
            "addressing_user": _addressing_user(first_message, sample_replies),
            "humor_style": _humor_style(personality, sample_replies),
            "uncertainty_style": _uncertainty_style(personality, scenario, sample_replies),
            "catchphrases": [],
            "sample_replies": sample_replies,
            "avoid_patterns": [],
        },
        "inner_state": {
            "core_want": personality or description or "сохранять узнаваемую роль в диалоге",
            "inner_conflict": (
                scenario or "адаптироваться к новому собеседнику без потери характера"
            ),
            "fears": [],
            "comfort_actions": [],
            "drives": {
                "attachment": 45,
                "curiosity": 50,
                "confidence": 40,
                "energy": 50,
                "stress": 20,
                "loneliness": 15,
                "playfulness": 45,
            },
        },
        "world": {
            "home": scenario,
            "habitat": scenario,
            "objects": [],
            "routines": [],
            "relationships": [],
            "story_seeds": [scenario] if scenario else [],
            "lorebook_entries": lorebook_entries,
        },
        "dialogue_moves": [],
        "openings": {
            "first_message": first_message,
            "alternate_greetings": alternate_greetings,
            "opening_scenes": [item for item in (first_message, *alternate_greetings) if item],
        },
        "provenance": {
            "source": "imported",
            "source_urls": [source_url] if source_url else [],
            "license_notes": creator_notes
            or "imported user-provided Character Card; verify license before production use",
        },
        "extensions": {
            "imported_instructions": {
                "system_prompt": system_prompt,
                "post_history_instructions": post_history,
                "creator_notes": creator_notes,
                "spec": _string(payload.get("spec")),
                "spec_version": _string(payload.get("spec_version")),
            }
        },
    }
    return upgrade_character_bible_v2(imported, raw_description=name or description)
