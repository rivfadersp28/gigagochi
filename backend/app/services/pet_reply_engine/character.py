from __future__ import annotations

from typing import Any

from app.services.character_cards import dialogue_moves_for_profile, normalize_character_profile_v2
from app.services.pet_reply_engine.models import PetPersonality, SocialStyle, Temperament


def _collect_text_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(
            text
            for item in value
            for text in _collect_text_values(item)
            if text
        )
    if isinstance(value, dict):
        return tuple(
            text
            for item in value.values()
            for text in _collect_text_values(item)
            if text
        )
    return ()


def _text_from_bible(character_bible: dict[str, Any] | None) -> str:
    if not character_bible:
        return ""
    parts: list[str] = []
    for key in (
        "personality",
        "dialogue_style",
        "opening_scenes",
        "lorebook_entries",
        "species",
        "signature",
        "signature_features",
        "materials",
        "proportions",
        "lore",
    ):
        value = character_bible.get(key)
        parts.extend(_collect_text_values(value))
    return " ".join(parts).casefold()


def _string_tuple(value: Any, limit: int = 4) -> tuple[str, ...]:
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


def _lorebook_entry_tuple(value: Any, limit: int = 6) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        keys = _string_tuple(item.get("keys"), limit=4)
        content = item.get("content")
        if not keys or not isinstance(content, str) or not content.strip():
            continue
        result.append(f"{', '.join(keys)}: {content.strip()}")
        if len(result) >= limit:
            break
    return tuple(result)


def _profile_lorebook_tuple(profile: dict[str, Any], limit: int = 6) -> tuple[str, ...]:
    world = profile.get("world") if isinstance(profile.get("world"), dict) else {}
    return _lorebook_entry_tuple(world.get("lorebook_entries"), limit=limit)


def _merge_unique(*items: tuple[str, ...], limit: int = 8) -> tuple[str, ...]:
    result: list[str] = []
    for values in items:
        for value in values:
            clean = value.strip()
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= limit:
                return tuple(result)
    return tuple(result)


def _infer_temperament(text: str) -> Temperament:
    if any(marker in text for marker in ("quiet", "shy", "timid", "тих", "робк", "застен")):
        return "shy"
    if any(marker in text for marker in ("bold", "brave", "смел", "бойк", "дерз")):
        return "bold"
    if any(marker in text for marker in ("curious", "любопыт", "интерес")):
        return "curious"
    if any(marker in text for marker in ("calm", "спокой", "ровн")):
        return "calm"
    if any(marker in text for marker in ("soft", "gentle", "неж", "мягк")):
        return "soft"
    return "playful"


def _infer_social_style(text: str) -> SocialStyle:
    if any(marker in text for marker in ("mischief", "trick", "шал", "озор")):
        return "mischievous"
    if any(marker in text for marker in ("independent", "самостоят", "горд")):
        return "independent"
    if any(marker in text for marker in ("clingy", "attached", "липк", "тянет")):
        return "clingy"
    return "warm"


def build_default_personality(
    raw_description: str,
    character_bible: dict[str, Any] | None = None,
) -> PetPersonality:
    profile_v2 = normalize_character_profile_v2(character_bible, raw_description=raw_description)
    text = f"{raw_description} {_text_from_bible(character_bible)}".casefold()
    speech_hint = None
    favorite_words: tuple[str, ...] = ()
    forbidden_words: tuple[str, ...] = ()
    quirks: tuple[str, ...] = ()
    speech_rules: tuple[str, ...] = ()
    emotional_reactions: tuple[str, ...] = ()
    initiative_style = None
    sample_replies: tuple[str, ...] = ()
    avoid_patterns: tuple[str, ...] = ()
    opening_scenes: tuple[str, ...] = ()
    lorebook_entries: tuple[str, ...] = ()
    dialogue_moves: tuple[str, ...] = dialogue_moves_for_profile(
        character_bible,
        raw_description=raw_description,
        limit=6,
    )
    if character_bible and isinstance(character_bible.get("personality"), str):
        speech_hint = str(character_bible["personality"]).strip()
        dialogue_style = character_bible.get("dialogue_style")
        if isinstance(dialogue_style, dict):
            speech_rules = _string_tuple(dialogue_style.get("voice_rules"), limit=6)
            emotional_reactions = _string_tuple(
                dialogue_style.get("emotional_reactions"), limit=6
            )
            if isinstance(dialogue_style.get("initiative_style"), str):
                initiative_style = str(dialogue_style["initiative_style"]).strip() or None
            sample_replies = _string_tuple(dialogue_style.get("sample_replies"), limit=6)
            avoid_patterns = _string_tuple(dialogue_style.get("avoid_patterns"), limit=6)
        opening_scenes = _string_tuple(character_bible.get("opening_scenes"), limit=3)
        lorebook_entries = _lorebook_entry_tuple(character_bible.get("lorebook_entries"), limit=6)
        lore = character_bible.get("lore")
        if isinstance(lore, dict):
            voice = lore.get("voice")
            inner_life = lore.get("inner_life")
            if isinstance(voice, dict):
                favorite_words = _string_tuple(voice.get("favorite_phrases"), limit=4)
                forbidden_words = _string_tuple(voice.get("avoid_saying"), limit=6)
            if isinstance(inner_life, dict):
                quirks = (
                    *_string_tuple(inner_life.get("habits"), limit=3),
                    *_string_tuple(inner_life.get("flaws"), limit=2),
                )

    voice = profile_v2.get("voice") if isinstance(profile_v2.get("voice"), dict) else {}
    inner_state = (
        profile_v2.get("inner_state") if isinstance(profile_v2.get("inner_state"), dict) else {}
    )
    world = profile_v2.get("world") if isinstance(profile_v2.get("world"), dict) else {}
    speech_rules = _merge_unique(
        speech_rules,
        _string_tuple(voice.get("voice_rules"), limit=8),
        _string_tuple(voice.get("speech_rules"), limit=8),
        limit=8,
    )
    favorite_words = _merge_unique(
        favorite_words,
        _string_tuple(voice.get("catchphrases"), limit=4),
        limit=4,
    )
    forbidden_words = _merge_unique(
        forbidden_words,
        _string_tuple(voice.get("avoid_patterns"), limit=8),
        limit=8,
    )
    quirks = _merge_unique(
        quirks,
        _string_tuple(inner_state.get("comfort_actions"), limit=4),
        _string_tuple(world.get("routines"), limit=4),
        limit=8,
    )
    sample_replies = _merge_unique(
        sample_replies,
        _string_tuple(voice.get("sample_replies"), limit=12),
        limit=12,
    )
    avoid_patterns = _merge_unique(
        avoid_patterns,
        _string_tuple(voice.get("avoid_patterns"), limit=12),
        limit=12,
    )
    openings = profile_v2.get("openings") if isinstance(profile_v2.get("openings"), dict) else {}
    opening_scenes = _merge_unique(
        opening_scenes,
        _string_tuple(openings.get("opening_scenes"), limit=6),
        _string_tuple(openings.get("alternate_greetings"), limit=6),
        limit=6,
    )
    lorebook_entries = _merge_unique(
        lorebook_entries,
        _profile_lorebook_tuple(profile_v2, limit=8),
        limit=8,
    )

    return PetPersonality(
        temperament=_infer_temperament(text),
        social_style=_infer_social_style(text),
        speech_flavor=speech_hint or "коротко, живо, немного нежно",
        favorite_words=favorite_words,
        forbidden_words=forbidden_words,
        quirks=quirks,
        speech_rules=speech_rules,
        emotional_reactions=emotional_reactions,
        initiative_style=initiative_style,
        sample_replies=sample_replies,
        avoid_patterns=avoid_patterns,
        opening_scenes=opening_scenes,
        lorebook_entries=lorebook_entries,
        dialogue_moves=dialogue_moves,
    )
