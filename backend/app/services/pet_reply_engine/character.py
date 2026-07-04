from __future__ import annotations

from typing import Any

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
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        if len(result) >= limit:
            break
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
    text = f"{raw_description} {_text_from_bible(character_bible)}".casefold()
    speech_hint = None
    favorite_words: tuple[str, ...] = ()
    forbidden_words: tuple[str, ...] = ()
    quirks: tuple[str, ...] = ()
    if character_bible and isinstance(character_bible.get("personality"), str):
        speech_hint = str(character_bible["personality"]).strip()
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

    return PetPersonality(
        temperament=_infer_temperament(text),
        social_style=_infer_social_style(text),
        speech_flavor=speech_hint or "коротко, живо, немного нежно",
        favorite_words=favorite_words,
        forbidden_words=forbidden_words,
        quirks=quirks,
    )
