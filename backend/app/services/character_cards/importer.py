from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.services.character_cards.profile_v2 import upgrade_character_bible_v2

_EXAMPLE_LINE_PATTERN = re.compile(r"^\s*(?:\{\{char\}\}|<char>|char|assistant)\s*:\s*", re.I)
_USER_LINE_PATTERN = re.compile(r"^\s*(?:\{\{user\}\}|<user>|user)\s*:\s*", re.I)


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


def _example_messages(value: Any, *, limit: int = 12) -> list[str]:
    if isinstance(value, list):
        candidates = [_string(item) for item in value]
    else:
        text = _string(value)
        candidates = []
        for line in re.split(r"\n+", text):
            if not line.strip() or _USER_LINE_PATTERN.search(line):
                continue
            candidates.append(_EXAMPLE_LINE_PATTERN.sub("", line).strip())
    result: list[str] = []
    for item in candidates:
        clean = item.strip().strip('"').strip()
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
    sample_replies = _example_messages(data.get("mes_example") or data.get("example_messages"))
    lorebook_entries = _character_book_entries(data)
    system_prompt = _string(data.get("system_prompt"))
    post_history = _string(data.get("post_history_instructions"))
    creator_notes = _string(data.get("creator_notes"))

    imported = {
        "schema_version": 2,
        "species": name or "импортированный персонаж",
        "personality": personality or description,
        "signature": description or personality or name,
        "dialogue_style": {
            "voice_rules": [item for item in (personality, system_prompt) if item],
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
            "role": "импортированный цифровой персонаж",
            "one_liner": description or personality or name,
        },
        "voice": {
            "voice_rules": [item for item in (personality, system_prompt) if item],
            "speech_rules": [personality] if personality else [],
            "sentence_rhythm": "сохраняет ритм импортированных example messages",
            "addressing_user": "обращается напрямую к собеседнику",
            "humor_style": "",
            "uncertainty_style": "",
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
