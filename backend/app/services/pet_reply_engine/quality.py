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
    }
    for flag in flags:
        score -= deductions.get(flag, 10 if flag.startswith("validator:") else 0)
    score = max(0, score)

    return {
        "score": score,
        "passed": score >= QUALITY_PASSING_SCORE,
        "flags": flags,
    }
