from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_VOICE_RULES = 6
MAX_CATCHPHRASES = 4
MAX_SAMPLE_REPLIES = 5
MAX_AVOID_PATTERNS = 6


@dataclass(frozen=True)
class VoiceProfile:
    stage: str | None
    rules: tuple[str, ...]
    catchphrases: tuple[str, ...]
    sample_replies: tuple[str, ...]
    avoid_patterns: tuple[str, ...]
    sentence_length: str
    warmth: str
    humor: str
    directness: str


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return " ".join(value.split()).strip()


def _string_value(value: Any) -> str:
    return _compact_spaces(value) if isinstance(value, str) and value.strip() else ""


def _string_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _string_value(item)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _record(value: Any) -> dict[str, Any]:
    return value if _is_record(value) else {}


def _bullet_block(title: str, values: list[str]) -> str:
    if not values:
        return ""
    lines = "\n".join(f"- {value}" for value in values)
    return f"{title}:\n{lines}"


def pet_voice_profile(
    character_bible: Any,
    *,
    stage: str | None = None,
) -> VoiceProfile | None:
    bible = _record(character_bible)
    if not bible:
        return None

    voice = _record(bible.get("voice"))
    dialogue_style = _record(bible.get("dialogue_style"))
    lore = _record(bible.get("lore"))
    lore_voice = _record(lore.get("voice"))

    rules = _string_list(
        [
            *_string_list(voice.get("rules"), limit=MAX_VOICE_RULES),
            *_string_list(voice.get("voice_rules"), limit=MAX_VOICE_RULES),
            *_string_list(voice.get("speech_rules"), limit=MAX_VOICE_RULES),
            *_string_list(dialogue_style.get("voice_rules"), limit=MAX_VOICE_RULES),
            _string_value(lore_voice.get("speech_pattern")),
        ],
        limit=MAX_VOICE_RULES,
    )
    catchphrases = _string_list(
        [
            *_string_list(voice.get("catchphrases"), limit=MAX_CATCHPHRASES),
            *_string_list(lore_voice.get("favorite_phrases"), limit=MAX_CATCHPHRASES),
        ],
        limit=MAX_CATCHPHRASES,
    )
    sample_replies = _string_list(
        [
            *_string_list(voice.get("sample_replies"), limit=MAX_SAMPLE_REPLIES),
            *_string_list(dialogue_style.get("sample_replies"), limit=MAX_SAMPLE_REPLIES),
        ],
        limit=MAX_SAMPLE_REPLIES,
    )
    avoid_patterns = _string_list(
        [
            *_string_list(voice.get("avoid"), limit=MAX_AVOID_PATTERNS),
            *_string_list(voice.get("avoid_patterns"), limit=MAX_AVOID_PATTERNS),
            *_string_list(dialogue_style.get("avoid_patterns"), limit=MAX_AVOID_PATTERNS),
            *_string_list(lore_voice.get("avoid_saying"), limit=MAX_AVOID_PATTERNS),
        ],
        limit=MAX_AVOID_PATTERNS,
    )

    sentence_length = _string_value(voice.get("sentence_length")) or "короткие фразы"
    warmth = _string_value(voice.get("warmth")) or "естественная теплота"
    humor = _string_value(voice.get("humor")) or "по ситуации, без стендапа"
    directness = _string_value(voice.get("directness")) or "прямо и просто"
    if not any([rules, catchphrases, sample_replies, avoid_patterns]):
        return None
    return VoiceProfile(
        stage=stage,
        rules=tuple(rules),
        catchphrases=tuple(catchphrases),
        sample_replies=tuple(sample_replies),
        avoid_patterns=tuple(avoid_patterns),
        sentence_length=sentence_length,
        warmth=warmth,
        humor=humor,
        directness=directness,
    )


def pet_voice_prompt_block(
    character_bible: Any,
    *,
    stage: str | None = None,
    include_catchphrases: bool = True,
) -> str | None:
    profile = pet_voice_profile(character_bible, stage=stage)
    if not profile:
        return None

    blocks = [
        _bullet_block(
            "Параметры голоса",
            [
                f"длина: {profile.sentence_length}",
                f"теплота: {profile.warmth}",
                f"юмор: {profile.humor}",
                f"прямота: {profile.directness}",
            ],
        ),
        _bullet_block("Правила голоса", list(profile.rules)),
    ]
    if include_catchphrases:
        blocks.append(_bullet_block("Любимые короткие фразы", list(profile.catchphrases)))
    blocks.extend(
        [
            _bullet_block("Примеры ритма", list(profile.sample_replies)),
            _bullet_block("Не говорить так", list(profile.avoid_patterns)),
        ]
    )
    body = "\n\n".join(block for block in blocks if block)
    if not body:
        return None

    stage_line = f"Возрастная стадия сейчас: {profile.stage}." if profile.stage else ""
    return (
        "VOICE_CONTROL: это нижний регулятор всех видимых реплик питомца. "
        "Следуй ему строже, чем общим советам по стилю. "
        "Он меняет только форму речи: не меняй факты, смысл, выбранные story bricks "
        "и ответ на вопрос пользователя ради более яркой манеры. "
        "Не цитируй список механически; переноси манеру, ритм, запреты и характерные слова.\n"
        f"{stage_line}\n"
        f"{body}"
    ).strip()
