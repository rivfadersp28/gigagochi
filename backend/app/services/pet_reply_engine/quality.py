from __future__ import annotations

import re
from typing import Any

from app.services.pet_reply_engine.intent import (
    is_home_question,
    is_lore_question,
    is_preference_question,
    is_relationship_question,
)

WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
SENTENCE_END_PATTERN = re.compile(r"[.!?…]+")
LORE_KEYWORD_PATTERN = re.compile(r"[А-Яа-яЁёA-Za-z0-9]{5,}")
GENERIC_REPLY_PATTERN = re.compile(
    r"^\s*(?:"
    r"давай|я\s+(?:рядом|тут|слушаю)|что\s+делаем|окей|хорошо|"
    r"мне\s+нравится|рассказывай\s+дальше"
    r")[.!?…)]*\s*$",
    re.IGNORECASE,
)
UNSUPPORTED_PREFERENCE_PATTERN = re.compile(
    r"(?:люблю|нравится|окей).*(?:коротк\w*\s+просьб|сини\w*\s+лейк|утренн\w*\s+туман)",
    re.IGNORECASE,
)
LISTY_PREFERENCE_PATTERN = re.compile(
    r"(?:люблю|нравится)\s+[^.!?…]*(?:,\s*|\s+и\s+)[^.!?…]*(?:,\s*|\s+и\s+)",
    re.IGNORECASE,
)
CAUSE_WORD_PATTERN = re.compile(
    r"\b(?:потому\s+что|после|когда|из-за|поэтому|там|однажды|с\s+тех\s+пор)\b",
    re.IGNORECASE,
)
ASSISTANT_LEAK_PATTERN = re.compile(
    r"(?:\b(?:ии|ai)\b|ассистент|языковая\s+модель|как\s+бот|как\s+модель|"
    r"пользовател|система|prompt|промпт|api|чем\s+могу\s+помочь|как\s+я\s+могу\s+помочь)",
    re.IGNORECASE,
)
GENERIC_COMFORT_PATTERN = re.compile(
    r"(?:я\s+всегда\s+(?:рядом|с\s+тобой)|я\s+рядом,\s*если\s+что|"
    r"я\s+тут,\s*если\s+что|вс[её]\s+будет\s+хорошо|ты\s+не\s+один)",
    re.IGNORECASE,
)
EMPTY_MYSTICISM_PATTERN = re.compile(
    r"(?:искорк|сияни|внутри\s+меня\s+(?:светлее|теплее)|моя\s+душ[аеи])",
    re.IGNORECASE,
)
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")
LATIN_WORD_PATTERN = re.compile(r"\b[A-Za-z]{4,}\b")

QUALITY_PASSING_SCORE = 70


def _word_count(text: str) -> int:
    return len(WORD_PATTERN.findall(text))


def _sentence_count(text: str) -> int:
    return len(SENTENCE_END_PATTERN.findall(text))


def _collect_lore_text(lore: Any) -> str:
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    collect(lore)
    return " ".join(parts)


def _lore_keywords(lore: dict[str, Any] | None) -> set[str]:
    if not lore:
        return set()
    text = _collect_lore_text(lore)
    return {
        word.casefold()
        for word in LORE_KEYWORD_PATTERN.findall(text)
        if len(word) >= 5
    }


def _has_lore_overlap(reply: str, lore: dict[str, Any] | None) -> bool:
    keywords = _lore_keywords(lore)
    if not keywords:
        return True
    reply_words = {word.casefold() for word in LORE_KEYWORD_PATTERN.findall(reply)}
    return bool(keywords & reply_words)


def _axis_scores(
    *,
    question: str | None,
    reply: str,
    lore: dict[str, Any] | None,
    flags: list[str],
    validation_flags: tuple[str, ...],
) -> dict[str, int]:
    lore_question = is_lore_question(question)
    preference_question = is_preference_question(question)
    directness = 100
    if "generic_reply" in flags or "empty_reply" in flags:
        directness -= 45
    if lore_question and "too_short_for_lore" in flags:
        directness -= 25
    if preference_question and "list_like_preference_without_cause" in flags:
        directness -= 25
    if "unsupported_preference" in flags:
        directness -= 30
    if lore_question and "no_lore_anchor" in flags:
        directness -= 20

    naturalness_ru = 100
    if not CYRILLIC_PATTERN.search(reply):
        naturalness_ru -= 50
    if len(LATIN_WORD_PATTERN.findall(reply)) >= 2:
        naturalness_ru -= 25
    if any(flag in validation_flags for flag in ("markdown_or_list", "structured_text")):
        naturalness_ru -= 25

    voice_consistency = 85
    if "generic_reply" in flags or "no_generic_comfort" in flags:
        voice_consistency -= 30
    if "no_assistant_leak" in flags:
        voice_consistency -= 45
    if EMPTY_MYSTICISM_PATTERN.search(reply):
        voice_consistency -= 20

    lore_grounding = 100
    if lore_question and "no_lore_anchor" in flags:
        lore_grounding -= 45
    if lore_question and not lore:
        lore_grounding -= 20

    emotional_continuity = 90
    if any(flag.endswith("mood_mismatch") or flag == "validator:mood_mismatch" for flag in flags):
        emotional_continuity -= 45
    if "no_generic_comfort" in flags:
        emotional_continuity -= 15

    initiative_quality = 90
    if reply.count("?") > 1:
        initiative_quality -= 35
    if "boundary" in (question or "").casefold() and "?" in reply:
        initiative_quality -= 25

    memory_use = 90
    if any(word in reply.casefold() for word in ("memorycandidate", "память канона", "reflection")):
        memory_use -= 45

    no_assistant_leak = 0 if "no_assistant_leak" in flags else 100
    no_generic_comfort = 0 if "no_generic_comfort" in flags else 100

    return {
        "directness": max(0, directness),
        "naturalness_ru": max(0, naturalness_ru),
        "voice_consistency": max(0, voice_consistency),
        "lore_grounding": max(0, lore_grounding),
        "emotional_continuity": max(0, emotional_continuity),
        "initiative_quality": max(0, initiative_quality),
        "memory_use": max(0, memory_use),
        "no_assistant_leak": no_assistant_leak,
        "no_generic_comfort": no_generic_comfort,
    }


def quality_report_for_reply(
    *,
    question: str | None,
    reply: str,
    lore: dict[str, Any] | None,
    used_fallback: bool,
    validation_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    flags: list[str] = []
    text = reply.strip()

    if not text:
        flags.append("empty_reply")
    if used_fallback:
        flags.append("used_fallback")
    flags.extend(f"validator:{flag}" for flag in validation_flags)

    if GENERIC_REPLY_PATTERN.search(text):
        flags.append("generic_reply")
    if ASSISTANT_LEAK_PATTERN.search(text):
        flags.append("no_assistant_leak")
    if GENERIC_COMFORT_PATTERN.search(text):
        flags.append("no_generic_comfort")
    if EMPTY_MYSTICISM_PATTERN.search(text):
        flags.append("empty_mysticism")
    if UNSUPPORTED_PREFERENCE_PATTERN.search(text):
        flags.append("unsupported_preference")
    if is_preference_question(question) and LISTY_PREFERENCE_PATTERN.search(text):
        if not CAUSE_WORD_PATTERN.search(text):
            flags.append("list_like_preference_without_cause")
    if is_lore_question(question) and _word_count(text) < 7:
        flags.append("too_short_for_lore")
    if is_lore_question(question) and not _has_lore_overlap(text, lore):
        flags.append("no_lore_anchor")
    place_or_relationship = is_home_question(question) or is_relationship_question(question)
    if place_or_relationship and _sentence_count(text) < 1:
        flags.append("fragment_answer")

    score = 100
    deductions = {
        "empty_reply": 100,
        "used_fallback": 20,
        "generic_reply": 35,
        "unsupported_preference": 45,
        "list_like_preference_without_cause": 30,
        "too_short_for_lore": 20,
        "no_lore_anchor": 30,
        "fragment_answer": 15,
        "no_assistant_leak": 80,
        "no_generic_comfort": 35,
        "empty_mysticism": 35,
    }
    for flag in flags:
        score -= deductions.get(flag, 10 if flag.startswith("validator:") else 0)
    score = max(0, score)

    axes = _axis_scores(
        question=question,
        reply=text,
        lore=lore,
        flags=flags,
        validation_flags=validation_flags,
    )

    return {
        "score": score,
        "passed": score >= QUALITY_PASSING_SCORE,
        "flags": flags,
        "axes": axes,
    }
